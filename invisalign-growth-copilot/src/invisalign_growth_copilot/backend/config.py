from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class AppSettings:
    app_name: str = os.getenv("APP_NAME", "Invisalign Growth Copilot")
    app_env: str = os.getenv("APP_ENV", "dev")
    api_prefix: str = os.getenv("API_PREFIX", "/api")

    supervisor_name: str = os.getenv("SUPERVISOR_NAME", "Invisalign_MAS")
    supervisor_endpoint_name: str = os.getenv("SUPERVISOR_ENDPOINT_NAME", "mas-47342197-endpoint")

    request_timeout_s: int = int(os.getenv("REQUEST_TIMEOUT_S", "180"))
    max_auto_approval_round_trips: int = int(os.getenv("MAX_AUTO_APPROVAL_ROUND_TRIPS", "4"))
    auto_approve_mcp_servers_csv: str = os.getenv("AUTO_APPROVE_MCP_SERVERS", "will_you")
    api_health_timeout_s: int = int(os.getenv("API_HEALTH_TIMEOUT_S", "15"))

    @property
    def auto_approve_mcp_servers(self) -> set[str]:
        parts = [part.strip() for part in self.auto_approve_mcp_servers_csv.split(",")]
        return {part for part in parts if part}


settings = AppSettings()
