from invisalign_growth_copilot.backend.models import ChartType
from invisalign_growth_copilot.backend.router import _extract_assistant_text, _infer_chart_spec


def test_infer_bar_chart_from_categorical_numeric_rows() -> None:
    rows = [
        {"provider_type": "GP Dentist", "total_revenue": 22000},
        {"provider_type": "Orthodontist", "total_revenue": 64000},
    ]

    chart = _infer_chart_spec(rows, "show revenue comparison")
    assert chart is not None
    assert chart.type == ChartType.BAR
    assert chart.x == "provider_type"
    assert chart.y == "total_revenue"


def test_infer_line_chart_from_date_rows() -> None:
    rows = [
        {"case_date": "2026-02-01", "total_cases": 140},
        {"case_date": "2026-02-02", "total_cases": 152},
    ]

    chart = _infer_chart_spec(rows, "show trends")
    assert chart is not None
    assert chart.type == ChartType.LINE


def test_extract_assistant_text_uses_final_assistant_message() -> None:
    history = [
        {"role": "user", "content": "hello"},
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

    assert _extract_assistant_text(history) == "Final answer"
