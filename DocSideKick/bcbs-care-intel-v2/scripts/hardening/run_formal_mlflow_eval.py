from __future__ import annotations

import json
import os
import statistics
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import mlflow
import requests
from databricks.sdk import WorkspaceClient
from mlflow.entities import Feedback
from mlflow.genai.scorers import Correctness, Guidelines, RelevanceToQuery, Safety, scorer


EXPERIMENT_PATH = os.getenv(
    "MLFLOW_EXPERIMENT_PATH",
    "/Users/will.schweitzer@databricks.com/bcbs-care-intel-evals",
)
CATALOG = os.getenv("UC_CATALOG", "main")
SCHEMA = os.getenv("UC_SCHEMA", "hls_payer_demo")
GENIE_SPACE_ID = os.getenv("GENIE_SPACE_ID", "01f126e12bda187fb0fa9f49d1c6e585")
KA_TILE_ID = os.getenv("KA_TILE_ID", "c7630b94-d330-49bd-8d4c-0b6e3a2d6a03")
MAS_ENDPOINT = os.getenv("SUPERVISOR_ENDPOINT_NAME", "mas-30532bb0-endpoint")
REQUEST_TIMEOUT_S = int(os.getenv("REQUEST_TIMEOUT_S", "50"))


@dataclass
class EvalSuite:
    layer: str
    dataset_name: str
    data: list[dict[str, Any]]


def _base_url(workspace: WorkspaceClient) -> str:
    host = (workspace.config.host or "").strip().rstrip("/")
    if host.startswith("https://") or host.startswith("http://"):
        return host
    return f"https://{host}"


def _workspace_client() -> WorkspaceClient:
    try:
        return WorkspaceClient()
    except Exception:
        host = os.getenv("DATABRICKS_HOST")
        token = os.getenv("DATABRICKS_TOKEN")
        if host and token:
            return WorkspaceClient(host=host, token=token)
        return WorkspaceClient(profile="DEFAULT")


def _headers(workspace: WorkspaceClient) -> dict[str, str]:
    headers = workspace.config.authenticate()
    headers["Content-Type"] = "application/json"
    return headers


def _ka_endpoint_name(tile_id: str) -> str:
    return f"ka-{tile_id.split('-')[0]}-endpoint"


def _assistant_text_from_payload(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    output = payload.get("output")
    if not isinstance(output, list):
        return ""
    for item in reversed(output):
        if not isinstance(item, dict):
            continue
        if item.get("type") != "message" or item.get("role") != "assistant":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for block in reversed(content):
            text = block.get("text") if isinstance(block, dict) else None
            if isinstance(text, str) and text.strip():
                return text.strip()
    return ""


def _post_endpoint(workspace: WorkspaceClient, endpoint_name: str, question: str) -> dict[str, Any]:
    response = requests.post(
        f"{_base_url(workspace)}/serving-endpoints/{endpoint_name}/invocations",
        headers=_headers(workspace),
        json={"input": [{"role": "user", "content": question}]},
        timeout=REQUEST_TIMEOUT_S,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Serving call failed [{response.status_code}] {response.text[:1000]}")
    return response.json() if response.text else {}


def _predict_genie(workspace: WorkspaceClient, question: str) -> dict[str, Any]:
    result = workspace.genie.start_conversation_and_wait(
        space_id=GENIE_SPACE_ID,
        content=question,
        timeout=timedelta(seconds=REQUEST_TIMEOUT_S),
    )
    sql_text = ""
    narrative = ""
    for att in result.attachments or []:
        if att.query and att.query.query:
            sql_text = att.query.query
        if att.text and att.text.content:
            narrative = att.text.content
    return {"response": (narrative or sql_text or "").strip(), "sql_text": sql_text}


def _predict_ka(workspace: WorkspaceClient, question: str) -> dict[str, Any]:
    payload = _post_endpoint(workspace, _ka_endpoint_name(KA_TILE_ID), question)
    return {"response": _assistant_text_from_payload(payload)}


def _predict_mas(workspace: WorkspaceClient, question: str) -> dict[str, Any]:
    payload = _post_endpoint(workspace, MAS_ENDPOINT, question)
    return {"response": _assistant_text_from_payload(payload)}


def _dataset_payloads() -> list[EvalSuite]:
    genie = EvalSuite(
        layer="genie",
        dataset_name="genie_eval_set",
        data=[
            {
                "inputs": {"question": "Find diabetic members who haven’t had an A1C test in the last 6 months."},
                "expectations": {"expected_facts": ["A1C", "6 months", "diabetic members"]},
            },
            {
                "inputs": {"question": "Show high-risk members with open gaps in care and their plan type."},
                "expectations": {"expected_facts": ["high-risk", "open gaps", "plan type"]},
            },
            {
                "inputs": {"question": "What is total claim cost for non-compliant diabetic members?"},
                "expectations": {"expected_facts": ["total cost", "non-compliant", "diabetic"]},
            },
            {
                "inputs": {"question": "Trend monthly claim cost for diagnosis code E11 over the last 12 months."},
                "expectations": {"expected_facts": ["monthly trend", "E11", "12 months"]},
            },
            {
                "inputs": {"question": "Compare compliant vs non-compliant diabetic cohorts by average risk score and member count."},
                "expectations": {"expected_facts": ["compliant", "non-compliant", "risk score", "member count"]},
            },
        ],
    )
    ka = EvalSuite(
        layer="ka",
        dataset_name="ka_eval_set",
        data=[
            {
                "inputs": {"question": "Why are members missing A1C tests?"},
                "expectations": {"expected_facts": ["barriers"], "guidelines": ["Use citations with doc_id when evidence is available."]},
            },
            {
                "inputs": {"question": "What barriers are care managers seeing?"},
                "expectations": {"expected_facts": ["care manager barriers"], "guidelines": ["Group barriers into concise themes with evidence."]},
            },
            {
                "inputs": {"question": "What evidence shows transportation or cost-sharing barriers?"},
                "expectations": {"expected_facts": ["transportation", "cost-sharing"], "guidelines": ["Say not found when evidence is missing."]},
            },
            {
                "inputs": {"question": "What are the clinical guidelines for diabetes A1C follow-up?"},
                "expectations": {"expected_facts": ["guideline", "A1C frequency"], "guidelines": ["Cite policy evidence."]},
            },
            {
                "inputs": {"question": "What interventions are recommended for overdue diabetic members?"},
                "expectations": {"expected_facts": ["intervention recommendations"], "guidelines": ["Recommendations must be evidence-grounded."]},
            },
        ],
    )
    mas = EvalSuite(
        layer="mas",
        dataset_name="mas_eval_set",
        data=[
            {
                "inputs": {"question": "Show high-risk members with open gaps in care by plan type."},
                "expectations": {"expected_facts": ["high-risk", "gaps", "plan type"]},
            },
            {
                "inputs": {"question": "What barriers are care managers seeing for missed A1C testing?"},
                "expectations": {"expected_facts": ["barriers", "A1C"]},
            },
            {
                "inputs": {"question": "Which diabetic members are overdue for A1C, why are they missing care, and what should we do?"},
                "expectations": {"expected_facts": ["cohort", "barriers", "actions"]},
            },
            {
                "inputs": {"question": "What is total cost for non-compliant diabetic members and how does it trend monthly?"},
                "expectations": {"expected_facts": ["cost", "trend"]},
            },
            {
                "inputs": {"question": "Explain this BCBS payer dataset in plain English."},
                "expectations": {"expected_facts": ["structured", "unstructured"]},
            },
        ],
    )
    return [genie, ka, mas]


@scorer
def routing_correctness(inputs: dict[str, Any], outputs: dict[str, Any]) -> Feedback:
    question = str(inputs.get("question", "")).lower()
    response = str(outputs.get("response", "")).lower()
    asks_structured = any(t in question for t in ("cost", "trend", "member count", "gap", "plan type"))
    asks_unstructured = any(t in question for t in ("barrier", "guideline", "why"))
    looks_meta = "i'm a supervisor agent" in response or "internal routing" in response
    if looks_meta:
        return Feedback(name="routing_correctness", value=False, rationale="Contains supervisor meta-language.")
    if asks_structured and "cohort" not in response and "cost" not in response and "gap" not in response:
        return Feedback(name="routing_correctness", value=False, rationale="Missing structured analytical content.")
    if asks_unstructured and "barrier" not in response and "guideline" not in response:
        return Feedback(name="routing_correctness", value=False, rationale="Missing unstructured narrative content.")
    return Feedback(name="routing_correctness", value=True, rationale="Routing behavior appears aligned to request intent.")


@scorer
def concise_exec_readability(outputs: dict[str, Any]) -> Feedback:
    response = str(outputs.get("response", "")).strip()
    words = len(response.split())
    if not response:
        return Feedback(name="concise_exec_readability", value=False, rationale="Empty response.")
    if words > 220:
        return Feedback(name="concise_exec_readability", value=False, rationale=f"Response too long ({words} words).")
    return Feedback(name="concise_exec_readability", value=True, rationale=f"Response length acceptable ({words} words).")


def _register_dataset_if_supported(dataset_name: str, records: list[dict[str, Any]]) -> Any:
    try:
        import mlflow.genai.datasets

        table_name = f"{CATALOG}.{SCHEMA}.{dataset_name}"
        managed = mlflow.genai.datasets.create_dataset(uc_table_name=table_name)
        managed.merge_records(records)
        return managed
    except Exception:
        return records


def _failed_trace_ids(run_id: str) -> list[str]:
    try:
        traces_df = mlflow.search_traces(run_id=run_id)
    except Exception:
        return []
    if traces_df is None or len(traces_df.index) == 0:
        return []
    failed: list[str] = []
    for _, row in traces_df.iterrows():
        assessments = row.get("assessments")
        trace_id = row.get("trace_id") or row.get("request_id")
        if not trace_id:
            continue
        if isinstance(assessments, list):
            for a in assessments:
                feedback = a.get("feedback", {}) if isinstance(a, dict) else {}
                value = feedback.get("value")
                if value in {"no", False, 0, "0"}:
                    failed.append(str(trace_id))
                    break
    return failed


def _predict_fn_for_layer(workspace: WorkspaceClient, layer: str):
    if layer == "genie":
        return lambda question: _predict_genie(workspace, question)
    if layer == "ka":
        return lambda question: _predict_ka(workspace, question)
    return lambda question: _predict_mas(workspace, question)


def main() -> None:
    mlflow.set_tracking_uri("databricks")
    mlflow.set_experiment(EXPERIMENT_PATH)
    workspace = _workspace_client()
    host = _base_url(workspace)

    suites = _dataset_payloads()
    leaderboard: list[dict[str, Any]] = []
    failure_patterns: list[str] = []

    for suite in suites:
        data = _register_dataset_if_supported(suite.dataset_name, suite.data)
        predict_fn = _predict_fn_for_layer(workspace, suite.layer)
        scorers = [
            Safety(),
            RelevanceToQuery(),
            Correctness(),
            Guidelines(
                name=f"{suite.layer}_instruction_adherence",
                guidelines=[
                    "Response must be concise and executive-readable.",
                    "Response must avoid unsupported claims.",
                    "Do not use internal orchestration meta-language.",
                ],
            ),
            routing_correctness,
            concise_exec_readability,
        ]
        started = time.perf_counter()
        with mlflow.start_run(run_name=f"bcbs_{suite.layer}_formal_eval"):
            results = mlflow.genai.evaluate(
                data=data,
                predict_fn=predict_fn,
                scorers=scorers,
            )
            duration_ms = int((time.perf_counter() - started) * 1000)
            metrics = dict(results.metrics or {})
            failed_trace_ids = _failed_trace_ids(results.run_id)
            leaderboard.append(
                {
                    "layer": suite.layer,
                    "dataset": suite.dataset_name,
                    "run_id": results.run_id,
                    "avg_score": statistics.mean(
                        [
                            float(v)
                            for k, v in metrics.items()
                            if isinstance(v, (int, float)) and k.endswith("/mean")
                        ]
                    )
                    if metrics
                    else 0.0,
                    "latency_ms": duration_ms,
                    "metrics": metrics,
                    "failed_trace_ids": failed_trace_ids,
                }
            )
            if failed_trace_ids:
                failure_patterns.append(
                    f"- {suite.layer}: {len(failed_trace_ids)} failed traces. "
                    f"Trace IDs: {', '.join(failed_trace_ids[:5])}"
                )

    leaderboard_sorted = sorted(leaderboard, key=lambda x: x["avg_score"], reverse=True)
    summary = {
        "experiment_path": EXPERIMENT_PATH,
        "layers": leaderboard_sorted,
    }
    print(json.dumps(summary, indent=2))

    markdown_lines = [
        "# BCBS Formal Evaluation Summary",
        "",
        "## Leaderboard",
        "",
    ]
    for row in leaderboard_sorted:
        markdown_lines.append(
            f"- **{row['layer']}**: avg_score={row['avg_score']:.4f}, run_id={row['run_id']}, latency_ms={row['latency_ms']}"
        )
    markdown_lines.extend(["", "## Top Failure Patterns", ""])
    if failure_patterns:
        markdown_lines.extend(failure_patterns)
    else:
        markdown_lines.append("- No failing traces detected by configured scorers.")

    with mlflow.start_run(run_name="bcbs_eval_rollup"):
        mlflow.log_text("\n".join(markdown_lines), "top_failure_patterns.md")
        mlflow.log_text(json.dumps(summary, indent=2), "leaderboard.json")
        mlflow.log_param("experiment_path", EXPERIMENT_PATH)
        mlflow.log_param("workspace_host", host)


if __name__ == "__main__":
    main()
