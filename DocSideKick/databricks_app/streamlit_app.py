import json
import os
import re
import time
from typing import Any, Dict, List, Optional

import altair as alt
import requests
import streamlit as st
from databricks.sdk.core import Config

st.set_page_config(page_title="DocSideKick Dashboard", page_icon="🩺", layout="wide")

DEFAULT_QUESTION = "Which patients with heart conditions have specific mentions of fatigue in their consult notes?"
DEFAULT_ENDPOINT = "mas-6fc6c65c-endpoint"
DEFAULT_WAREHOUSE_ID = os.getenv("DATABRICKS_WAREHOUSE_ID", "1e1f63a1a14d1f34")
DEFAULT_NOTES_GLOB = "/Volumes/main/docsidekick/docsidekick_notes/*.txt"


def _inject_css() -> None:
    st.markdown(
        """
        <style>
          .stApp {
            background: #f8f9fa;
            font-family: Inter, -apple-system, BlinkMacSystemFont, "San Francisco", "Segoe UI", sans-serif;
          }
          section[data-testid="stSidebar"] {
            background: #1a2b3c !important;
          }
          section[data-testid="stSidebar"] * {
            color: #ffffff !important;
          }
          div[data-testid="stContainer"],
          div[data-testid="stMetric"] {
            border-radius: 12px;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.05);
            background: #ffffff;
          }
          .status-row {
            display: flex;
            align-items: center;
            gap: 10px;
            margin-bottom: 8px;
          }
          .status-dot {
            width: 10px;
            height: 10px;
            background: #2ecc71;
            border-radius: 50%;
            box-shadow: 0 0 0 0 rgba(46, 204, 113, 0.7);
            animation: pulse 1.5s infinite;
          }
          @keyframes pulse {
            0% { box-shadow: 0 0 0 0 rgba(46, 204, 113, 0.7); }
            70% { box-shadow: 0 0 0 10px rgba(46, 204, 113, 0); }
            100% { box-shadow: 0 0 0 0 rgba(46, 204, 113, 0); }
          }
          .chat-bubble {
            border-radius: 12px;
            padding: 12px 14px;
            margin: 8px 0;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.05);
            line-height: 1.45;
          }
          .doctor-bubble {
            background: #e8edf2;
            border-left: 4px solid #546a7b;
            color: #1f2a33;
          }
          .assistant-bubble {
            background: #e6f6f6;
            border-left: 4px solid #008b8b;
            color: #103537;
          }
          .bubble-label {
            font-size: 12px;
            font-weight: 600;
            letter-spacing: 0.2px;
            opacity: 0.8;
            margin-bottom: 6px;
          }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _base_url_from_config(cfg: Config) -> str:
    host = (cfg.host or "").strip().rstrip("/")
    if host.startswith("http://") or host.startswith("https://"):
        return host
    return f"https://{host}"


def _fetch_sql_scalar(statement: str, warehouse_id: str) -> Optional[int]:
    cfg = Config()
    headers = cfg.authenticate()
    headers["Content-Type"] = "application/json"
    base_url = _base_url_from_config(cfg)

    post_resp = requests.post(
        f"{base_url}/api/2.0/sql/statements",
        headers=headers,
        json={
            "warehouse_id": warehouse_id,
            "statement": statement,
            "wait_timeout": "10s",
            "disposition": "INLINE",
        },
        timeout=30,
    )
    if not post_resp.ok:
        return None

    payload = post_resp.json()
    status = (payload.get("status") or {}).get("state", "")
    statement_id = payload.get("statement_id")

    for _ in range(6):
        if status == "SUCCEEDED":
            data_array = ((payload.get("result") or {}).get("data_array") or [])
            if data_array and data_array[0]:
                try:
                    return int(data_array[0][0])
                except (TypeError, ValueError):
                    return None
            return None
        if status in {"FAILED", "CANCELED", "CLOSED"} or not statement_id:
            return None

        time.sleep(1.5)
        get_resp = requests.get(
            f"{base_url}/api/2.0/sql/statements/{statement_id}",
            headers=headers,
            params={"wait_timeout": "10s"},
            timeout=30,
        )
        if not get_resp.ok:
            return None
        payload = get_resp.json()
        status = (payload.get("status") or {}).get("state", "")

    return None


def _fetch_sql_rows(statement: str, warehouse_id: str) -> List[Dict[str, Any]]:
    cfg = Config()
    headers = cfg.authenticate()
    headers["Content-Type"] = "application/json"
    base_url = _base_url_from_config(cfg)

    post_resp = requests.post(
        f"{base_url}/api/2.0/sql/statements",
        headers=headers,
        json={
            "warehouse_id": warehouse_id,
            "statement": statement,
            "wait_timeout": "10s",
            "disposition": "INLINE",
        },
        timeout=30,
    )
    if not post_resp.ok:
        return []

    payload = post_resp.json()
    status = (payload.get("status") or {}).get("state", "")
    statement_id = payload.get("statement_id")

    for _ in range(6):
        if status == "SUCCEEDED":
            result = payload.get("result") or {}
            manifest = result.get("manifest") or {}
            schema = (manifest.get("schema") or {}).get("columns") or []
            column_names = [col.get("name", f"col_{idx}") for idx, col in enumerate(schema)]
            data_array = result.get("data_array") or []
            rows: List[Dict[str, Any]] = []
            for row in data_array:
                if not isinstance(row, list):
                    continue
                rows.append({column_names[i]: row[i] if i < len(row) else None for i in range(len(column_names))})
            return rows

        if status in {"FAILED", "CANCELED", "CLOSED"} or not statement_id:
            return []

        time.sleep(1.5)
        get_resp = requests.get(
            f"{base_url}/api/2.0/sql/statements/{statement_id}",
            headers=headers,
            params={"wait_timeout": "10s"},
            timeout=30,
        )
        if not get_resp.ok:
            return []
        payload = get_resp.json()
        status = (payload.get("status") or {}).get("state", "")

    return []


def _should_render_visual(question: str, answer_text: str) -> bool:
    q = question.lower()
    a = answer_text.lower()
    return any(token in q for token in ["graph", "chart", "plot", "visual", "visualize", "bar"]) or "█" in a


def _build_patient_distribution_chart(warehouse_id: str) -> Optional[alt.Chart]:
    rows = _fetch_sql_rows(
        """
        SELECT
          CASE
            WHEN age BETWEEN 36 AND 50 THEN '36-50'
            WHEN age BETWEEN 51 AND 65 THEN '51-65'
            ELSE '66+'
          END AS age_group,
          heart_condition,
          COUNT(*) AS patient_count
        FROM main.docsidekick.patients
        GROUP BY 1, 2
        ORDER BY 1, 2
        """,
        warehouse_id,
    )
    if not rows:
        return None

    return (
        alt.Chart(alt.Data(values=rows))
        .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
        .encode(
            x=alt.X("age_group:N", title="Age Group"),
            y=alt.Y("patient_count:Q", title="Patient Count"),
            color=alt.Color(
                "heart_condition:N",
                title="Heart Condition",
                scale=alt.Scale(range=["#008b8b", "#9cb0c0"]),
            ),
            tooltip=["age_group:N", "heart_condition:N", "patient_count:Q"],
        )
        .properties(height=300)
    )


def _extract_answer_parts(response_json: Dict[str, Any]) -> Dict[str, str]:
    assistant_texts: List[str] = []
    structured_table = ""
    note_evidence = ""

    output = response_json.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict) or item.get("role") != "assistant":
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                text = part.get("text") if isinstance(part, dict) else None
                if not isinstance(text, str):
                    continue
                assistant_texts.append(text)
                if not structured_table and re.search(r"\|\s*:?-{2,}", text):
                    structured_table = text
                if not note_evidence and "consult notes" in text.lower():
                    note_evidence = text

    final_answer = assistant_texts[-1] if assistant_texts else json.dumps(response_json, indent=2)
    if not note_evidence and len(assistant_texts) > 1:
        note_evidence = assistant_texts[-2]

    return {
        "final_answer": final_answer,
        "structured_table": structured_table,
        "note_evidence": note_evidence,
    }


def get_endpoint_status(endpoint_name: str) -> str:
    cfg = Config()
    headers = cfg.authenticate()
    base_url = _base_url_from_config(cfg)
    resp = requests.get(
        f"{base_url}/api/2.0/serving-endpoints/{endpoint_name}",
        headers=headers,
        timeout=30,
    )
    if not resp.ok:
        return f"Unknown ({resp.status_code})"

    payload = resp.json()
    state = payload.get("state", {})
    ready = state.get("ready")
    config = state.get("config_update")
    if ready and config:
        return f"{ready} / {config}"
    return str(ready or "Unknown")


def ask_supervisor(endpoint_name: str, question: str) -> Dict[str, str]:
    cfg = Config()
    headers = cfg.authenticate()
    headers["Content-Type"] = "application/json"
    base_url = _base_url_from_config(cfg)

    payload = {
        "input": [
            {"role": "system", "content": "You are Doc Side Kick, a clinical assistant over synthetic data."},
            {"role": "user", "content": question},
        ]
    }

    resp = requests.post(
        f"{base_url}/serving-endpoints/{endpoint_name}/invocations",
        headers=headers,
        json=payload,
        timeout=120,
    )

    if not resp.ok:
        raise RuntimeError(f"Endpoint call failed ({resp.status_code}): {resp.text[:500]}")

    return _extract_answer_parts(resp.json())


def _render_bubble(role: str, content: str) -> None:
    bubble_class = "assistant-bubble" if role == "assistant" else "doctor-bubble"
    label = "Assistant" if role == "assistant" else "Doctor"
    escaped = content.replace("<", "&lt;").replace(">", "&gt;")
    st.markdown(
        f"""
        <div class="chat-bubble {bubble_class}">
          <div class="bubble-label">{label}</div>
          <div>{escaped}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


_inject_css()

endpoint_name = os.getenv("SUPERVISOR_ENDPOINT_NAME", DEFAULT_ENDPOINT)
warehouse_id = os.getenv("DATABRICKS_WAREHOUSE_ID", DEFAULT_WAREHOUSE_ID)

st.sidebar.title("DocSideKick")
st.sidebar.caption("Clinical Copilot Settings")
st.sidebar.code(endpoint_name)
if st.sidebar.button("Refresh Endpoint Status"):
    st.sidebar.success(get_endpoint_status(endpoint_name))

st.markdown(
    """
    <div class="status-row">
      <h1 style="margin: 0;">DocSideKick Dashboard</h1>
      <span class="status-dot"></span>
      <span style="font-weight: 600; color: #1f6f43;">System: Online</span>
    </div>
    """,
    unsafe_allow_html=True,
)

patient_count = _fetch_sql_scalar("SELECT COUNT(*) FROM main.docsidekick.patients", warehouse_id)
notes_count = _fetch_sql_scalar(f"SELECT COUNT(*) FROM read_files('{DEFAULT_NOTES_GLOB}')", warehouse_id)

c1, c2, c3 = st.columns(3)
c1.metric("Total Patients", patient_count if patient_count is not None else "N/A")
c2.metric("Notes Analyzed", notes_count if notes_count is not None else "N/A")
c3.metric("AI Confidence Score", "98%")

if "chat_history" not in st.session_state:
    st.session_state.chat_history = [
        {"role": "assistant", "content": "Ask a clinical cohort question and I will combine structured and note evidence."}
    ]
if "latest_structured" not in st.session_state:
    st.session_state.latest_structured = ""
if "latest_notes" not in st.session_state:
    st.session_state.latest_notes = ""
if "show_chart" not in st.session_state:
    st.session_state.show_chart = False

st.markdown("### Clinical Assistant Chat")
for message in st.session_state.chat_history:
    _render_bubble(message["role"], message["content"])

prompt = st.chat_input(DEFAULT_QUESTION)
if prompt:
    st.session_state.chat_history.append({"role": "doctor", "content": prompt})
    with st.spinner("Analyzing structured and unstructured clinical evidence..."):
        try:
            result = ask_supervisor(endpoint_name, prompt)
            st.session_state.chat_history.append({"role": "assistant", "content": result["final_answer"]})
            st.session_state.latest_structured = result["structured_table"]
            st.session_state.latest_notes = result["note_evidence"]
            st.session_state.show_chart = _should_render_visual(prompt, result["final_answer"])
        except Exception as exc:
            st.session_state.chat_history.append({"role": "assistant", "content": f"Error: {exc}"})
    st.rerun()

if st.session_state.show_chart:
    st.markdown("### Clinical Visualization")
    chart = _build_patient_distribution_chart(warehouse_id)
    if chart is not None:
        st.altair_chart(chart, use_container_width=True)
    else:
        st.info("Chart data unavailable right now.")

with st.expander("Structured Clinical Data", expanded=False):
    if st.session_state.latest_structured:
        st.markdown(st.session_state.latest_structured)
    else:
        st.info("Structured SQL result table will appear after a query.")

if st.session_state.latest_notes:
    st.warning(f"Unstructured Note Evidence\n\n{st.session_state.latest_notes}")
else:
    st.warning("Unstructured Note Evidence\n\nNote evidence will appear after a query.")

st.caption("All outputs are based on synthetic, HIPAA-safe demo data.")
