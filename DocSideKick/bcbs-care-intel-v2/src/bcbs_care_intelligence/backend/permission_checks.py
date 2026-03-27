from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any

import requests
from databricks.sdk import WorkspaceClient

from .config import AppSettings
from .models import HealthDiagnostics, PermissionCheck


class BootstrapPermissionValidator:
    def __init__(self, settings: AppSettings):
        self.settings = settings
        self.workspace = WorkspaceClient()
        self._lock = threading.Lock()
        self._cached: HealthDiagnostics | None = None
        self._last_run: float = 0.0

    def _base_url(self) -> str:
        host = (self.workspace.config.host or "").strip().rstrip("/")
        if host.startswith("https://") or host.startswith("http://"):
            return host
        return f"https://{host}"

    def _headers(self) -> dict[str, str]:
        headers = self.workspace.config.authenticate()
        headers["Content-Type"] = "application/json"
        return headers

    def _check_supervisor_can_query(self) -> PermissionCheck:
        try:
            resp = requests.post(
                f"{self._base_url()}/serving-endpoints/{self.settings.supervisor_endpoint_name}/invocations",
                headers=self._headers(),
                json={"input": [{"role": "user", "content": "health check"}]},
                timeout=min(self.settings.request_timeout_s, 15),
            )
            ok = resp.status_code < 400
            return PermissionCheck(
                name="CAN_QUERY supervisor endpoint",
                ok=ok,
                detail="Supervisor endpoint invocation succeeded" if ok else f"{resp.status_code}: {resp.text[:240]}",
                remediation=(
                    f"Grant app SP CAN_QUERY on serving endpoint {self.settings.supervisor_endpoint_name}."
                    if not ok
                    else None
                ),
            )
        except Exception as exc:  # noqa: BLE001
            return PermissionCheck(
                name="CAN_QUERY supervisor endpoint",
                ok=False,
                detail=str(exc),
                remediation=f"Grant app SP CAN_QUERY on {self.settings.supervisor_endpoint_name}.",
            )

    def _check_genie_can_run(self) -> PermissionCheck:
        try:
            resp = requests.get(f"{self._base_url()}/api/2.0/data-rooms", headers=self._headers(), timeout=20)
            if resp.status_code >= 400:
                return PermissionCheck(
                    name="CAN_RUN genie space",
                    ok=False,
                    detail=f"{resp.status_code}: {resp.text[:240]}",
                    remediation=f"Grant app SP CAN_RUN on Genie space {self.settings.genie_space_title}.",
                )
            payload = resp.json() if resp.text else {}
            has_space = any(
                room.get("space_id") == self.settings.genie_space_id
                or room.get("display_name") == self.settings.genie_space_title
                for room in payload.get("data_rooms", [])
            )
            return PermissionCheck(
                name="CAN_RUN genie space",
                ok=has_space,
                detail=f"Visible Genie spaces include target={has_space}",
                remediation=(
                    f"Grant app SP CAN_RUN on Genie space {self.settings.genie_space_title} ({self.settings.genie_space_id})."
                    if not has_space
                    else None
                ),
            )
        except Exception as exc:  # noqa: BLE001
            return PermissionCheck(
                name="CAN_RUN genie space",
                ok=False,
                detail=str(exc),
                remediation=f"Grant app SP CAN_RUN on Genie space {self.settings.genie_space_title}.",
            )

    def _check_warehouse_can_use(self) -> PermissionCheck:
        try:
            resp = requests.get(
                f"{self._base_url()}/api/2.0/sql/warehouses/{self.settings.warehouse_id}",
                headers=self._headers(),
                timeout=20,
            )
            ok = resp.status_code < 400
            return PermissionCheck(
                name="CAN_USE SQL warehouse",
                ok=ok,
                detail="Warehouse metadata access succeeded" if ok else f"{resp.status_code}: {resp.text[:240]}",
                remediation=(
                    f"Grant app SP CAN_USE on warehouse {self.settings.warehouse_id}."
                    if not ok
                    else None
                ),
            )
        except Exception as exc:  # noqa: BLE001
            return PermissionCheck(
                name="CAN_USE SQL warehouse",
                ok=False,
                detail=str(exc),
                remediation=f"Grant app SP CAN_USE on warehouse {self.settings.warehouse_id}.",
            )

    def _execute_sql_statement(self, statement: str) -> tuple[bool, str]:
        create = requests.post(
            f"{self._base_url()}/api/2.0/sql/statements",
            headers=self._headers(),
            json={
                "warehouse_id": self.settings.warehouse_id,
                "statement": statement,
                "wait_timeout": "8s",
                "on_wait_timeout": "CONTINUE",
            },
            timeout=20,
        )
        if create.status_code >= 400:
            return False, f"{create.status_code}: {create.text[:240]}"

        payload = create.json() if create.text else {}
        statement_id = payload.get("statement_id")
        if not statement_id:
            return False, "No statement_id returned"

        deadline = time.time() + self.settings.health_check_sql_timeout_s
        while time.time() < deadline:
            status_resp = requests.get(
                f"{self._base_url()}/api/2.0/sql/statements/{statement_id}",
                headers=self._headers(),
                timeout=20,
            )
            if status_resp.status_code >= 400:
                return False, f"{status_resp.status_code}: {status_resp.text[:240]}"
            status_payload = status_resp.json() if status_resp.text else {}
            state = (
                status_payload.get("status", {}).get("state")
                or status_payload.get("status", {}).get("state", "UNKNOWN")
            )
            if state == "SUCCEEDED":
                return True, "Statement succeeded"
            if state in {"FAILED", "CANCELED", "CLOSED"}:
                error_text = status_payload.get("status", {}).get("error", {}).get("message") or "Statement failed"
                return False, str(error_text)[:240]
            time.sleep(self.settings.health_check_poll_s)
        return False, f"Timed out after {self.settings.health_check_sql_timeout_s}s"

    def _check_catalog_schema_visibility(self) -> PermissionCheck:
        query = f"SHOW TABLES IN {self.settings.schema_full_name}"
        ok, detail = self._execute_sql_statement(query)
        return PermissionCheck(
            name="USE_CATALOG + USE_SCHEMA",
            ok=ok,
            detail=detail,
            remediation=(
                f"Grant USE_CATALOG on {self.settings.catalog} and USE_SCHEMA on {self.settings.schema_full_name}."
                if not ok
                else None
            ),
        )

    def _check_table_select(self, table_name: str) -> PermissionCheck:
        fq_table = f"{self.settings.schema_full_name}.{table_name}"
        ok, detail = self._execute_sql_statement(f"SELECT 1 FROM {fq_table} LIMIT 1")
        return PermissionCheck(
            name=f"SELECT {fq_table}",
            ok=ok,
            detail=detail,
            remediation=f"Grant SELECT on {fq_table}." if not ok else None,
        )

    def run_checks(self, force: bool = False) -> HealthDiagnostics:
        with self._lock:
            if (
                not force
                and self._cached is not None
                and (time.time() - self._last_run) < self.settings.health_check_ttl_s
            ):
                return self._cached

            checks = [
                self._check_supervisor_can_query(),
                self._check_genie_can_run(),
                self._check_warehouse_can_use(),
                self._check_catalog_schema_visibility(),
            ]
            checks.extend([self._check_table_select(table_name) for table_name in self.settings.required_tables])

            overall_ok = all(check.ok for check in checks)
            diagnostics = HealthDiagnostics(
                status="ok" if overall_ok else "degraded",
                app_env=self.settings.app_env,
                checks_ran_at=datetime.now(timezone.utc).isoformat(),
                checks=checks,
            )
            self._cached = diagnostics
            self._last_run = time.time()
            return diagnostics

