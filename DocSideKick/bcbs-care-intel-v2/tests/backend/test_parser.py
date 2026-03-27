from bcbs_care_intelligence.backend.models import ChartType
from bcbs_care_intelligence.backend.parser import (
    count_tool_calls,
    extract_best_table,
    extract_final_assistant_text,
    infer_chart_spec,
    normalize_query_for_latency,
    parse_markdown_table,
)


def test_parse_markdown_table() -> None:
    text = """
| diagnosis_code | total_cost | num_claims |
|---|---:|---:|
| E11 | 995710.17 | 2398 |
| I10 | 307050.54 | 944 |
"""
    rows = parse_markdown_table(text)
    assert rows is not None
    assert len(rows) == 2
    assert rows[0]["diagnosis_code"] == "E11"
    assert rows[0]["total_cost"] == 995710.17


def test_extract_final_assistant_text_prefers_last_non_tool_message() -> None:
    payload = {
        "output": [
            {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "<name>tool</name>"}]},
            {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Final summary"}]},
        ]
    }
    assert extract_final_assistant_text(payload) == "Final summary"


def test_extract_best_table_from_payload() -> None:
    payload = {
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": "| a | b |\n|---|---|\n| 1 | 2 |",
                    }
                ],
            }
        ]
    }
    rows = extract_best_table(payload)
    assert rows is not None
    assert rows[0]["a"] == 1


def test_infer_chart_spec_returns_line_for_time_series() -> None:
    rows = [
        {"month": "2026-01-01", "member_count": 100},
        {"month": "2026-02-01", "member_count": 90},
    ]
    chart = infer_chart_spec(rows, "show trend")
    assert chart is not None
    assert chart.type == ChartType.LINE


def test_count_tool_calls() -> None:
    payload = {
        "output": [
            {"type": "function_call", "name": "tool_1"},
            {"type": "function_call", "name": "tool_2"},
            {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "done"}]},
        ]
    }
    assert count_tool_calls(payload) == 2


def test_normalize_query_for_latency() -> None:
    normalized = normalize_query_for_latency("Can you show me the most interesting trends in this data?")
    assert "top 5 most important payer trends" in normalized
