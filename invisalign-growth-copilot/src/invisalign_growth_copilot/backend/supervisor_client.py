from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

import requests
from databricks.sdk import WorkspaceClient

from .config import AppSettings

SYSTEM_PROMPT = (
    "You are the Invisalign Provider Growth Copilot for sales teams. "
    "Respond with direct, practical guidance that can be used in account planning. "
    "Prioritize quantified recommendations, concise reasoning, and execution-ready language. "
    "When external trend context is available, cite it clearly."
)


class DatabricksSupervisorClient:
    def __init__(self, app_settings: AppSettings):
        self.settings = app_settings
        self.workspace = WorkspaceClient()

    def _base_url(self) -> str:
        host = (self.workspace.config.host or "").strip().rstrip("/")
        if host.startswith("http://") or host.startswith("https://"):
            return host
        return f"https://{host}"

    def _headers(self, access_token: str | None = None) -> dict[str, str]:
        if access_token:
            return {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            }
        headers = self.workspace.config.authenticate()
        headers["Content-Type"] = "application/json"
        return headers

    def _invoke_once(self, history: list[dict[str, Any]], access_token: str | None = None) -> dict[str, Any]:
        endpoint_name = self.settings.supervisor_endpoint_name
        response = requests.post(
            f"{self._base_url()}/serving-endpoints/{endpoint_name}/invocations",
            headers=self._headers(access_token),
            json={"input": history},
            timeout=self.settings.request_timeout_s,
        )
        if response.status_code >= 400:
            raise RuntimeError(
                f"Supervisor invocation failed ({response.status_code}): {response.text}"
            )
        return response.json() if response.text else {}

    @staticmethod
    def _approval_request_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
        output = payload.get("output", [])
        if not isinstance(output, list):
            return []
        requests_out: list[dict[str, Any]] = []
        for item in output:
            if isinstance(item, dict) and item.get("type") == "mcp_approval_request" and isinstance(item.get("id"), str):
                requests_out.append(item)
        return requests_out

    @staticmethod
    def _has_tool_error(payload: dict[str, Any], tool_name: str) -> bool:
        output = payload.get("output", [])
        if not isinstance(output, list):
            return False
        for item in output:
            if not isinstance(item, dict):
                continue
            if item.get("type") != "function_call_output" or item.get("name") != tool_name:
                continue
            tool_output = item.get("output")
            if isinstance(tool_output, str) and tool_output.strip().lower().startswith("error"):
                return True
        return False

    @staticmethod
    def _has_tool_success(payload: dict[str, Any], tool_name: str) -> bool:
        output = payload.get("output", [])
        if not isinstance(output, list):
            return False
        for item in output:
            if not isinstance(item, dict):
                continue
            if item.get("type") != "function_call_output" or item.get("name") != tool_name:
                continue
            tool_output = item.get("output")
            if isinstance(tool_output, str) and tool_output.strip() and not tool_output.strip().lower().startswith("error"):
                return True
        return False

    def _invoke_with_auto_approval_sync(self, message: str, access_token: str | None = None) -> dict[str, Any]:
        history: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": message},
        ]

        approval_hops = 0
        round_trips = 0
        external_tool_seen = False
        external_tool_failed = False
        external_tool_succeeded = False

        started = time.perf_counter()
        final_payload: dict[str, Any] = {}

        pending_approval_after_limit = False
        for round_idx in range(self.settings.max_auto_approval_round_trips + 1):
            round_trips += 1
            final_payload = self._invoke_once(history, access_token)
            output = final_payload.get("output", [])
            if isinstance(output, list):
                history.extend(output)

            if self._has_tool_error(final_payload, "you-search"):
                external_tool_seen = True
                external_tool_failed = True
            if self._has_tool_success(final_payload, "you-search"):
                external_tool_seen = True
                external_tool_succeeded = True

            approval_requests = self._approval_request_items(final_payload)
            if not approval_requests:
                break

            if round_idx == self.settings.max_auto_approval_round_trips:
                pending_approval_after_limit = True
                break

            for approval_request in approval_requests:
                server_label = str(approval_request.get("server_label") or "").strip()
                can_auto_approve = (
                    not self.settings.auto_approve_mcp_servers
                    or server_label in self.settings.auto_approve_mcp_servers
                )
                if not can_auto_approve:
                    continue

                history.append(
                    {
                        "type": "mcp_approval_response",
                        "id": f"mcp_approved_{uuid.uuid4().hex[:12]}",
                        "approval_request_id": approval_request["id"],
                        "approve": True,
                    }
                )
                approval_hops += 1

        if pending_approval_after_limit:
            # Force a final assistant answer without additional tool usage if approval loops never settled.
            history.append(
                {
                    "role": "user",
                    "content": (
                        "External tool access could not complete. "
                        "Provide the best final answer now using internal context only."
                    ),
                }
            )
            round_trips += 1
            final_payload = self._invoke_once(history, access_token)
            output = final_payload.get("output", [])
            if isinstance(output, list):
                history.extend(output)

        latency_ms = int((time.perf_counter() - started) * 1000)
        external_trend_status = "not_used"
        if external_tool_seen and external_tool_succeeded:
            external_trend_status = "ok"
        elif external_tool_seen and external_tool_failed:
            external_trend_status = "unavailable"

        return {
            "endpoint_name": self.settings.supervisor_endpoint_name,
            "payload": final_payload,
            "history": history,
            "approval_hops": approval_hops,
            "round_trips": round_trips,
            "latency_ms": latency_ms,
            "external_trend_status": external_trend_status,
        }

    async def invoke(self, message: str, access_token: str | None = None) -> dict[str, Any]:
        if access_token:
            try:
                result = await asyncio.to_thread(self._invoke_with_auto_approval_sync, message, access_token)
                result["auth_mode"] = "user_token"
                result["auth_fallback_used"] = False
                result["auth_fallback_error"] = None
                return result
            except Exception as first_error:  # noqa: BLE001
                result = await asyncio.to_thread(self._invoke_with_auto_approval_sync, message, None)
                result["auth_mode"] = "app_identity"
                result["auth_fallback_used"] = True
                result["auth_fallback_error"] = str(first_error)
                return result

        result = await asyncio.to_thread(self._invoke_with_auto_approval_sync, message, None)
        result["auth_mode"] = "app_identity"
        result["auth_fallback_used"] = False
        result["auth_fallback_error"] = None
        return result

    async def health(self) -> tuple[str | None, bool, str | None]:
        def _health_sync() -> tuple[str | None, bool, str | None]:
            try:
                endpoint = self.workspace.serving_endpoints.get(self.settings.supervisor_endpoint_name)
                state = None
                if getattr(endpoint, "state", None):
                    ready_state = getattr(endpoint.state, "ready", None)
                    if ready_state is not None:
                        state = str(getattr(ready_state, "value", ready_state))
                endpoint_ready = state == "READY"
                return state, endpoint_ready, None
            except Exception as exc:  # noqa: BLE001
                return None, False, str(exc)

        return await asyncio.to_thread(_health_sync)
