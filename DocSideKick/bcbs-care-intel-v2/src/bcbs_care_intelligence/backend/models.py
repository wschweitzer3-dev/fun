from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ChartType(str, Enum):
    BAR = "bar"
    LINE = "line"
    PIE = "pie"


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class ChartSpec(BaseModel):
    type: ChartType
    x: str
    y: str
    data: list[dict[str, Any]]
    title: str | None = None


class DebugInfo(BaseModel):
    supervisor_endpoint: str | None = None
    request_id: str | None = None
    latency_ms: int | None = None
    path: str | None = None
    tool_call_count: int | None = None
    latency_warning: str | None = None
    error: str | None = None
    fallback_used: bool = False


class ChatResponse(BaseModel):
    response_text: str
    data_table: list[dict[str, Any]] | None = None
    chart_spec: ChartSpec | None = None
    sources: list[str] | None = None
    debug: DebugInfo | None = None


class InsightCard(BaseModel):
    title: str
    value: str
    detail: str


class InsightsSnapshot(BaseModel):
    title: str
    summary: str
    cards: list[InsightCard]
    chart_specs: list[ChartSpec]
    data_table: list[dict[str, Any]]


class PermissionCheck(BaseModel):
    name: str
    ok: bool
    detail: str
    remediation: str | None = None


class HealthDiagnostics(BaseModel):
    status: str
    app_env: str
    checks_ran_at: str | None = None
    checks: list[PermissionCheck] = Field(default_factory=list)
