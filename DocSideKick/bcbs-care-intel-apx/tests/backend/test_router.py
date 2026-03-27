from bcbs_care_intelligence.backend.models import ChartType
from bcbs_care_intelligence.backend.router import _extract_preferred_response_text, _infer_chart_spec


def test_infer_bar_chart_from_categorical_numeric_rows() -> None:
    rows = [
        {"condition": "Diabetes", "member_count": 148},
        {"condition": "Hypertension", "member_count": 97},
    ]

    chart = _infer_chart_spec(rows, "show count by condition")
    assert chart is not None
    assert chart.type == ChartType.BAR
    assert chart.x == "condition"
    assert chart.y == "member_count"


def test_infer_line_chart_from_date_rows() -> None:
    rows = [
        {"month": "2026-01-01", "member_count": 160},
        {"month": "2026-02-01", "member_count": 152},
    ]

    chart = _infer_chart_spec(rows, "show trends")
    assert chart is not None
    assert chart.type == ChartType.LINE


def test_extract_preferred_response_text_uses_final_assistant_message() -> None:
    payload = {
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Working on it..."}],
            },
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Final answer"}],
            },
        ]
    }

    assert _extract_preferred_response_text(payload) == "Final answer"
