import json
import os
from typing import Any, Dict, List

import requests
import streamlit as st
from databricks.sdk.core import Config

st.set_page_config(page_title="Doc Side Kick", page_icon="🩺", layout="centered")

DEFAULT_QUESTION = (
    "Which patients with heart conditions have specific mentions of fatigue in their consult notes?"
)
DEFAULT_ENDPOINT = "mas-6fc6c65c-endpoint"


def _extract_text(response_json: Dict[str, Any]) -> str:
    # Handles common chat/agent response envelopes.
    if isinstance(response_json, dict):
        if "messages" in response_json and isinstance(response_json["messages"], list):
            parts = []
            for msg in response_json["messages"]:
                if isinstance(msg, dict) and msg.get("role") == "assistant":
                    content = msg.get("content")
                    if isinstance(content, str):
                        parts.append(content)
            if parts:
                return "\n\n".join(parts)

        if "choices" in response_json and isinstance(response_json["choices"], list):
            texts = []
            for choice in response_json["choices"]:
                msg = choice.get("message") if isinstance(choice, dict) else None
                if isinstance(msg, dict) and isinstance(msg.get("content"), str):
                    texts.append(msg["content"])
            if texts:
                return "\n\n".join(texts)

        if "output" in response_json:
            out = response_json["output"]
            if isinstance(out, str):
                return out
            if isinstance(out, list):
                collected: List[str] = []
                for item in out:
                    if not isinstance(item, dict):
                        continue
                    if item.get("role") != "assistant":
                        continue
                    content = item.get("content")
                    if isinstance(content, list):
                        for part in content:
                            if isinstance(part, dict) and isinstance(part.get("text"), str):
                                collected.append(part["text"])
                if collected:
                    return collected[-1]

    return json.dumps(response_json, indent=2)


def get_endpoint_status(endpoint_name: str) -> str:
    cfg = Config()
    headers = cfg.authenticate()
    resp = requests.get(
        f"https://{cfg.host}/api/2.0/serving-endpoints/{endpoint_name}",
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


def ask_supervisor(endpoint_name: str, question: str) -> str:
    cfg = Config()
    headers = cfg.authenticate()
    headers["Content-Type"] = "application/json"

    payload = {
        "input": [
            {"role": "system", "content": "You are Doc Side Kick, a clinical assistant over synthetic data."},
            {"role": "user", "content": question},
        ]
    }

    resp = requests.post(
        f"https://{cfg.host}/serving-endpoints/{endpoint_name}/invocations",
        headers=headers,
        json=payload,
        timeout=120,
    )

    if not resp.ok:
        raise RuntimeError(f"Endpoint call failed ({resp.status_code}): {resp.text[:500]}")

    return _extract_text(resp.json())


st.title("Doc Side Kick")
st.caption("Synthetic-care demo: combine structured patient data and consult-note evidence.")

endpoint_name = os.getenv("SUPERVISOR_ENDPOINT_NAME", DEFAULT_ENDPOINT)

with st.container(border=True):
    st.markdown("**Supervisor Endpoint**")
    st.code(endpoint_name)
    if st.button("Check Endpoint Status"):
        with st.spinner("Checking status..."):
            status = get_endpoint_status(endpoint_name)
        st.success(f"Status: {status}")

question = st.text_area("Clinician question", value=DEFAULT_QUESTION, height=100)

if st.button("Run Doc Side Kick", type="primary"):
    with st.spinner("Querying supervisor..."):
        try:
            answer = ask_supervisor(endpoint_name, question)
            st.markdown("### Answer")
            st.write(answer)
        except Exception as exc:
            st.error(str(exc))

st.divider()
st.caption("All outputs are based on synthetic, HIPAA-safe demo data.")
