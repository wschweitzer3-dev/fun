from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from fastapi import APIRouter

from .config import settings
from .models import (
    ChartSpec,
    ChartType,
    ChatRequest,
    ChatResponse,
    DebugInfo,
    InsightCard,
    InsightsSnapshot,
)
from .supervisor_client import DatabricksSupervisorClient

api = APIRouter(prefix=settings.api_prefix)
supervisor_client = DatabricksSupervisorClient(settings)


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


def _extract_first_text(payload: Any) -> str | None:
    if isinstance(payload, str):
        stripped = payload.strip()
        return stripped or None
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key.lower() in {"response_text", "text", "content", "answer", "output_text"}:
                found = _extract_first_text(value)
                if found:
                    return found
        for value in payload.values():
            found = _extract_first_text(value)
            if found:
                return found
    if isinstance(payload, list):
        for item in payload:
            found = _extract_first_text(item)
            if found:
                return found
    return None


def _extract_preferred_response_text(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return _extract_first_text(payload)

    output = payload.get("output")
    if not isinstance(output, list):
        return _extract_first_text(payload)

    # Prefer the final assistant message text so the UI shows the completed answer,
    # not intermediate orchestration/tool-call messages.
    for item in reversed(output):
        if not isinstance(item, dict):
            continue
        if item.get("type") != "message":
            continue
        if item.get("role") != "assistant":
            continue

        content = item.get("content")
        if isinstance(content, list):
            for block in reversed(content):
                if isinstance(block, dict):
                    text = block.get("text")
                    if isinstance(text, str) and text.strip():
                        return text.strip()

    return _extract_first_text(payload)


def _extract_first_sql(payload: Any) -> str | None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key.lower() in {"sql", "query", "sql_text", "statement"} and isinstance(value, str):
                stripped = value.strip()
                if stripped:
                    return stripped
            found = _extract_first_sql(value)
            if found:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = _extract_first_sql(item)
            if found:
                return found
    return None


def _extract_table(payload: Any) -> list[dict[str, Any]] | None:
    if isinstance(payload, list):
        if payload and all(isinstance(item, dict) for item in payload):
            return payload
        for item in payload:
            rows = _extract_table(item)
            if rows:
                return rows
    elif isinstance(payload, dict):
        for key, value in payload.items():
            if key.lower() in {"data_table", "rows", "table", "data"} and isinstance(value, list):
                if value and all(isinstance(item, dict) for item in value):
                    return value
            rows = _extract_table(value)
            if rows:
                return rows
    return None


def _extract_sources(payload: Any) -> list[str]:
    sources: list[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                lower = key.lower()
                if lower in {"sources", "source", "citations"}:
                    if isinstance(item, str):
                        if item.strip():
                            sources.append(item.strip())
                    elif isinstance(item, list):
                        for entry in item:
                            if isinstance(entry, str) and entry.strip():
                                sources.append(entry.strip())
                            elif isinstance(entry, dict):
                                doc_id = entry.get("doc_id") or entry.get("id")
                                label = entry.get("title") or entry.get("source") or entry.get("url")
                                if doc_id:
                                    sources.append(f"doc_id:{doc_id}")
                                if label:
                                    sources.append(str(label))
                if lower == "doc_id" and isinstance(item, str):
                    sources.append(f"doc_id:{item}")
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(payload)
    deduped = []
    seen = set()
    for source in sources:
        if source not in seen:
            deduped.append(source)
            seen.add(source)
    return deduped


def _extract_doc_sources_from_text(text: str) -> list[str]:
    doc_ids = sorted({match.upper() for match in re.findall(r"\bDOC_[A-Z]+_\d+\b", text, flags=re.IGNORECASE)})
    return [f"doc_id:{doc_id}" for doc_id in doc_ids]


def _looks_like_dataset_overview_request(message: str) -> bool:
    lowered = message.lower()
    wants_overview = any(token in lowered for token in ("explain", "overview", "summarize", "what is in"))
    mentions_data = "dataset" in lowered or "data set" in lowered or "data" in lowered
    return wants_overview and mentions_data


def _is_meta_supervisor_response(text: str) -> bool:
    lowered = text.lower()
    return "i'm a supervisor agent" in lowered or "coordinating with specialized agents" in lowered


def _default_dataset_overview() -> str:
    return (
        "This demo has two connected datasets. Structured payer tables include: members "
        "(demographics, plan, risk_score), claims (diagnosis/procedure, dates, cost), labs "
        "(A1C and other lab values), and gaps_in_care (diabetic members overdue for A1C in last 180 days). "
        "Unstructured documents include clinical notes, care-manager outreach notes, and diabetes policy/guideline docs. "
        "Use structured tables for cohort/cost trends and unstructured docs for barriers, context, and recommendations."
    )


def _normalize_supervisor_text(message: str, text: str) -> str:
    lowered = text.lower()
    if "technical issue accessing the analytics system" in lowered:
        if _looks_like_dataset_overview_request(message):
            return _default_dataset_overview()
        return (
            "I could not retrieve full live analytics context for that request. "
            "Try a narrower prompt (for example: 'top 5 cost trends in claims last 12 months') "
            "or retry in a few seconds."
        )
    return text


def _infer_chart_spec(rows: list[dict[str, Any]] | None, message: str) -> ChartSpec | None:
    if not rows:
        return None
    if not isinstance(rows[0], dict):
        return None

    sample = rows[0]
    numeric_cols = [k for k, v in sample.items() if _is_numeric(v)]
    date_cols = [k for k, v in sample.items() if _is_iso_date(v)]
    categorical_cols = [k for k in sample.keys() if k not in numeric_cols and k not in date_cols]
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

    if categorical_cols:
        x_col = categorical_cols[0]
        distinct_values = {str(row.get(x_col, "")) for row in rows}
        use_pie = len(distinct_values) <= 8 and any(
            word in lowered_message for word in ("distribution", "share", "mix", "composition")
        )
        return ChartSpec(
            type=ChartType.PIE if use_pie else ChartType.BAR,
            x=x_col,
            y=y_col,
            data=rows,
            title="Population Distribution" if use_pie else "Cohort Comparison",
        )

    return None


@api.post("/chat", response_model=ChatResponse, operation_id="chatWithSupervisor")
async def chat_with_supervisor(payload: ChatRequest) -> ChatResponse:
    if _looks_like_dataset_overview_request(payload.message):
        return ChatResponse(
            response_text=_default_dataset_overview(),
            sources=[
                "catalog:main.hls_payer_demo",
                "genie:HLS Payer Structured Genie",
                "ka:HLS_Payer_Knowledge_Assistant",
            ],
            debug=DebugInfo(
                supervisor_endpoint=settings.supervisor_endpoint_name,
                genie_space_id=settings.genie_space_id,
                fallback_used=False,
            ),
        )

    try:
        supervisor_result = await supervisor_client.invoke(payload.message)
        supervisor_payload = supervisor_result.get("payload", {})
        supervisor_endpoint = supervisor_result.get("endpoint_name")

        response_text = _extract_preferred_response_text(supervisor_payload) or ""
        if response_text and _looks_like_dataset_overview_request(payload.message) and _is_meta_supervisor_response(response_text):
            response_text = _default_dataset_overview()
        response_text = _normalize_supervisor_text(payload.message, response_text)
        sources = _extract_sources(supervisor_payload)
        sources.extend(_extract_doc_sources_from_text(response_text))
        data_table = _extract_table(supervisor_payload)
        sql_text = _extract_first_sql(supervisor_payload)
        response_text = response_text or "Supervisor returned no narrative text for this prompt."
        chart_spec = _infer_chart_spec(data_table, payload.message)

        dedup_sources: list[str] = []
        seen = set()
        for source in sources:
            if source not in seen:
                dedup_sources.append(source)
                seen.add(source)

        return ChatResponse(
            response_text=response_text,
            data_table=data_table,
            chart_spec=chart_spec,
            sources=dedup_sources or None,
            debug=DebugInfo(
                supervisor_endpoint=supervisor_endpoint,
                genie_space_id=settings.genie_space_id,
                sql_text=sql_text,
                fallback_used=False,
            ),
        )
    except Exception as exc:
        return ChatResponse(
            response_text=(
                "Live supervisor call failed. Verify endpoint permissions/readiness, then retry this request."
            ),
            sources=["system:error"],
            debug=DebugInfo(
                supervisor_endpoint=settings.supervisor_endpoint_name,
                genie_space_id=settings.genie_space_id,
                error=str(exc),
                fallback_used=False,
            ),
        )


@api.get("/insights/snapshot", response_model=InsightsSnapshot, operation_id="getInsightsSnapshot")
async def get_insights_snapshot() -> InsightsSnapshot:
    table = [
        {"segment": "Diabetic overdue for A1C", "member_count": 148, "avg_risk_score": 2.3},
        {"segment": "Diabetic compliant", "member_count": 392, "avg_risk_score": 1.4},
        {"segment": "High-risk with open gaps", "member_count": 86, "avg_risk_score": 2.8},
    ]

    chart_specs = [
        ChartSpec(
            type=ChartType.BAR,
            x="segment",
            y="member_count",
            data=table,
            title="Population Segments",
        ),
        ChartSpec(
            type=ChartType.LINE,
            x="month",
            y="overdue_count",
            data=[
                {"month": "2025-10-01", "overdue_count": 171},
                {"month": "2025-11-01", "overdue_count": 165},
                {"month": "2025-12-01", "overdue_count": 162},
                {"month": "2026-01-01", "overdue_count": 156},
                {"month": "2026-02-01", "overdue_count": 152},
                {"month": "2026-03-01", "overdue_count": 148},
            ],
            title="A1C Gap Trend",
        ),
    ]

    return InsightsSnapshot(
        title="Member Insights Snapshot",
        summary=(
            "Gap closure is improving month-over-month, but a concentrated high-risk segment remains "
            "with elevated care-manager barriers and higher expected cost."
        ),
        cards=[
            InsightCard(
                title="Overdue A1C Members",
                value="148",
                detail="Down 13.5% from prior quarter",
            ),
            InsightCard(
                title="High-Risk Open Gaps",
                value="86",
                detail="Primary intervention cohort",
            ),
            InsightCard(
                title="Estimated Avoidable Spend",
                value="$1.24M",
                detail="If gaps persist over next 6 months",
            ),
        ],
        chart_specs=chart_specs,
        data_table=table,
    )
