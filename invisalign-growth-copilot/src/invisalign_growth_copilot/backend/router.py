from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Request

from .config import settings
from .models import (
    ChartSpec,
    ChartType,
    ChatRequest,
    ChatResponse,
    DebugInfo,
    HealthResponse,
    KpiFormat,
    KpiTile,
)
from .supervisor_client import DatabricksSupervisorClient

api = APIRouter(prefix=settings.api_prefix)
supervisor_client = DatabricksSupervisorClient(settings)

URL_PATTERN = re.compile(r"https?://[^\s)\]>\"']+")


def _is_numeric(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_iso_date(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def _extract_assistant_text(history: list[dict[str, Any]]) -> str:
    for item in reversed(history):
        if not isinstance(item, dict):
            continue
        if item.get("type") != "message" or item.get("role") != "assistant":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "output_text":
                text = block.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
        if parts:
            return "\n\n".join(parts)
    return "I could not extract a final answer from the MAS response."


def _extract_first_sql(payload: Any) -> str | None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key.lower() in {"sql", "query", "sql_text", "statement"} and isinstance(value, str):
                if value.strip():
                    return value.strip()
            found = _extract_first_sql(value)
            if found:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = _extract_first_sql(item)
            if found:
                return found
    return None


def _extract_table(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        if payload and all(isinstance(item, dict) for item in payload):
            return payload
        for item in payload:
            rows = _extract_table(item)
            if rows:
                return rows
        return []

    if isinstance(payload, dict):
        for key, value in payload.items():
            if key.lower() in {"rows", "data", "table", "data_table", "result"} and isinstance(value, list):
                if value and all(isinstance(item, dict) for item in value):
                    return value
            rows = _extract_table(value)
            if rows:
                return rows
    return []


def _extract_sources(payload: Any, response_text: str) -> list[str]:
    sources: list[str] = []

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            for key, value in item.items():
                key_lower = key.lower()
                if key_lower in {"source", "sources", "citation", "citations", "url", "doc_id"}:
                    if isinstance(value, str) and value.strip():
                        sources.append(value.strip())
                    elif isinstance(value, list):
                        for entry in value:
                            if isinstance(entry, str) and entry.strip():
                                sources.append(entry.strip())
                            elif isinstance(entry, dict):
                                for field in ("url", "title", "doc_id", "source"):
                                    field_value = entry.get(field)
                                    if isinstance(field_value, str) and field_value.strip():
                                        sources.append(field_value.strip())
                visit(value)
        elif isinstance(item, list):
            for entry in item:
                visit(entry)

    visit(payload)
    sources.extend(URL_PATTERN.findall(response_text))

    deduped: list[str] = []
    seen = set()
    for source in sources:
        if source not in seen:
            deduped.append(source)
            seen.add(source)
    return deduped


def _infer_chart_spec(rows: list[dict[str, Any]], message: str) -> ChartSpec | None:
    if not rows:
        return None

    sample = rows[0]
    numeric_cols = [key for key, value in sample.items() if _is_numeric(value)]
    date_cols = [key for key, value in sample.items() if _is_iso_date(value)]
    category_cols = [key for key in sample.keys() if key not in numeric_cols and key not in date_cols]

    if not numeric_cols:
        return None

    y_col = numeric_cols[0]
    lowered_message = message.lower()

    if date_cols:
        return ChartSpec(
            type=ChartType.LINE,
            x=date_cols[0],
            y=y_col,
            data=rows,
            title="Trend Over Time",
        )

    if category_cols:
        x_col = category_cols[0]
        distinct_count = len({str(row.get(x_col, "")) for row in rows})
        should_use_pie = (
            distinct_count <= 6
            and any(token in lowered_message for token in ("share", "mix", "distribution", "composition"))
        )
        return ChartSpec(
            type=ChartType.PIE if should_use_pie else ChartType.BAR,
            x=x_col,
            y=y_col,
            data=rows,
            title="Composition" if should_use_pie else "Comparison",
        )

    return None


def _derive_kpis(rows: list[dict[str, Any]]) -> list[KpiTile]:
    if not rows:
        return []

    sample = rows[0]
    numeric_cols = [key for key, value in sample.items() if _is_numeric(value)]
    tiles: list[KpiTile] = [
        KpiTile(label="Rows Returned", value=float(len(rows)), format=KpiFormat.NUMBER, decimals=0)
    ]

    if not numeric_cols:
        return tiles

    primary_col = numeric_cols[0]
    values = [float(row.get(primary_col, 0.0)) for row in rows if _is_numeric(row.get(primary_col))]
    if values:
        total = sum(values)
        mean = total / len(values)
        tile_format = KpiFormat.CURRENCY if "revenue" in primary_col.lower() else KpiFormat.NUMBER
        decimals = 0 if tile_format != KpiFormat.PERCENT else 2
        tiles.append(KpiTile(label=f"Total {primary_col}", value=total, format=tile_format, decimals=decimals))
        tiles.append(KpiTile(label=f"Average {primary_col}", value=mean, format=tile_format, decimals=1))

    return tiles[:3]


def _output_mentions_external_failure(history: list[dict[str, Any]]) -> bool:
    for item in history:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "function_call_output" or item.get("name") != "you-search":
            continue
        output = item.get("output")
        if isinstance(output, str) and output.strip().lower().startswith("error"):
            return True
    return False


def _should_expect_external_trend(message: str) -> bool:
    lowered = message.lower()
    return any(token in lowered for token in ("trend", "external", "real-time", "realtime", "market", "news"))


@api.post("/chat", response_model=ChatResponse, operation_id="chatWithSupervisor")
async def chat_with_supervisor(payload: ChatRequest, request: Request) -> ChatResponse:
    try:
        forwarded_token = request.headers.get("x-forwarded-access-token")
        if not forwarded_token:
            auth_header = request.headers.get("authorization", "")
            if auth_header.lower().startswith("bearer "):
                forwarded_token = auth_header[7:].strip()
        result = await supervisor_client.invoke(payload.message, forwarded_token)
        history = result.get("history", [])
        final_payload = result.get("payload", {})
        response_text = _extract_assistant_text(history if isinstance(history, list) else [])

        data_table = _extract_table(final_payload)
        if not data_table and isinstance(history, list):
            data_table = _extract_table(history)

        chart_spec = _infer_chart_spec(data_table, payload.message)
        kpis = _derive_kpis(data_table)
        sources = _extract_sources(history, response_text) if isinstance(history, list) else []
        if not sources:
            sources = [f"serving_endpoint:{settings.supervisor_endpoint_name}"]

        notices: list[str] = []
        external_status = str(result.get("external_trend_status") or "not_used")
        if external_status == "unavailable":
            notices.append(
                "External web trend enrichment is temporarily unavailable. Returned recommendation uses internal MAS reasoning."
            )
        if bool(result.get("auth_fallback_used")):
            notices.append(
                "User-scoped Databricks token was not usable for this request. Used app identity to complete the answer."
            )

        return ChatResponse(
            response_text=response_text,
            data_table=data_table,
            chart_spec=chart_spec,
            kpis=kpis,
            sources=sources,
            notices=notices,
            debug=DebugInfo(
                supervisor_endpoint=str(result.get("endpoint_name") or settings.supervisor_endpoint_name),
                latency_ms=int(result.get("latency_ms") or 0),
                approval_hops=int(result.get("approval_hops") or 0),
                total_round_trips=int(result.get("round_trips") or 1),
                external_trend_status=external_status,
                auth_mode=str(result.get("auth_mode") or "app_identity"),
                auth_fallback_used=bool(result.get("auth_fallback_used")),
                auth_fallback_error=result.get("auth_fallback_error"),
                fallback_used=False,
                error=None,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        return ChatResponse(
            response_text=(
                "I could not complete the live MAS call. Verify endpoint access and retry the request."
            ),
            data_table=[],
            chart_spec=None,
            kpis=[],
            sources=[],
            notices=["Live supervisor call failed; no fallback answer was injected."],
            debug=DebugInfo(
                supervisor_endpoint=settings.supervisor_endpoint_name,
                latency_ms=0,
                approval_hops=0,
                total_round_trips=0,
                external_trend_status="not_used",
                fallback_used=False,
                error=str(exc),
            ),
        )


@api.get("/health", response_model=HealthResponse, operation_id="getApiHealth")
async def get_api_health() -> HealthResponse:
    supervisor_state, endpoint_ready, error = await supervisor_client.health()
    status = "ok" if endpoint_ready else "degraded"
    return HealthResponse(
        status=status,
        app_name=settings.app_name,
        app_env=settings.app_env,
        supervisor_endpoint=settings.supervisor_endpoint_name,
        supervisor_state=supervisor_state,
        endpoint_ready=endpoint_ready,
        error=error,
    )
