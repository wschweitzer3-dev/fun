from __future__ import annotations

import hashlib
import json
from collections import defaultdict, deque
from typing import Any


def stable_question_id(question: str) -> str:
    digest = hashlib.sha256(question.strip().encode("utf-8")).hexdigest()
    return f"q-{digest[:16]}"


def infer_intent_type(question: str) -> str:
    q = question.lower()
    structured_signals = (
        "trend",
        "cost",
        "revenue",
        "count",
        "compare",
        "segment",
        "cohort",
        "market share",
        "growth",
    )
    narrative_signals = (
        "why",
        "risk",
        "barrier",
        "objection",
        "talk track",
        "what should",
        "how should",
        "playbook",
        "recommendation",
    )
    has_structured = any(token in q for token in structured_signals)
    has_narrative = any(token in q for token in narrative_signals)
    if has_structured and has_narrative:
        return "blended"
    if has_narrative:
        return "unstructured"
    return "structured"


def latency_bucket(latency_ms: int | None) -> str:
    if latency_ms is None:
        return "unknown"
    if latency_ms < 3_000:
        return "fast"
    if latency_ms < 10_000:
        return "medium"
    return "slow"


def classify_failure_bucket(
    scorer_name: str,
    score_raw: Any,
    source: str,
) -> str:
    if source == "infra_trace":
        return "infra"
    passed = _is_pass_value(score_raw)
    if passed:
        return "pass"
    scorer = scorer_name.lower()
    if "routing" in scorer:
        return "routing"
    if "correctness" in scorer:
        return "hallucination"
    if "grounded_actionability" in scorer or "guidelines" in scorer:
        return "missing_evidence"
    if "empty_or_meta" in scorer or "verbosity_guardrail" in scorer:
        return "format"
    return "format"


def to_float_score(score_raw: Any) -> float | None:
    if isinstance(score_raw, bool):
        return 1.0 if score_raw else 0.0
    if isinstance(score_raw, (int, float)):
        return float(score_raw)
    if isinstance(score_raw, str):
        lowered = score_raw.strip().lower()
        if lowered in {"true", "yes", "pass"}:
            return 1.0
        if lowered in {"false", "no", "fail"}:
            return 0.0
        try:
            return float(lowered)
        except ValueError:
            return None
    return None


def select_diverse_trace_records(records: list[dict[str, Any]], sample_size: int) -> list[dict[str, Any]]:
    if sample_size <= 0:
        return []
    by_bucket: dict[tuple[str, str], deque[dict[str, Any]]] = defaultdict(deque)
    for record in records:
        intent = str(record.get("intent_type") or "structured")
        bucket = latency_bucket(_safe_int(record.get("latency_ms")))
        by_bucket[(intent, bucket)].append(record)
    ordered_keys = sorted(by_bucket.keys())
    out: list[dict[str, Any]] = []
    while len(out) < sample_size and ordered_keys:
        next_keys: list[tuple[str, str]] = []
        for key in ordered_keys:
            queue = by_bucket[key]
            if queue:
                out.append(queue.popleft())
                if len(out) >= sample_size:
                    break
            if queue:
                next_keys.append(key)
        ordered_keys = next_keys
    return out


def parse_trace_inputs(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        candidate = raw.strip()
        if not candidate:
            return {}
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return {}
    return {}


def _is_pass_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value > 0
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "pass", "1"}
    return False


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None
