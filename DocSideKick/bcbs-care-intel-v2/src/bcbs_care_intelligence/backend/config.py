from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class AppSettings:
    app_name: str = os.getenv("APP_NAME", "BCBS Care Intelligence")
    app_env: str = os.getenv("APP_ENV", "prod")
    api_prefix: str = os.getenv("API_PREFIX", "/api")

    supervisor_name: str = os.getenv("SUPERVISOR_NAME", "HLS_Payer_Supervisor")
    supervisor_endpoint_name: str = os.getenv("SUPERVISOR_ENDPOINT_NAME", "mas-30532bb0-endpoint")
    knowledge_endpoint_name: str = os.getenv("KNOWLEDGE_ENDPOINT_NAME", "ka-c7630b94-endpoint")
    supervisor_endpoint_cache_ttl_s: int = int(os.getenv("SUPERVISOR_ENDPOINT_CACHE_TTL_S", "300"))
    supervisor_connect_timeout_s: int = int(os.getenv("SUPERVISOR_CONNECT_TIMEOUT_S", "10"))
    supervisor_read_timeout_s: int = int(os.getenv("SUPERVISOR_READ_TIMEOUT_S", "90"))
    supervisor_retry_count: int = int(os.getenv("SUPERVISOR_RETRY_COUNT", "2"))
    supervisor_retry_backoff_s: float = float(os.getenv("SUPERVISOR_RETRY_BACKOFF_S", "2.0"))
    supervisor_max_output_tokens: int = int(os.getenv("SUPERVISOR_MAX_OUTPUT_TOKENS", "900"))

    genie_space_title: str = os.getenv("GENIE_SPACE_TITLE", "HLS Payer Structured Genie")
    genie_space_id: str = os.getenv("GENIE_SPACE_ID", "01f126e12bda187fb0fa9f49d1c6e585")

    warehouse_id: str = os.getenv("WAREHOUSE_ID", "1e1f63a1a14d1f34")
    catalog: str = os.getenv("UC_CATALOG", "main")
    schema: str = os.getenv("UC_SCHEMA", "hls_payer_demo")
    table_names_csv: str = os.getenv("UC_TABLES", "members,claims,labs,gaps_in_care")

    request_timeout_s: int = int(os.getenv("REQUEST_TIMEOUT_S", "90"))
    response_cache_ttl_s: int = int(os.getenv("RESPONSE_CACHE_TTL_S", "60"))
    soft_latency_warn_ms: int = int(os.getenv("SOFT_LATENCY_WARN_MS", "10000"))
    health_check_ttl_s: int = int(os.getenv("HEALTH_CHECK_TTL_S", "300"))
    health_check_sql_timeout_s: int = int(os.getenv("HEALTH_CHECK_SQL_TIMEOUT_S", "30"))
    health_check_poll_s: float = float(os.getenv("HEALTH_CHECK_POLL_S", "1.5"))
    enable_mock_fallback: bool = os.getenv("ENABLE_MOCK_FALLBACK", "false").lower() == "true"

    @property
    def required_tables(self) -> list[str]:
        return [name.strip() for name in self.table_names_csv.split(",") if name.strip()]

    @property
    def schema_full_name(self) -> str:
        return f"{self.catalog}.{self.schema}"


settings = AppSettings()
