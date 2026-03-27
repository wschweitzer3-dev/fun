from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class AppSettings:
    app_name: str = os.getenv("APP_NAME", "BCBS Care Intelligence")
    app_env: str = os.getenv("APP_ENV", "dev")
    api_prefix: str = os.getenv("API_PREFIX", "/api")

    supervisor_name: str = os.getenv("SUPERVISOR_NAME", "HLS_Payer_Supervisor")
    supervisor_endpoint_name: str | None = os.getenv("SUPERVISOR_ENDPOINT_NAME")
    supervisor_endpoint_cache_ttl_s: int = int(os.getenv("SUPERVISOR_ENDPOINT_CACHE_TTL_S", "300"))

    genie_space_title: str = os.getenv("GENIE_SPACE_TITLE", "HLS Payer Structured Genie")
    genie_space_id: str | None = os.getenv("GENIE_SPACE_ID")

    request_timeout_s: int = int(os.getenv("REQUEST_TIMEOUT_S", "120"))
    enable_mock_fallback: bool = os.getenv("ENABLE_MOCK_FALLBACK", "true").lower() == "true"


settings = AppSettings()

