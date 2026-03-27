from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from .models import ChartSpec, ChartType

TABLE_HEADER_PATTERN = re.compile(r"^\|.*\|$")
TABLE_SEPARATOR_PATTERN = re.compile(r"^\|\s*[:-]+(?:\s*\|\s*[:-]+)+\s*\|$")
DOC_ID_PATTERN = re.compile(r"\bDOC_[A-Z]+_\d+\b", flags=re.IGNORECASE)


def _is_numeric(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_iso_date(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00").replace(" ", "T"))
        return True
    except ValueError:
        return False


def _normalize_cell(value: str) -> Any:
    raw = value.strip()
    if raw.lower() in {"", "null", "none", "nat"}:
        return None
    if raw.endswith("%"):
        pct = raw[:-1].replace(",", "").strip()
        if pct.replace(".", "", 1).isdigit():
            return float(pct)
    candidate = raw.replace(",", "")
    if candidate.replace(".", "", 1).isdigit():
        if "." in candidate:
            return float(candidate)
        return int(candidate)
    return raw


def _split_row(line: str) -> list[str]:
    return [part.strip() for part in line.strip().strip("|").split("|")]


def parse_markdown_table(text: str) -> list[dict[str, Any]] | None:
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    for idx in range(len(lines) - 2):
        header_line = lines[idx]
        separator_line = lines[idx + 1]
        if not TABLE_HEADER_PATTERN.match(header_line):
            continue
        if not TABLE_SEPARATOR_PATTERN.match(separator_line):
            continue

        headers = _split_row(header_line)
        if not headers:
            continue

        rows: list[dict[str, Any]] = []
        cursor = idx + 2
        while cursor < len(lines):
            row_line = lines[cursor]
            if not TABLE_HEADER_PATTERN.match(row_line):
                break
            values = _split_row(row_line)
            if len(values) != len(headers):
                break
            row = {headers[col_idx]: _normalize_cell(values[col_idx]) for col_idx in range(len(headers))}
            rows.append(row)
            cursor += 1

        if rows:
            return rows
    return None


def extract_all_assistant_texts(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return []
    output = payload.get("output")
    if not isinstance(output, list):
        return []
    texts: list[str] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "message" or item.get("role") != "assistant":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                texts.append(text.strip())
    return texts


def count_tool_calls(payload: Any) -> int:
    if not isinstance(payload, dict):
        return 0
    output = payload.get("output")
    if not isinstance(output, list):
        return 0
    return sum(1 for item in output if isinstance(item, dict) and item.get("type") == "function_call")


def extract_final_assistant_text(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    output = payload.get("output")
    if not isinstance(output, list):
        return None
    for item in reversed(output):
        if not isinstance(item, dict):
            continue
        if item.get("type") != "message" or item.get("role") != "assistant":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for block in reversed(content):
            if not isinstance(block, dict):
                continue
            text = block.get("text")
            if not isinstance(text, str) or not text.strip():
                continue
            normalized = text.strip().lower()
            if normalized.startswith("<name>") and normalized.endswith("</name>"):
                continue
            if normalized in {"calling tool"}:
                continue
            return text.strip()
    return None


def extract_best_table(payload: Any) -> list[dict[str, Any]] | None:
    if not isinstance(payload, dict):
        return None
    output = payload.get("output")
    if not isinstance(output, list):
        return None

    best_rows: list[dict[str, Any]] | None = None
    for item in reversed(output):
        if not isinstance(item, dict):
            continue
        if item.get("type") != "message" or item.get("role") != "assistant":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for block in reversed(content):
            if not isinstance(block, dict):
                continue
            text = block.get("text")
            if not isinstance(text, str) or not text.strip():
                continue
            rows = parse_markdown_table(text)
            if rows and (best_rows is None or len(rows) >= len(best_rows)):
                best_rows = rows
                # Short-circuit when we find a reasonably rich table.
                if len(rows) >= 5:
                    return best_rows
    return best_rows


def extract_sources(payload: Any, response_text: str) -> list[str]:
    sources: list[str] = []

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                lower = key.lower()
                if lower in {"source", "sources", "citations"}:
                    if isinstance(value, str) and value.strip():
                        sources.append(value.strip())
                    elif isinstance(value, list):
                        for item in value:
                            if isinstance(item, str) and item.strip():
                                sources.append(item.strip())
                            elif isinstance(item, dict):
                                url = item.get("url")
                                doc_id = item.get("doc_id") or item.get("id")
                                title = item.get("title") or item.get("source")
                                if isinstance(doc_id, str):
                                    sources.append(f"doc_id:{doc_id.strip()}")
                                if isinstance(url, str) and url.strip():
                                    sources.append(url.strip())
                                if isinstance(title, str) and title.strip():
                                    sources.append(title.strip())
                if lower == "doc_id" and isinstance(value, str):
                    sources.append(f"doc_id:{value.strip()}")
                visit(value)
        elif isinstance(node, list):
            for item in node:
                visit(item)

    visit(payload)
    all_texts = [response_text, *extract_all_assistant_texts(payload)]
    for text in all_texts:
        for doc_id in sorted({match.upper() for match in DOC_ID_PATTERN.findall(text)}):
            sources.append(f"doc_id:{doc_id}")
    deduped: list[str] = []
    seen: set[str] = set()
    for source in sources:
        if source and source not in seen:
            seen.add(source)
            deduped.append(source)
    return deduped


def infer_chart_spec(rows: list[dict[str, Any]] | None, prompt: str) -> ChartSpec | None:
    if not rows:
        return None
    sample = rows[0]
    numeric_cols = [col for col, value in sample.items() if _is_numeric(value)]
    if not numeric_cols:
        return None
    date_cols = [col for col, value in sample.items() if _is_iso_date(value)]
    categorical_cols = [col for col in sample.keys() if col not in numeric_cols and col not in date_cols]

    y_col = numeric_cols[0]
    lowered_prompt = prompt.lower()

    if date_cols:
        return ChartSpec(type=ChartType.LINE, x=date_cols[0], y=y_col, data=rows, title="Trend Over Time")

    if categorical_cols:
        x_col = categorical_cols[0]
        distinct_values = {str(row.get(x_col, "")) for row in rows}
        pie_hint = any(token in lowered_prompt for token in ("share", "mix", "composition", "distribution"))
        chart_type = ChartType.PIE if pie_hint and len(distinct_values) <= 8 else ChartType.BAR
        title = "Composition" if chart_type == ChartType.PIE else "Comparison"
        return ChartSpec(type=chart_type, x=x_col, y=y_col, data=rows, title=title)
    return None


def looks_like_dataset_overview_request(message: str) -> bool:
    lowered = message.lower()
    asks_explain = any(token in lowered for token in ("explain", "overview", "summarize", "what is in"))
    mentions_dataset = any(token in lowered for token in ("dataset", "data set", "data"))
    return asks_explain and mentions_dataset


def default_dataset_overview() -> str:
    return (
        "This app combines structured and unstructured payer data. Structured tables are "
        "main.hls_payer_demo.members (member demographics/plan/risk), claims (diagnosis, procedures, dates, costs), "
        "labs (A1C and other lab values), and gaps_in_care (diabetic members missing A1C in the last 180 days). "
        "Unstructured sources include clinical notes, care-manager outreach notes, and policy/guideline documents. "
        "Use structured outputs for cohort/cost/trend analysis and unstructured outputs for barriers, context, and recommendations."
    )


def normalize_query_for_latency(message: str) -> str:
    lowered = message.lower()
    if "most interesting trends" in lowered or "interesting trends" in lowered:
        return (
            "Summarize the top 5 most important payer trends with numeric evidence only: "
            "E11 cost concentration, A1C gap size, high-risk segment size, monthly claim-cost trend, and top barrier themes."
        )
    if "analyze this dataset" in lowered and "step by step" in lowered:
        return (
            "Provide a concise executive summary of this dataset with no more than 5 bullet points and one action recommendation."
        )
    return message
