from __future__ import annotations

import re
import threading
import time

from fastapi import APIRouter

from .config import settings
from .models import (
    ChatRequest,
    ChatResponse,
    DebugInfo,
    InsightCard,
    InsightsSnapshot,
)
from .parser import (
    count_tool_calls,
    default_dataset_overview,
    extract_best_table,
    extract_final_assistant_text,
    extract_sources,
    infer_chart_spec,
    looks_like_dataset_overview_request,
    normalize_query_for_latency,
)
from .supervisor_client import DatabricksSupervisorClient

api = APIRouter(prefix=settings.api_prefix)
supervisor_client = DatabricksSupervisorClient(settings)
_response_cache: dict[str, tuple[float, ChatResponse]] = {}
_cache_lock = threading.Lock()
_UNSTRUCTURED_UNAVAILABLE_PATTERN = re.compile(
    r"(unstructured.*unavailable|cannot retrieve.*barrier|unable to retrieve.*barrier|access limitations|permissions issue.*knowledge endpoint)",
    flags=re.IGNORECASE,
)
_UNSTRUCTURED_INTENT_PATTERN = re.compile(
    r"(barrier|care manager|guideline|narrative|why are|why is|missing care|outreach|documentation)",
    flags=re.IGNORECASE,
)


def _cache_key(message: str) -> str:
    return " ".join(message.strip().lower().split())


def _get_cached_response(key: str) -> ChatResponse | None:
    now = time.time()
    with _cache_lock:
        entry = _response_cache.get(key)
        if not entry:
            return None
        expiry, payload = entry
        if now > expiry:
            _response_cache.pop(key, None)
            return None
        return payload.model_copy(deep=True)


def _set_cached_response(key: str, payload: ChatResponse) -> None:
    with _cache_lock:
        _response_cache[key] = (
            time.time() + settings.response_cache_ttl_s,
            payload.model_copy(deep=True),
        )


def _looks_degraded_unstructured_response(text: str) -> bool:
    return bool(_UNSTRUCTURED_UNAVAILABLE_PATTERN.search(text or ""))


@api.post("/chat", response_model=ChatResponse, operation_id="chatWithSupervisor")
async def chat_with_supervisor(payload: ChatRequest) -> ChatResponse:
    request_started = time.perf_counter()
    normalized_message = normalize_query_for_latency(payload.message)
    key = _cache_key(normalized_message)

    cached = _get_cached_response(key)
    if cached is not None:
        elapsed_ms = int((time.perf_counter() - request_started) * 1000)
        cached_debug = cached.debug.model_copy(deep=True) if cached.debug else DebugInfo()
        cached_debug.path = "cache_hit"
        cached_debug.latency_ms = elapsed_ms
        cached_debug.supervisor_endpoint = cached_debug.supervisor_endpoint or settings.supervisor_endpoint_name
        if elapsed_ms >= settings.soft_latency_warn_ms:
            cached_debug.latency_warning = (
                f"Response exceeded soft latency target ({settings.soft_latency_warn_ms} ms)."
            )
        else:
            cached_debug.latency_warning = None
        cached.debug = cached_debug
        return cached

    if looks_like_dataset_overview_request(payload.message):
        response = ChatResponse(
            response_text=default_dataset_overview(),
            sources=[
                "catalog:main.hls_payer_demo",
                "genie:HLS Payer Structured Genie",
                "supervisor:HLS_Payer_Supervisor",
            ],
            debug=DebugInfo(
                supervisor_endpoint=settings.supervisor_endpoint_name,
                latency_ms=int((time.perf_counter() - request_started) * 1000),
                path="live",
                fallback_used=False,
            ),
        )
        _set_cached_response(key, response)
        return response

    try:
        result = await supervisor_client.invoke(normalized_message)
        supervisor_payload = result.get("payload", {})
        response_text = extract_final_assistant_text(supervisor_payload) or "Supervisor returned no narrative text."
        rows = extract_best_table(supervisor_payload)
        chart_spec = infer_chart_spec(rows, payload.message)
        sources = extract_sources(supervisor_payload, response_text)
        request_id = result.get("request_id")
        latency_ms = result.get("latency_ms")
        endpoint_name = result.get("endpoint_name") or settings.supervisor_endpoint_name
        tool_call_count = count_tool_calls(supervisor_payload)
        response_path = "live"

        if _looks_degraded_unstructured_response(response_text):
            retry_result = await supervisor_client.invoke(
                normalized_message
                + " If unstructured evidence retrieval fails, retry once and then cite available doc_id sources."
            )
            retry_payload = retry_result.get("payload", {})
            retry_text = extract_final_assistant_text(retry_payload) or response_text
            if not _looks_degraded_unstructured_response(retry_text):
                supervisor_payload = retry_payload
                response_text = retry_text
                rows = extract_best_table(retry_payload)
                chart_spec = infer_chart_spec(rows, payload.message)
                sources = extract_sources(retry_payload, retry_text)
                request_id = retry_result.get("request_id") or request_id
                latency_ms = (
                    int(latency_ms) + int(retry_result.get("latency_ms"))
                    if isinstance(latency_ms, int) and isinstance(retry_result.get("latency_ms"), int)
                    else retry_result.get("latency_ms") or latency_ms
                )
                tool_call_count = count_tool_calls(retry_payload)
                response_path = "live_retry"

        if _looks_degraded_unstructured_response(response_text) and _UNSTRUCTURED_INTENT_PATTERN.search(
            normalized_message
        ):
            try:
                ka_result = await supervisor_client.invoke_knowledge(normalized_message)
                ka_payload = ka_result.get("payload", {})
                ka_text = extract_final_assistant_text(ka_payload) or ""
                ka_sources = extract_sources(ka_payload, ka_text)
                if ka_text and not _looks_degraded_unstructured_response(ka_text):
                    response_text = ka_text
                    rows = extract_best_table(ka_payload)
                    chart_spec = infer_chart_spec(rows, payload.message)
                    sources = ka_sources
                    request_id = ka_result.get("request_id") or request_id
                    latency_ms = (
                        int(latency_ms) + int(ka_result.get("latency_ms"))
                        if isinstance(latency_ms, int) and isinstance(ka_result.get("latency_ms"), int)
                        else ka_result.get("latency_ms") or latency_ms
                    )
                    endpoint_name = ka_result.get("endpoint_name") or endpoint_name
                    tool_call_count = count_tool_calls(ka_payload)
                    response_path = "live_ka_fallback"
            except Exception:
                # If fallback fails, return the original MAS response for transparency.
                pass

        latency_warning = None
        if isinstance(latency_ms, int) and latency_ms >= settings.soft_latency_warn_ms:
            latency_warning = (
                f"Response exceeded soft latency target ({settings.soft_latency_warn_ms} ms)."
            )

        response = ChatResponse(
            response_text=response_text,
            data_table=rows,
            chart_spec=chart_spec,
            sources=sources or None,
            debug=DebugInfo(
                supervisor_endpoint=endpoint_name,
                request_id=str(request_id) if request_id else None,
                latency_ms=int(latency_ms) if isinstance(latency_ms, int) else None,
                path=response_path,
                tool_call_count=tool_call_count,
                latency_warning=latency_warning,
                fallback_used=False,
            ),
        )
        if not _looks_degraded_unstructured_response(response_text):
            _set_cached_response(key, response)
        return response
    except Exception as exc:  # noqa: BLE001
        elapsed_ms = int((time.perf_counter() - request_started) * 1000)
        return ChatResponse(
            response_text=f"Live supervisor call failed: {exc}",
            sources=["system:error"],
            debug=DebugInfo(
                supervisor_endpoint=settings.supervisor_endpoint_name,
                latency_ms=elapsed_ms,
                path="live",
                error=str(exc),
                fallback_used=False,
            ),
        )


@api.get("/insights/snapshot", response_model=InsightsSnapshot, operation_id="getInsightsSnapshot")
async def get_insights_snapshot() -> InsightsSnapshot:
    table = [
        {"segment": "Diabetic overdue for A1C", "member_count": 530, "avg_risk_score": 2.3},
        {"segment": "Diabetic compliant", "member_count": 970, "avg_risk_score": 1.5},
        {"segment": "High-risk open gaps", "member_count": 148, "avg_risk_score": 2.9},
    ]
    return InsightsSnapshot(
        title="Member Insights Snapshot",
        summary=(
            "Diabetes remains the largest avoidable cost concentration. Gap-closure progress is measurable, "
            "but high-risk members with open A1C gaps are still overrepresented in spend."
        ),
        cards=[
            InsightCard(title="Members Overdue for A1C", value="530", detail="Primary intervention cohort"),
            InsightCard(title="High-Risk Open Gaps", value="148", detail="Risk score >= 2.0"),
            InsightCard(title="Diabetes-Linked Spend", value="$995K", detail="Top diagnosis-cost driver"),
        ],
        chart_specs=[
            {
                "type": "bar",
                "x": "segment",
                "y": "member_count",
                "data": table,
                "title": "Gap Segmentation",
            },
            {
                "type": "line",
                "x": "month",
                "y": "overdue_count",
                "data": [
                    {"month": "2025-10-01", "overdue_count": 612},
                    {"month": "2025-11-01", "overdue_count": 598},
                    {"month": "2025-12-01", "overdue_count": 582},
                    {"month": "2026-01-01", "overdue_count": 561},
                    {"month": "2026-02-01", "overdue_count": 546},
                    {"month": "2026-03-01", "overdue_count": 530},
                ],
                "title": "A1C Overdue Trend",
            },
        ],
        data_table=table,
    )
