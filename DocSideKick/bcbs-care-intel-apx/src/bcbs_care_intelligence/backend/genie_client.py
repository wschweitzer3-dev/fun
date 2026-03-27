from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import requests
from databricks.sdk import WorkspaceClient

from .config import AppSettings


def _to_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if hasattr(value, "as_dict"):
        return value.as_dict()
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return {"value": str(value)}


def _extract_first(payload: Any, candidate_keys: set[str]) -> str | None:
    if isinstance(payload, dict):
        for key, val in payload.items():
            if key.lower() in candidate_keys and isinstance(val, str) and val.strip():
                return val
            found = _extract_first(val, candidate_keys)
            if found:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = _extract_first(item, candidate_keys)
            if found:
                return found
    return None


def _extract_rows(payload: Any) -> list[dict[str, Any]] | None:
    if isinstance(payload, list):
        if payload and all(isinstance(item, dict) for item in payload):
            return payload
        for item in payload:
            rows = _extract_rows(item)
            if rows:
                return rows
    elif isinstance(payload, dict):
        for key, value in payload.items():
            lower = key.lower()
            if lower in {"data", "rows", "data_table", "table"} and isinstance(value, list):
                if value and all(isinstance(item, dict) for item in value):
                    return value
            rows = _extract_rows(value)
            if rows:
                return rows
    return None


class DatabricksGenieClient:
    def __init__(self, app_settings: AppSettings):
        self.settings = app_settings
        self.workspace = WorkspaceClient()
        self._cached_space_id: str | None = app_settings.genie_space_id

    def _base_url(self) -> str:
        host = (self.workspace.config.host or "").strip().rstrip("/")
        if host.startswith("http://") or host.startswith("https://"):
            return host
        return f"https://{host}"

    def _headers(self) -> dict[str, str]:
        headers = self.workspace.config.authenticate()
        headers["Content-Type"] = "application/json"
        return headers

    def _resolve_space_id_sync(self) -> str:
        if self._cached_space_id:
            return self._cached_space_id

        resp = requests.get(
            f"{self._base_url()}/api/2.0/data-rooms",
            headers=self._headers(),
            timeout=30,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"Failed to list Genie spaces: {resp.status_code} {resp.text}")

        payload = resp.json() if resp.text else {}
        for room in payload.get("data_rooms", []):
            if room.get("display_name") == self.settings.genie_space_title:
                self._cached_space_id = room.get("space_id")
                return self._cached_space_id

        raise RuntimeError(
            f"Unable to find Genie space '{self.settings.genie_space_title}'. Set GENIE_SPACE_ID explicitly."
        )

    def _ask_sync(self, message: str) -> dict[str, Any]:
        space_id = self._resolve_space_id_sync()
        response = self.workspace.genie.start_conversation_and_wait(
            space_id=space_id,
            content=message,
            wait_timeout=f"{self.settings.request_timeout_s}s",
        )
        payload = _to_dict(response)

        return {
            "space_id": space_id,
            "response_text": _extract_first(payload, {"text_response", "response_text", "content"}),
            "sql_text": _extract_first(payload, {"sql", "sql_query", "query"}),
            "rows": _extract_rows(payload),
            "raw": payload,
            "queried_at_utc": datetime.utcnow().isoformat(),
        }

    async def ask(self, message: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._ask_sync, message)

