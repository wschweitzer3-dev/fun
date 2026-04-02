from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ChartType(str, Enum):
    BAR = "bar"
    LINE = "line"
    PIE = "pie"


class KpiFormat(str, Enum):
    NUMBER = "number"
    CURRENCY = "currency"
    PERCENT = "percent"


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class ChartSpec(BaseModel):
    type: ChartType
    x: str
    y: str
    data: list[dict[str, Any]]
    title: str | None = None


class KpiTile(BaseModel):
    label: str
    value: float
    format: KpiFormat = KpiFormat.NUMBER
    decimals: int = 0
    suffix: str | None = None


class DebugInfo(BaseModel):
    supervisor_endpoint: str
    latency_ms: int
    approval_hops: int = 0
    total_round_trips: int = 1
    external_trend_status: str = "not_used"
    auth_mode: str | None = None
    auth_fallback_used: bool = False
    auth_fallback_error: str | None = None
    error: str | None = None
    fallback_used: bool = False


class ChatResponse(BaseModel):
    response_text: str
    data_table: list[dict[str, Any]] = Field(default_factory=list)
    chart_spec: ChartSpec | None = None
    kpis: list[KpiTile] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    notices: list[str] = Field(default_factory=list)
    debug: DebugInfo


class HealthResponse(BaseModel):
    status: str
    app_name: str
    app_env: str
    supervisor_endpoint: str
    supervisor_state: str | None = None
    endpoint_ready: bool = False
    error: str | None = None
