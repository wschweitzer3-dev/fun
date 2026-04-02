from invisalign_growth_copilot.evaluation.monitoring import (
    classify_failure_bucket,
    infer_intent_type,
    select_diverse_trace_records,
    stable_question_id,
    to_float_score,
)


def test_intent_inference() -> None:
    assert infer_intent_type("Show trend by region") == "structured"
    assert infer_intent_type("Why are providers hesitant?") == "unstructured"
    assert infer_intent_type("Show trend by region and explain why") == "blended"


def test_failure_bucket_classification() -> None:
    assert classify_failure_bucket("routing_quality_judge", False, "trace") == "routing"
    assert classify_failure_bucket("correctness", False, "trace") == "hallucination"
    assert classify_failure_bucket("guidelines", False, "trace") == "missing_evidence"
    assert classify_failure_bucket("verbosity_guardrail", False, "trace") == "format"
    assert classify_failure_bucket("safety", False, "infra_trace") == "infra"


def test_score_conversion() -> None:
    assert to_float_score(True) == 1.0
    assert to_float_score(False) == 0.0
    assert to_float_score("pass") == 1.0
    assert to_float_score("no") == 0.0
    assert to_float_score("0.4") == 0.4


def test_stable_question_id_consistency() -> None:
    q = "Which providers should we target?"
    assert stable_question_id(q) == stable_question_id(q)
    assert stable_question_id(q).startswith("q-")


def test_diverse_sampling_round_robin() -> None:
    records = [
        {"question": "q1", "intent_type": "structured", "latency_ms": 1000},
        {"question": "q2", "intent_type": "structured", "latency_ms": 12000},
        {"question": "q3", "intent_type": "unstructured", "latency_ms": 1500},
        {"question": "q4", "intent_type": "blended", "latency_ms": 4000},
    ]
    sampled = select_diverse_trace_records(records, sample_size=3)
    assert len(sampled) == 3
    intents = {item["intent_type"] for item in sampled}
    assert len(intents) >= 2
