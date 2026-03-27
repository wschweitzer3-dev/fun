from __future__ import annotations

import asyncio
import time
from typing import Any

import requests
from databricks.sdk import WorkspaceClient

from .config import AppSettings

SYSTEM_PROMPT = (
    "You are BCBS Care Intelligence, a healthcare analytics copilot for payer data. "
    "Do not mention being a supervisor, orchestrator, or internal agent. "
    "Answer directly and concisely with practical detail. "
    "If asked to explain the dataset, provide a useful overview of structured tables "
    "(members, claims, labs, gaps_in_care) and unstructured sources "
    "(clinical notes, care manager notes, policy/guideline docs), including what each contains."
)


class DatabricksSupervisorClient:
    def __init__(self, app_settings: AppSettings):
        self.settings = app_settings
        self.workspace = WorkspaceClient()
        self._cached_endpoint: str | None = None
        self._cache_deadline: float = 0.0

    def _base_url(self) -> str:
        host = (self.workspace.config.host or "").strip().rstrip("/")
        if host.startswith("http://") or host.startswith("https://"):
            return host
        return f"https://{host}"

    def _headers(self) -> dict[str, str]:
        headers = self.workspace.config.authenticate()
        headers["Content-Type"] = "application/json"
        return headers

    def _resolve_endpoint_name_sync(self) -> str:
        if self.settings.supervisor_endpoint_name:
            return self.settings.supervisor_endpoint_name

        if self._cached_endpoint and time.time() < self._cache_deadline:
            return self._cached_endpoint

        endpoint_name: str | None = None
        tile_error: str | None = None
        try:
            resp = requests.get(
                f"{self._base_url()}/api/2.0/tiles",
                headers=self._headers(),
                timeout=30,
            )
            if resp.status_code >= 400:
                tile_error = f"{resp.status_code} {resp.text}"
            else:
                payload = resp.json() if resp.text else {}
                for tile in payload.get("tiles", []):
                    if tile.get("name") == self.settings.supervisor_name and tile.get("tile_type") == "MAS":
                        tile_id = tile.get("tile_id")
                        if isinstance(tile_id, str) and tile_id:
                            endpoint_name = f"mas-{tile_id.split('-')[0]}-endpoint"
                            break
        except Exception as exc:  # noqa: BLE001
            tile_error = str(exc)

        if not endpoint_name:
            mas_endpoint_names = []
            for endpoint in self.workspace.serving_endpoints.list():
                name = getattr(endpoint, "name", None)
                if isinstance(name, str) and name.startswith("mas-") and name.endswith("-endpoint"):
                    mas_endpoint_names.append(name)

            if len(mas_endpoint_names) == 1:
                endpoint_name = mas_endpoint_names[0]
            else:
                detail = f" Tile discovery error: {tile_error}." if tile_error else ""
                raise RuntimeError(
                    f"Unable to resolve MAS endpoint for supervisor '{self.settings.supervisor_name}'."
                    f"{detail} Set SUPERVISOR_ENDPOINT_NAME explicitly."
                )

        self._cached_endpoint = endpoint_name
        self._cache_deadline = time.time() + self.settings.supervisor_endpoint_cache_ttl_s
        return endpoint_name

    def _invoke_sync(self, message: str) -> dict[str, Any]:
        endpoint_name = self._resolve_endpoint_name_sync()
        resp = requests.post(
            f"{self._base_url()}/serving-endpoints/{endpoint_name}/invocations",
            headers=self._headers(),
            json={
                "input": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": message},
                ]
            },
            timeout=self.settings.request_timeout_s,
        )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"Supervisor invocation failed for endpoint {endpoint_name}: {resp.status_code} {resp.text}"
            )
        return {"endpoint_name": endpoint_name, "payload": resp.json() if resp.text else {}}

    async def invoke(self, message: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._invoke_sync, message)
