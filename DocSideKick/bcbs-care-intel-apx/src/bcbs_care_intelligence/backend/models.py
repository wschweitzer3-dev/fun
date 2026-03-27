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
    genie_space_id: str | None = None
    sql_text: str | None = None
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

