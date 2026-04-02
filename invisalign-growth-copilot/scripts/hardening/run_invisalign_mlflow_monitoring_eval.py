from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import statistics
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import mlflow
import pandas as pd
from databricks.sdk import WorkspaceClient
from mlflow.entities import Feedback
from mlflow.genai.judges import make_judge
from mlflow.genai.scorers import Correctness, Guidelines, RelevanceToQuery, Safety, scorer

from invisalign_growth_copilot.backend.config import AppSettings
from invisalign_growth_copilot.backend.supervisor_client import DatabricksSupervisorClient
from invisalign_growth_copilot.evaluation.monitoring import (
    classify_failure_bucket,
    infer_intent_type,
    parse_trace_inputs,
    select_diverse_trace_records,
    stable_question_id,
    to_float_score,
)

LOGGER = logging.getLogger("invisalign_mlflow_monitoring_eval")

DEFAULT_EXPERIMENT_PATH = "/Users/will.schweitzer@databricks.com/invisalign-growth-copilot-evals"
DEFAULT_DATASET_NAME = "main.invisalign_growth_eval.invisalign_mas_eval_set"
DEFAULT_RESULTS_TABLE = "main.invisalign_growth_eval.monitoring_eval_results"
DEFAULT_RUNS_TABLE = "main.invisalign_growth_eval.monitoring_eval_runs"
DEFAULT_INFRA_TABLE = "main.invisalign_growth_eval.monitoring_eval_infra_failures"
DEFAULT_MONITORING_SQL = (
    Path(__file__).resolve().parent / "sql" / "create_monitoring_views.sql"
)
DEFAULT_SEED_PATH = (
    Path(__file__).resolve().parent / "data" / "invisalign_mas_seed_eval_set.json"
)


@dataclass
class InfraFailure:
    question_id: str
    question: str
    source: str
    reason: str
    trace_id: str | None = None


@dataclass
class EvalContext:
    endpoint_name: str
    mode: str
    run_started_utc: str
    infra_failures: list[InfraFailure]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run monitoring/debug MLflow evaluation for Invisalign MAS."
    )
    parser.add_argument("--experiment-path", default=os.getenv("MLFLOW_EXPERIMENT_PATH", DEFAULT_EXPERIMENT_PATH))
    parser.add_argument("--supervisor-endpoint", default=os.getenv("SUPERVISOR_ENDPOINT_NAME", "mas-47342197-endpoint"))
    parser.add_argument("--dataset-name", default=os.getenv("EVAL_DATASET_NAME", DEFAULT_DATASET_NAME))
    parser.add_argument("--sample-size", type=int, default=int(os.getenv("EVAL_SAMPLE_SIZE", "24")))
    parser.add_argument("--mode", choices=["daily", "weekly"], default=os.getenv("EVAL_MODE", "daily"))
    parser.add_argument("--seed-path", default=os.getenv("EVAL_SEED_PATH", str(DEFAULT_SEED_PATH)))
    parser.add_argument("--trace-max-results", type=int, default=int(os.getenv("EVAL_TRACE_MAX_RESULTS", "400")))
    parser.add_argument("--trace-experiment-ids", default=os.getenv("TRACE_EXPERIMENT_IDS", ""))
    parser.add_argument("--judge-model", default=os.getenv("EVAL_JUDGE_MODEL", ""))
    parser.add_argument("--request-timeout-s", type=int, default=int(os.getenv("EVAL_REQUEST_TIMEOUT_S", "150")))
    parser.add_argument("--invoke-retries", type=int, default=int(os.getenv("EVAL_INVOKE_RETRIES", "3")))
    parser.add_argument("--warehouse-id", default=os.getenv("EVAL_WAREHOUSE_ID", ""))
    parser.add_argument("--results-table", default=os.getenv("EVAL_RESULTS_TABLE", DEFAULT_RESULTS_TABLE))
    parser.add_argument("--runs-table", default=os.getenv("EVAL_RUNS_TABLE", DEFAULT_RUNS_TABLE))
    parser.add_argument("--infra-table", default=os.getenv("EVAL_INFRA_TABLE", DEFAULT_INFRA_TABLE))
    parser.add_argument("--apply-monitoring-sql", action="store_true")
    parser.add_argument("--monitoring-sql-path", default=os.getenv("EVAL_MONITORING_SQL_PATH", str(DEFAULT_MONITORING_SQL)))
    parser.add_argument("--dry-run", action="store_true", help="Build datasets and scorers but skip mlflow.genai.evaluate")
    parser.add_argument("--log-level", default=os.getenv("LOG_LEVEL", "INFO"))
    return parser.parse_args()


def _base_url(workspace: WorkspaceClient) -> str:
    host = (workspace.config.host or "").strip().rstrip("/")
    if host.startswith("https://") or host.startswith("http://"):
        return host
    return f"https://{host}"


def _load_seed_records(seed_path: Path, mode: str) -> list[dict[str, Any]]:
    seed_payload = json.loads(seed_path.read_text(encoding="utf-8"))
    if not isinstance(seed_payload, list):
        raise ValueError(f"Seed file must be a list: {seed_path}")
    if mode == "daily":
        seed_payload = seed_payload[:14]

    rows: list[dict[str, Any]] = []
    for item in seed_payload:
        question = str(item.get("question", "")).strip()
        if not question:
            continue
        expected_route = str(item.get("expected_route") or infer_intent_type(question))
        rows.append(
            {
                "inputs": {
                    "question": question,
                    "question_id": stable_question_id(question),
                    "source": "seed",
                    "intent_type": infer_intent_type(question),
                },
                "expectations": {
                    "expected_facts": item.get("expected_facts") or [],
                    "expected_route": expected_route,
                    "must_include": item.get("must_include") or [],
                    "must_not_include": item.get("must_not_include") or [],
                },
                "metadata": {
                    "source": "seed",
                    "intent_type": infer_intent_type(question),
                },
            }
        )
    return rows


def _extract_trace_candidates(traces_df: pd.DataFrame) -> tuple[list[dict[str, Any]], list[InfraFailure]]:
    candidates: list[dict[str, Any]] = []
    infra: list[InfraFailure] = []
    if traces_df is None or traces_df.empty:
        return candidates, infra

    for _, row in traces_df.iterrows():
        trace_id = str(row.get("trace_id") or row.get("request_id") or "")
        request_payload = parse_trace_inputs(row.get("request"))
        if not request_payload:
            trace_meta = row.get("trace_metadata")
            if isinstance(trace_meta, dict):
                request_payload = parse_trace_inputs(trace_meta.get("mlflow.traceInputs"))

        question = str(
            request_payload.get("question")
            or request_payload.get("message")
            or request_payload.get("query")
            or ""
        ).strip()
        response_payload = row.get("response")
        response_text = ""
        if isinstance(response_payload, dict):
            response_text = str(
                response_payload.get("response")
                or response_payload.get("response_text")
                or ""
            ).strip()
        state = str(row.get("state") or "")
        latency_ms = _coerce_int(row.get("execution_duration"))

        if not question:
            infra.append(
                InfraFailure(
                    question_id=f"trace-{trace_id[:16]}" if trace_id else "trace-unknown",
                    question="[missing question from trace]",
                    source="infra_trace",
                    reason="trace_missing_question",
                    trace_id=trace_id or None,
                )
            )
            continue
        if state and "OK" not in state.upper():
            infra.append(
                InfraFailure(
                    question_id=stable_question_id(question),
                    question=question,
                    source="infra_trace",
                    reason=f"trace_state_{state}",
                    trace_id=trace_id or None,
                )
            )
            continue
        if not response_text:
            infra.append(
                InfraFailure(
                    question_id=stable_question_id(question),
                    question=question,
                    source="infra_trace",
                    reason="trace_empty_response",
                    trace_id=trace_id or None,
                )
            )
            continue

        candidates.append(
            {
                "question": question,
                "question_id": stable_question_id(question),
                "intent_type": infer_intent_type(question),
                "source": "trace",
                "trace_id": trace_id or None,
                "latency_ms": latency_ms,
            }
        )
    return candidates, infra


def _build_trace_eval_records(candidates: list[dict[str, Any]], sample_size: int) -> list[dict[str, Any]]:
    sampled = select_diverse_trace_records(candidates, sample_size=sample_size)
    records: list[dict[str, Any]] = []
    for c in sampled:
        question = str(c["question"])
        records.append(
            {
                "inputs": {
                    "question": question,
                    "question_id": c["question_id"],
                    "source": "trace",
                    "intent_type": c["intent_type"],
                },
                "expectations": {
                    "expected_route": c["intent_type"],
                    "expected_facts": [],
                    "must_include": [],
                    "must_not_include": [],
                },
                "metadata": {
                    "source": "trace",
                    "intent_type": c["intent_type"],
                    "origin_trace_id": c.get("trace_id"),
                },
            }
        )
    return records


def _resolve_trace_experiment_ids(experiment_id: str, trace_experiment_ids_arg: str) -> list[str]:
    if trace_experiment_ids_arg.strip():
        return [part.strip() for part in trace_experiment_ids_arg.split(",") if part.strip()]
    return [experiment_id]


def _create_or_update_dataset(
    dataset_name: str,
    experiment_id: str,
    records: list[dict[str, Any]],
) -> tuple[Any | None, str | None]:
    dataset = None
    try:
        for candidate in mlflow.genai.datasets.search_datasets(experiment_ids=[experiment_id], max_results=200):
            if candidate.name == dataset_name:
                dataset = candidate
                break
        if dataset is None:
            dataset = mlflow.genai.datasets.create_dataset(
                name=dataset_name,
                experiment_id=experiment_id,
            )
        dataset.merge_records(records)
        return dataset, None
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning(
            "Managed dataset unavailable (%s). Continuing with in-memory eval records only.",
            exc,
        )
        return None, str(exc)


def _extract_assistant_text(history: list[dict[str, Any]]) -> str:
    for item in reversed(history):
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
            text = str(block.get("text") or "").strip()
            if text:
                return text
    return ""


def _make_predict_fn(context: EvalContext, client: DatabricksSupervisorClient, invoke_retries: int):
    def _predict_fn(
        question: str,
        question_id: str | None = None,
        source: str | None = None,
        intent_type: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        latency_ms: int | None = None
        for attempt in range(1, invoke_retries + 1):
            started = time.perf_counter()
            try:
                result = asyncio.run(client.invoke(question))
                latency_ms = int(result.get("latency_ms") or ((time.perf_counter() - started) * 1000))
                history = result.get("history") or []
                response_text = _extract_assistant_text(history if isinstance(history, list) else [])
                payload = result.get("payload") or {}
                trace_id = None
                if isinstance(payload, dict):
                    trace_id = str(payload.get("id") or payload.get("trace_id") or "") or None
                if not response_text.strip():
                    context.infra_failures.append(
                        InfraFailure(
                            question_id=question_id or stable_question_id(question),
                            question=question,
                            source=source or "eval",
                            reason="invoke_empty_response",
                            trace_id=trace_id,
                        )
                    )
                return {
                    "response": response_text.strip(),
                    "latency_ms": latency_ms,
                    "trace_id": trace_id,
                    "intent_type": intent_type or infer_intent_type(question),
                    "source": source or "eval",
                }
            except Exception as exc:  # noqa: BLE001
                latency_ms = int((time.perf_counter() - started) * 1000)
                if attempt >= invoke_retries:
                    context.infra_failures.append(
                        InfraFailure(
                            question_id=question_id or stable_question_id(question),
                            question=question,
                            source=source or "eval",
                            reason=f"invoke_exception:{exc}",
                            trace_id=None,
                        )
                    )
                    return {
                        "response": "",
                        "latency_ms": latency_ms,
                        "trace_id": None,
                        "intent_type": intent_type or infer_intent_type(question),
                        "source": source or "eval",
                        "error": str(exc),
                    }
                time.sleep(min(6, attempt * 1.5))

        return {
            "response": "",
            "latency_ms": latency_ms or 0,
            "trace_id": None,
            "intent_type": intent_type or infer_intent_type(question),
            "source": source or "eval",
            "error": "retry_exhausted",
        }

    return _predict_fn


@scorer
def empty_or_meta_response(outputs: dict[str, Any]) -> Feedback:
    response = str(outputs.get("response", "")).strip()
    if not response:
        return Feedback(name="empty_or_meta_response", value=False, rationale="Empty response.")
    lowered = response.lower()
    banned = (
        "internal orchestration",
        "tool-call",
        "tool call",
        "i am a supervisor",
        "routing logic",
    )
    if any(token in lowered for token in banned):
        return Feedback(name="empty_or_meta_response", value=False, rationale="Contains internal/meta language.")
    return Feedback(name="empty_or_meta_response", value=True, rationale="Response contains actionable user-facing content.")


@scorer
def verbosity_guardrail(outputs: dict[str, Any]) -> Feedback:
    response = str(outputs.get("response", "")).strip()
    words = len(response.split())
    if words == 0:
        return Feedback(name="verbosity_guardrail", value=False, rationale="Empty response.")
    if words > 240:
        return Feedback(name="verbosity_guardrail", value=False, rationale=f"Response too long ({words} words).")
    return Feedback(name="verbosity_guardrail", value=True, rationale=f"Response length acceptable ({words} words).")


def _build_judge_scorers(judge_model: str) -> list[Any]:
    model_name = judge_model.strip() or None
    routing_quality_judge = make_judge(
        name="routing_quality_judge",
        instructions=(
            "Given inputs {{ inputs }}, expectations {{ expectations }}, and outputs {{ outputs }}, "
            "return true if the response type matches expected_route (structured, unstructured, blended) "
            "and directly addresses the user request without irrelevant detours."
        ),
        feedback_value_type=bool,
        model=model_name,
        description="Checks if response routing behavior matches user intent.",
    )
    grounded_actionability_judge = make_judge(
        name="grounded_actionability_judge",
        instructions=(
            "Given inputs {{ inputs }}, expectations {{ expectations }}, and outputs {{ outputs }}, "
            "return true if the response is actionable, specific, and grounded in the request context. "
            "Return false for vague, generic, or likely unsupported claims."
        ),
        feedback_value_type=bool,
        model=model_name,
        description="Checks actionability and grounding quality.",
    )
    return [routing_quality_judge, grounded_actionability_judge]


def _build_scorers(judge_model: str) -> tuple[list[Any], list[str]]:
    model_name = judge_model.strip() or None
    scorers: list[Any] = [
        Safety(model=model_name, name="safety"),
        RelevanceToQuery(model=model_name, name="relevance_to_query"),
        Correctness(model=model_name, name="correctness"),
        Guidelines(
            name="guidelines",
            model=model_name,
            guidelines=[
                "Use concise, execution-ready language for sales teams.",
                "Avoid internal orchestration/tool-call details in the final answer.",
                "Avoid unsupported claims; state uncertainty when evidence appears incomplete.",
            ],
        ),
        empty_or_meta_response,
        verbosity_guardrail,
    ]
    scorer_names = [
        "safety",
        "relevance_to_query",
        "correctness",
        "guidelines",
        "empty_or_meta_response",
        "verbosity_guardrail",
    ]
    try:
        judge_scorers = _build_judge_scorers(judge_model=judge_model)
        scorers.extend(judge_scorers)
        scorer_names.extend(["routing_quality_judge", "grounded_actionability_judge"])
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("LLM judges disabled due to initialization error: %s", exc)
    return scorers, scorer_names


def _register_scorers_if_possible(scorers: list[Any]) -> None:
    for scorer_obj in scorers:
        register_fn = getattr(scorer_obj, "register", None)
        if callable(register_fn):
            try:
                register_fn()
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("Scorer registration skipped for %s: %s", getattr(scorer_obj, "name", scorer_obj), exc)


def _flatten_eval_results(
    eval_df: pd.DataFrame,
    run_id: str,
    scorer_names: list[str],
    mode: str,
    dataset_name: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if eval_df is None or eval_df.empty:
        return rows
    now = datetime.now(UTC).isoformat()

    for _, eval_row in eval_df.iterrows():
        request = eval_row.get("request")
        request_dict = request if isinstance(request, dict) else {}
        response = eval_row.get("response")
        response_dict = response if isinstance(response, dict) else {}
        question = str(request_dict.get("question") or "")
        question_id = str(request_dict.get("question_id") or stable_question_id(question))
        source = str(request_dict.get("source") or "unknown")
        intent_type = str(request_dict.get("intent_type") or infer_intent_type(question))
        response_text = str(response_dict.get("response") or "")
        trace_id = str(eval_row.get("trace_id") or response_dict.get("trace_id") or "") or None
        latency_ms = _coerce_int(response_dict.get("latency_ms"))
        for scorer_name in scorer_names:
            value_col = f"{scorer_name}/value"
            rationale_col = f"{scorer_name}/rationale"
            if value_col not in eval_row:
                continue
            score_raw = eval_row.get(value_col)
            rationale = str(eval_row.get(rationale_col) or "")
            failure_bucket = classify_failure_bucket(scorer_name, score_raw, source=source)
            rows.append(
                {
                    "run_id": run_id,
                    "timestamp": now,
                    "mode": mode,
                    "dataset_name": dataset_name,
                    "dataset_version": "",
                    "question_id": question_id,
                    "question": question,
                    "response": response_text,
                    "scorer_name": scorer_name,
                    "score": to_float_score(score_raw),
                    "score_raw": str(score_raw),
                    "rationale": rationale,
                    "trace_id": trace_id,
                    "latency_ms": latency_ms,
                    "intent_type": intent_type,
                    "source": source,
                    "failure_bucket": failure_bucket,
                }
            )
    return rows


def _coerce_int(value: Any) -> int | None:
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


def _table_parts(table_fqn: str) -> tuple[str, str, str]:
    parts = [part.strip() for part in table_fqn.split(".") if part.strip()]
    if len(parts) != 3:
        raise ValueError(f"Expected 3-level table name catalog.schema.table, got: {table_fqn}")
    return parts[0], parts[1], parts[2]


def _sql_escape(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)) and not (isinstance(value, float) and math.isnan(value)):
        return str(value)
    text = str(value).replace("'", "''")
    return f"'{text}'"


def _execute_sql(workspace: WorkspaceClient, warehouse_id: str, statement: str) -> None:
    response = workspace.statement_execution.execute_statement(
        statement=statement,
        warehouse_id=warehouse_id,
        wait_timeout="50s",
    )
    status = str(getattr(response, "status", "") or "")
    if "FAILED" in status or "CANCELED" in status:
        raise RuntimeError(f"SQL statement failed: {status}\n{statement}")


def _persist_tables(
    workspace: WorkspaceClient,
    warehouse_id: str,
    results_table: str,
    runs_table: str,
    infra_table: str,
) -> None:
    for table in [results_table, runs_table, infra_table]:
        catalog, schema, _ = _table_parts(table)
        _execute_sql(workspace, warehouse_id, f"CREATE SCHEMA IF NOT EXISTS {catalog}.{schema}")

    _execute_sql(
        workspace,
        warehouse_id,
        f"""
CREATE TABLE IF NOT EXISTS {results_table} (
  run_id STRING,
  timestamp TIMESTAMP,
  mode STRING,
  dataset_name STRING,
  dataset_version STRING,
  question_id STRING,
  question STRING,
  response STRING,
  scorer_name STRING,
  score DOUBLE,
  score_raw STRING,
  rationale STRING,
  trace_id STRING,
  latency_ms INT,
  intent_type STRING,
  source STRING,
  failure_bucket STRING
)
USING DELTA
""",
    )
    _execute_sql(
        workspace,
        warehouse_id,
        f"""
CREATE TABLE IF NOT EXISTS {runs_table} (
  run_id STRING,
  timestamp TIMESTAMP,
  mode STRING,
  dataset_name STRING,
  endpoint_name STRING,
  avg_score DOUBLE,
  metrics_json STRING,
  total_eval_rows INT,
  total_failures INT,
  infra_failures INT,
  workspace_host STRING
)
USING DELTA
""",
    )
    _execute_sql(
        workspace,
        warehouse_id,
        f"""
CREATE TABLE IF NOT EXISTS {infra_table} (
  run_id STRING,
  timestamp TIMESTAMP,
  question_id STRING,
  question STRING,
  source STRING,
  reason STRING,
  trace_id STRING
)
USING DELTA
""",
    )


def _persist_eval_rows(
    workspace: WorkspaceClient,
    warehouse_id: str,
    results_table: str,
    rows: list[dict[str, Any]],
) -> None:
    for row in rows:
        _execute_sql(
            workspace,
            warehouse_id,
            f"""
INSERT INTO {results_table} VALUES (
  {_sql_escape(row.get("run_id"))},
  {_sql_escape(row.get("timestamp"))},
  {_sql_escape(row.get("mode"))},
  {_sql_escape(row.get("dataset_name"))},
  {_sql_escape(row.get("dataset_version"))},
  {_sql_escape(row.get("question_id"))},
  {_sql_escape(row.get("question"))},
  {_sql_escape(row.get("response"))},
  {_sql_escape(row.get("scorer_name"))},
  {_sql_escape(row.get("score"))},
  {_sql_escape(row.get("score_raw"))},
  {_sql_escape(row.get("rationale"))},
  {_sql_escape(row.get("trace_id"))},
  {_sql_escape(row.get("latency_ms"))},
  {_sql_escape(row.get("intent_type"))},
  {_sql_escape(row.get("source"))},
  {_sql_escape(row.get("failure_bucket"))}
)
""",
        )


def _persist_run_summary(
    workspace: WorkspaceClient,
    warehouse_id: str,
    runs_table: str,
    run_summary: dict[str, Any],
) -> None:
    _execute_sql(
        workspace,
        warehouse_id,
        f"""
INSERT INTO {runs_table} VALUES (
  {_sql_escape(run_summary.get("run_id"))},
  {_sql_escape(run_summary.get("timestamp"))},
  {_sql_escape(run_summary.get("mode"))},
  {_sql_escape(run_summary.get("dataset_name"))},
  {_sql_escape(run_summary.get("endpoint_name"))},
  {_sql_escape(run_summary.get("avg_score"))},
  {_sql_escape(json.dumps(run_summary.get("metrics") or {}, sort_keys=True))},
  {_sql_escape(run_summary.get("total_eval_rows"))},
  {_sql_escape(run_summary.get("total_failures"))},
  {_sql_escape(run_summary.get("infra_failures"))},
  {_sql_escape(run_summary.get("workspace_host"))}
)
""",
    )


def _persist_infra_failures(
    workspace: WorkspaceClient,
    warehouse_id: str,
    infra_table: str,
    run_id: str,
    timestamp: str,
    failures: list[InfraFailure],
) -> None:
    for failure in failures:
        _execute_sql(
            workspace,
            warehouse_id,
            f"""
INSERT INTO {infra_table} VALUES (
  {_sql_escape(run_id)},
  {_sql_escape(timestamp)},
  {_sql_escape(failure.question_id)},
  {_sql_escape(failure.question)},
  {_sql_escape(failure.source)},
  {_sql_escape(failure.reason)},
  {_sql_escape(failure.trace_id)}
)
""",
        )


def _apply_monitoring_sql(
    workspace: WorkspaceClient,
    warehouse_id: str,
    monitoring_sql_path: Path,
    results_table: str,
    runs_table: str,
    infra_table: str,
) -> None:
    sql_text = monitoring_sql_path.read_text(encoding="utf-8")
    sql_text = (
        sql_text.replace("__EVAL_RESULTS_TABLE__", results_table)
        .replace("__EVAL_RUNS_TABLE__", runs_table)
        .replace("__EVAL_INFRA_TABLE__", infra_table)
    )
    for statement in [s.strip() for s in sql_text.split(";") if s.strip()]:
        _execute_sql(workspace, warehouse_id, statement)


def _build_failure_markdown(rows: list[dict[str, Any]], infra_failures: list[InfraFailure]) -> str:
    failing = [row for row in rows if row.get("failure_bucket") not in {"pass"}]
    bucket_counts: dict[str, int] = {}
    for row in failing:
        bucket = str(row.get("failure_bucket") or "format")
        bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1

    top_items = sorted(
        failing,
        key=lambda r: (
            r.get("failure_bucket") != "routing",
            r.get("score") is not None and r.get("score") > 0,
            str(r.get("question_id") or ""),
        ),
    )[:10]

    lines = [
        "# Invisalign MAS Monitoring - Top 10 Fix Opportunities",
        "",
        "## Failure Bucket Counts",
        "",
    ]
    if bucket_counts:
        for bucket, count in sorted(bucket_counts.items(), key=lambda kv: kv[1], reverse=True):
            lines.append(f"- **{bucket}**: {count}")
    else:
        lines.append("- No quality failures found.")
    lines.extend(["", "## Top Fix Opportunities", ""])
    if top_items:
        for item in top_items:
            lines.append(
                "- "
                f"`{item.get('question_id')}` [{item.get('failure_bucket')}] "
                f"{item.get('scorer_name')}: {item.get('question')}"
            )
    else:
        lines.append("- No failing rows in this run.")
    lines.extend(["", "## Infra Failures", ""])
    if infra_failures:
        for failure in infra_failures[:25]:
            lines.append(
                f"- `{failure.question_id}` ({failure.source}) {failure.reason} :: {failure.question}"
            )
    else:
        lines.append("- No infra failures recorded.")
    return "\n".join(lines)


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO))

    if not Path(args.seed_path).exists():
        raise FileNotFoundError(f"Seed file not found: {args.seed_path}")

    mlflow.set_tracking_uri("databricks")
    if args.experiment_path.isdigit():
        experiment = mlflow.set_experiment(experiment_id=args.experiment_path)
    else:
        experiment = mlflow.set_experiment(args.experiment_path)
    experiment_id = str(experiment.experiment_id)

    workspace = WorkspaceClient()
    trace_experiment_ids = _resolve_trace_experiment_ids(experiment_id, args.trace_experiment_ids)
    traces_df = pd.DataFrame()
    try:
        traces_df = mlflow.search_traces(
            experiment_ids=trace_experiment_ids,
            max_results=args.trace_max_results,
            order_by=["timestamp_ms DESC"],
            return_type="pandas",
        )
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning(
            "Trace ingestion unavailable for experiment(s) %s; continuing seed-only. reason=%s",
            ",".join(trace_experiment_ids),
            exc,
        )
    trace_candidates, trace_infra_failures = _extract_trace_candidates(traces_df)

    seed_records = _load_seed_records(Path(args.seed_path), mode=args.mode)
    trace_records = _build_trace_eval_records(trace_candidates, sample_size=args.sample_size)
    eval_records = seed_records + trace_records
    dataset, dataset_error = _create_or_update_dataset(
        dataset_name=args.dataset_name,
        experiment_id=experiment_id,
        records=eval_records,
    )

    settings = AppSettings(
        supervisor_endpoint_name=args.supervisor_endpoint,
        request_timeout_s=args.request_timeout_s,
    )
    client = DatabricksSupervisorClient(settings)
    context = EvalContext(
        endpoint_name=args.supervisor_endpoint,
        mode=args.mode,
        run_started_utc=datetime.now(UTC).isoformat(),
        infra_failures=list(trace_infra_failures),
    )
    predict_fn = _make_predict_fn(context=context, client=client, invoke_retries=args.invoke_retries)
    scorers, scorer_names = _build_scorers(judge_model=args.judge_model)
    _register_scorers_if_possible(scorers)

    if args.dry_run:
        dry_summary = {
            "dry_run": True,
            "experiment_id": experiment_id,
            "dataset_name": args.dataset_name,
            "dataset_id": getattr(dataset, "dataset_id", None),
            "seed_rows": len(seed_records),
            "trace_rows": len(trace_records),
            "trace_candidates_seen": len(trace_candidates),
            "infra_failures_seeded": len(context.infra_failures),
            "scorers": scorer_names,
            "dataset_error": dataset_error,
        }
        print(json.dumps(dry_summary, indent=2))
        return

    with mlflow.start_run(run_name=f"invisalign_mas_monitoring_{args.mode}") as run:
        results = mlflow.genai.evaluate(
            data=eval_records,
            predict_fn=predict_fn,
            scorers=scorers,
        )
        metrics = dict(results.metrics or {})
        eval_df = (results.tables or {}).get("eval_results")
        flattened_rows = _flatten_eval_results(
            eval_df=eval_df,
            run_id=results.run_id,
            scorer_names=scorer_names,
            mode=args.mode,
            dataset_name=args.dataset_name,
        )

        avg_score = statistics.mean(
            [float(v) for k, v in metrics.items() if isinstance(v, (int, float)) and k.endswith("/mean")]
        ) if metrics else 0.0
        failures_count = len([row for row in flattened_rows if row.get("failure_bucket") != "pass"])
        run_ts = datetime.now(UTC).isoformat()
        summary = {
            "run_id": results.run_id,
            "timestamp": run_ts,
            "mode": args.mode,
            "dataset_name": args.dataset_name,
            "dataset_id": getattr(dataset, "dataset_id", None),
            "dataset_version": "",
            "endpoint_name": args.supervisor_endpoint,
            "seed_rows": len(seed_records),
            "trace_rows": len(trace_records),
            "trace_candidates_seen": len(trace_candidates),
            "total_eval_rows": len(flattened_rows),
            "total_failures": failures_count,
            "infra_failures": len(context.infra_failures),
            "avg_score": round(avg_score, 4),
            "metrics": metrics,
            "workspace_host": _base_url(workspace),
            "monitoring_tables_persisted": bool(args.warehouse_id),
            "dataset_error": dataset_error,
        }
        fix_markdown = _build_failure_markdown(flattened_rows, context.infra_failures)

        mlflow.log_text(
            json.dumps(
                [failure.__dict__ for failure in context.infra_failures],
                indent=2,
            ),
            "monitoring/infra_failures.json",
        )
        mlflow.log_text(json.dumps(summary, indent=2), "monitoring/summary.json")
        mlflow.log_text(fix_markdown, "monitoring/top_fix_opportunities.md")
        mlflow.log_param("eval_mode", args.mode)
        mlflow.log_param("supervisor_endpoint", args.supervisor_endpoint)
        mlflow.log_param("dataset_name", args.dataset_name)
        mlflow.log_param("dataset_id", getattr(dataset, "dataset_id", ""))
        mlflow.log_param("trace_experiment_ids", ",".join(trace_experiment_ids))
        mlflow.log_metric("monitoring/total_eval_rows", len(flattened_rows))
        mlflow.log_metric("monitoring/total_failures", failures_count)
        mlflow.log_metric("monitoring/infra_failures", len(context.infra_failures))
        mlflow.log_metric("monitoring/avg_score", float(summary["avg_score"]))

        if args.warehouse_id:
            _persist_tables(
                workspace=workspace,
                warehouse_id=args.warehouse_id,
                results_table=args.results_table,
                runs_table=args.runs_table,
                infra_table=args.infra_table,
            )
            _persist_eval_rows(
                workspace=workspace,
                warehouse_id=args.warehouse_id,
                results_table=args.results_table,
                rows=flattened_rows,
            )
            _persist_run_summary(
                workspace=workspace,
                warehouse_id=args.warehouse_id,
                runs_table=args.runs_table,
                run_summary=summary,
            )
            _persist_infra_failures(
                workspace=workspace,
                warehouse_id=args.warehouse_id,
                infra_table=args.infra_table,
                run_id=results.run_id,
                timestamp=run_ts,
                failures=context.infra_failures,
            )
            if args.apply_monitoring_sql:
                _apply_monitoring_sql(
                    workspace=workspace,
                    warehouse_id=args.warehouse_id,
                    monitoring_sql_path=Path(args.monitoring_sql_path),
                    results_table=args.results_table,
                    runs_table=args.runs_table,
                    infra_table=args.infra_table,
                )

        print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
