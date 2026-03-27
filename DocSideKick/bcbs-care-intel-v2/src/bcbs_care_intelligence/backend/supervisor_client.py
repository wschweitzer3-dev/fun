from __future__ import annotations

import asyncio
import time
from typing import Any

import requests
from databricks.sdk import WorkspaceClient

from .config import AppSettings

SYSTEM_PROMPT = (
    "You are BCBS Care Intelligence, a healthcare analytics copilot for payer data. "
    "Never describe internal orchestration, routing, or tool mechanics. "
    "For barrier, outreach, guideline, or narrative questions, use unstructured_care_insights and ground answers in retrieved evidence. "
    "For cohort, cost, risk, trend, and gap queries, use structured_payer_analytics. "
    "For blended prompts, combine both tools with at most two calls and return one unified answer with sections: Cohort, Barriers, Actions. "
    "Do not provide generic hypothetical barrier lists. If evidence is unavailable, explicitly say which tool/query failed. "
    "Keep answers concise and focused for fast response times."
)


class DatabricksSupervisorClient:
    def __init__(self, settings: AppSettings):
        self.settings = settings
        self.workspace = WorkspaceClient()
        self._cached_endpoint: str | None = None
        self._cache_deadline: float = 0.0

    def _base_url(self) -> str:
        host = (self.workspace.config.host or "").strip().rstrip("/")
        if host.startswith("https://") or host.startswith("http://"):
            return host
        return f"https://{host}"

    def _headers(self) -> dict[str, str]:
        headers = self.workspace.config.authenticate()
        headers["Content-Type"] = "application/json"
        return headers

    def resolve_endpoint_name(self) -> str:
        if self.settings.supervisor_endpoint_name:
            return self.settings.supervisor_endpoint_name
        if self._cached_endpoint and time.time() < self._cache_deadline:
            return self._cached_endpoint

        resp = requests.get(f"{self._base_url()}/api/2.0/tiles", headers=self._headers(), timeout=20)
        if resp.status_code >= 400:
            raise RuntimeError(f"Unable to list supervisor tiles: {resp.status_code} {resp.text}")

        payload = resp.json() if resp.text else {}
        for tile in payload.get("tiles", []):
            if tile.get("name") == self.settings.supervisor_name and tile.get("tile_type") == "MAS":
                tile_id = tile.get("tile_id")
                if isinstance(tile_id, str) and tile_id:
                    endpoint = f"mas-{tile_id.split('-')[0]}-endpoint"
                    self._cached_endpoint = endpoint
                    self._cache_deadline = time.time() + self.settings.supervisor_endpoint_cache_ttl_s
                    return endpoint

        raise RuntimeError(
            f"Supervisor tile '{self.settings.supervisor_name}' not found. Set SUPERVISOR_ENDPOINT_NAME explicitly."
        )

    def _invoke_endpoint_sync(
        self,
        endpoint_name: str,
        message: str,
        include_system_prompt: bool,
    ) -> dict[str, Any]:
        start = time.perf_counter()
        messages = [{"role": "user", "content": message}]
        if include_system_prompt:
            messages.insert(0, {"role": "system", "content": SYSTEM_PROMPT})
        payload = {
            "input": messages,
            "max_output_tokens": self.settings.supervisor_max_output_tokens,
        }
        last_error: Exception | None = None
        response: requests.Response | None = None
        request_id: str | None = None

        for attempt in range(1, self.settings.supervisor_retry_count + 1):
            try:
                response = requests.post(
                    f"{self._base_url()}/serving-endpoints/{endpoint_name}/invocations",
                    headers=self._headers(),
                    json=payload,
                    timeout=(
                        self.settings.supervisor_connect_timeout_s,
                        self.settings.supervisor_read_timeout_s,
                    ),
                )
                request_id = response.headers.get("x-request-id")
                if response.status_code >= 400:
                    raise RuntimeError(f"Endpoint call failed [{response.status_code}] {response.text[:1000]}")
                break
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt == self.settings.supervisor_retry_count:
                    raise RuntimeError(
                        f"Endpoint {endpoint_name} timed out/failed after {attempt} attempts: {exc}"
                    ) from exc
                time.sleep(self.settings.supervisor_retry_backoff_s * attempt)

        latency_ms = int((time.perf_counter() - start) * 1000)
        if response is None:
            raise RuntimeError(f"Endpoint {endpoint_name} call failed with no response: {last_error}")

        if response.status_code >= 400:
            raise RuntimeError(
                f"Endpoint call failed [{response.status_code}] {response.text[:1000]}"
            )

        payload = response.json() if response.text else {}
        return {
            "endpoint_name": endpoint_name,
            "payload": payload,
            "latency_ms": latency_ms,
            "request_id": request_id,
        }

    def invoke_sync(self, message: str) -> dict[str, Any]:
        endpoint_name = self.resolve_endpoint_name()
        return self._invoke_endpoint_sync(endpoint_name, message, include_system_prompt=True)

    def invoke_knowledge_sync(self, message: str) -> dict[str, Any]:
        prompt = (
            "Answer strictly from unstructured care notes and guideline documents. "
            "Include doc_id citations for evidence."
        )
        return self._invoke_endpoint_sync(
            self.settings.knowledge_endpoint_name,
            f"{prompt}\n\nQuestion: {message}",
            include_system_prompt=False,
        )

    async def invoke(self, message: str) -> dict[str, Any]:
        return await asyncio.to_thread(self.invoke_sync, message)

    async def invoke_knowledge(self, message: str) -> dict[str, Any]:
        return await asyncio.to_thread(self.invoke_knowledge_sync, message)

    def warmup_sync(self) -> None:
        try:
            self.invoke_sync("Reply with exactly one word: READY")
        except Exception:
            # Warm-up is best effort and should not fail app startup.
            return
