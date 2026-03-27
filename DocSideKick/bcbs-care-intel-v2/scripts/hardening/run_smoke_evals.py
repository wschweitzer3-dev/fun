from __future__ import annotations

import argparse
import json
import os
import re
import time
import uuid
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import requests
from databricks.sdk import WorkspaceClient


CATALOG = os.getenv("UC_CATALOG", "main")
SCHEMA = os.getenv("UC_SCHEMA", "hls_payer_demo")
WAREHOUSE_ID = os.getenv("WAREHOUSE_ID", "1e1f63a1a14d1f34")
GENIE_SPACE_ID = os.getenv("GENIE_SPACE_ID", "01f126e12bda187fb0fa9f49d1c6e585")
KA_TILE_ID = os.getenv("KA_TILE_ID", "c7630b94-d330-49bd-8d4c-0b6e3a2d6a03")
MAS_ENDPOINT = os.getenv("SUPERVISOR_ENDPOINT_NAME", "mas-30532bb0-endpoint")
APP_BASE_URL = os.getenv("APP_BASE_URL", "").strip().rstrip("/")
APP_AUTH_TOKEN = os.getenv("APP_AUTH_TOKEN", "").strip()
REQUEST_TIMEOUT_S = int(os.getenv("REQUEST_TIMEOUT_S", "50"))
SQL_RETRY_COUNT = int(os.getenv("SQL_RETRY_COUNT", "4"))
SQL_RETRY_SLEEP_S = float(os.getenv("SQL_RETRY_SLEEP_S", "2.0"))
ENDPOINT_RETRY_COUNT = int(os.getenv("ENDPOINT_RETRY_COUNT", "2"))

SMOKE_RESULTS_TABLE = f"{CATALOG}.{SCHEMA}.agent_eval_smoke_results"
SMOKE_SUMMARY_TABLE = f"{CATALOG}.{SCHEMA}.agent_eval_run_summary"
DOC_ID_PATTERN = re.compile(r"\bdoc[_:\- ]?id\b|\bDOC_[A-Z0-9_]+\b", re.IGNORECASE)
META_SUPERVISOR_PATTERN = re.compile(r"i[' ]?m a supervisor agent|internal orchestration", re.IGNORECASE)
DEFAULT_SNAPSHOT_PATH = Path("artifacts") / "smoke_last_run.json"


@dataclass
class SmokeTest:
    layer: str
    test_id: str
    question: str
    routing_label: str
    must_pass: bool
    expected_terms: tuple[str, ...] = ()


@dataclass
class SmokeResult:
    run_ts: str
    run_id: str
    layer: str
    test_id: str
    question: str
    passed: bool
    latency_ms: int
    error: str
    response_excerpt: str
    routing_label: str
    must_pass: bool


GENIE_TESTS = [
    SmokeTest(
        "genie",
        "genie_01",
        "Find diabetic members who haven’t had an A1C test in the last 6 months.",
        "structured_gap_query",
        True,
        ("gap", "a1c", "member"),
    ),
    SmokeTest(
        "genie",
        "genie_02",
        "Show high-risk members with open gaps in care and their plan type.",
        "structured_high_risk_gap",
        False,
        ("risk", "gap", "plan"),
    ),
    SmokeTest(
        "genie",
        "genie_03",
        "What is total claim cost for non-compliant diabetic members?",
        "structured_cost",
        False,
        ("cost",),
    ),
    SmokeTest(
        "genie",
        "genie_04",
        "Trend monthly claim cost for diagnosis code E11 over the last 12 months.",
        "structured_trend",
        False,
        ("e11", "cost"),
    ),
    SmokeTest(
        "genie",
        "genie_05",
        "Compare compliant vs non-compliant diabetic cohorts by average risk score and member count.",
        "structured_cohort_compare",
        False,
        ("risk", "count"),
    ),
]

KA_TESTS = [
    SmokeTest("ka", "ka_01", "Why are members missing A1C tests?", "unstructured_barriers", False, ("barrier",)),
    SmokeTest("ka", "ka_02", "What barriers are care managers seeing?", "unstructured_themes", False, ("care", "barrier")),
    SmokeTest("ka", "ka_03", "What evidence shows transportation or cost-sharing barriers?", "unstructured_evidence", False, ("transport", "cost")),
    SmokeTest("ka", "ka_04", "What are the clinical guidelines for diabetes A1C follow-up?", "unstructured_guidelines", False, ("guideline", "a1c")),
    SmokeTest("ka", "ka_05", "What interventions are recommended for overdue diabetic members?", "unstructured_actions", False, ("intervention", "recommend")),
]

MAS_TESTS = [
    SmokeTest("mas", "mas_01", "Show high-risk members with open gaps in care by plan type.", "structured_only", False, ("gap", "risk")),
    SmokeTest("mas", "mas_02", "What barriers are care managers seeing for missed A1C testing?", "unstructured_only", False, ("barrier", "a1c")),
    SmokeTest(
        "mas",
        "mas_03",
        "Which diabetic members are overdue for A1C, why are they missing care, and what should we do?",
        "blended",
        True,
        ("cohort", "barrier", "action"),
    ),
    SmokeTest("mas", "mas_04", "What is total cost for non-compliant diabetic members?", "structured_cost", False, ("cost",)),
    SmokeTest("mas", "mas_05", "Explain this BCBS payer dataset in plain English.", "dataset_overview", False, ("structured", "unstructured")),
]

APP_TESTS = [
    SmokeTest("app", "app_01", "Explain the dataset.", "app_contract", True, ("structured",)),
    SmokeTest("app", "app_02", "Which diabetic members are missing A1C tests and why?", "app_blended", False, ("a1c",)),
    SmokeTest("app", "app_03", "Show high-risk members with open gaps in care.", "app_structured", False, ("gap",)),
    SmokeTest("app", "app_04", "What barriers are care managers seeing?", "app_unstructured", False, ("barrier",)),
    SmokeTest(
        "app",
        "app_05",
        "Which diabetic members are overdue for A1C, why are they missing care, and what should we do?",
        "app_wow",
        True,
        ("cohort", "barrier", "action"),
    ),
]


def _iso_now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _workspace_client() -> WorkspaceClient:
    try:
        return WorkspaceClient()
    except Exception:
        host = os.getenv("DATABRICKS_HOST")
        token = os.getenv("DATABRICKS_TOKEN")
        if host and token:
            return WorkspaceClient(host=host, token=token)
        return WorkspaceClient(profile="DEFAULT")


def _ka_endpoint_name(tile_id: str) -> str:
    return f"ka-{tile_id.split('-')[0]}-endpoint"


def _base_url(workspace: WorkspaceClient) -> str:
    host = (workspace.config.host or "").strip().rstrip("/")
    if host.startswith("https://") or host.startswith("http://"):
        return host
    return f"https://{host}"


def _headers(workspace: WorkspaceClient) -> dict[str, str]:
    headers = workspace.config.authenticate()
    headers["Content-Type"] = "application/json"
    return headers


def _safe_excerpt(text: str, limit: int = 400) -> str:
    normalized = " ".join((text or "").split())
    return normalized[:limit]


def _extract_assistant_text(payload: Any) -> str:
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
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                text = block["text"].strip()
                if text:
                    return text
    return ""


def _extract_doc_sources(payload: Any) -> list[str]:
    sources: list[str] = []

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                lowered = key.lower()
                if lowered in {"sources", "citations"} and isinstance(value, list):
                    for item in value:
                        if isinstance(item, str):
                            sources.append(item)
                        elif isinstance(item, dict):
                            for f in ("doc_id", "id", "source", "title", "url"):
                                v = item.get(f)
                                if isinstance(v, str):
                                    sources.append(v)
                if lowered == "doc_id" and isinstance(value, str):
                    sources.append(value)
                visit(value)
        elif isinstance(node, list):
            for item in node:
                visit(item)

    visit(payload)
    return sources


def _contains_terms(text: str, terms: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return all(term.lower() in lowered for term in terms)


def _post_sql_statement(
    workspace: WorkspaceClient,
    warehouse_id: str,
    statement: str,
    timeout_s: int = 180,
) -> None:
    base = _base_url(workspace)
    headers = _headers(workspace)
    create = None
    last_error: Exception | None = None
    for attempt in range(1, SQL_RETRY_COUNT + 1):
        try:
            create = requests.post(
                f"{base}/api/2.0/sql/statements",
                headers=headers,
                json={
                    "warehouse_id": warehouse_id,
                    "statement": statement,
                    "wait_timeout": "10s",
                    "on_wait_timeout": "CONTINUE",
                },
                timeout=20,
            )
            if create.status_code >= 400:
                raise RuntimeError(f"SQL create failed: {create.status_code} {create.text}")
            break
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt == SQL_RETRY_COUNT:
                raise RuntimeError(f"SQL create failed after {attempt} attempts: {exc}") from exc
            time.sleep(SQL_RETRY_SLEEP_S * attempt)
    if create is None:
        raise RuntimeError(f"SQL create failed before request dispatch: {last_error}")
    payload = create.json() if create.text else {}
    statement_id = payload.get("statement_id")
    if not statement_id:
        raise RuntimeError("SQL statement_id missing")
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        status_resp = None
        for attempt in range(1, SQL_RETRY_COUNT + 1):
            try:
                status_resp = requests.get(
                    f"{base}/api/2.0/sql/statements/{statement_id}",
                    headers=headers,
                    timeout=20,
                )
                if status_resp.status_code >= 400:
                    raise RuntimeError(f"SQL poll failed: {status_resp.status_code} {status_resp.text}")
                break
            except Exception as exc:  # noqa: BLE001
                if attempt == SQL_RETRY_COUNT:
                    raise RuntimeError(f"SQL poll failed after {attempt} attempts: {exc}") from exc
                time.sleep(SQL_RETRY_SLEEP_S * attempt)
        if status_resp is None:
            raise RuntimeError("SQL polling response missing")
        status_payload = status_resp.json() if status_resp.text else {}
        state = (status_payload.get("status", {}) or {}).get("state", "UNKNOWN")
        if state == "SUCCEEDED":
            return
        if state in {"FAILED", "CANCELED", "CLOSED"}:
            err = ((status_payload.get("status", {}) or {}).get("error", {}) or {}).get("message", "SQL failed")
            raise RuntimeError(f"SQL failed with state={state}: {err}")
        time.sleep(1.5)
    raise TimeoutError("Timed out waiting for SQL statement")


def _ensure_eval_tables(workspace: WorkspaceClient) -> None:
    ddl_results = f"""
CREATE TABLE IF NOT EXISTS {SMOKE_RESULTS_TABLE} (
  run_ts TIMESTAMP,
  run_id STRING,
  layer STRING,
  test_id STRING,
  question STRING,
  pass BOOLEAN,
  latency_ms BIGINT,
  error STRING,
  response_excerpt STRING,
  routing_label STRING,
  must_pass BOOLEAN
) USING DELTA
"""
    ddl_summary = f"""
CREATE TABLE IF NOT EXISTS {SMOKE_SUMMARY_TABLE} (
  run_ts TIMESTAMP,
  run_id STRING,
  layer STRING,
  total_tests INT,
  passed_tests INT,
  pass_rate DOUBLE,
  p95_latency_ms DOUBLE,
  must_pass_ok BOOLEAN
) USING DELTA
"""
    _post_sql_statement(workspace, WAREHOUSE_ID, ddl_results)
    _post_sql_statement(workspace, WAREHOUSE_ID, ddl_summary)


def _escape_sql(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "''")


def _insert_results(workspace: WorkspaceClient, results: list[SmokeResult]) -> None:
    values = []
    for row in results:
        values.append(
            "("
            f"to_timestamp('{_escape_sql(row.run_ts)}'),"
            f"'{_escape_sql(row.run_id)}',"
            f"'{_escape_sql(row.layer)}',"
            f"'{_escape_sql(row.test_id)}',"
            f"'{_escape_sql(row.question)}',"
            f"{'true' if row.passed else 'false'},"
            f"{row.latency_ms},"
            f"'{_escape_sql(row.error)}',"
            f"'{_escape_sql(row.response_excerpt)}',"
            f"'{_escape_sql(row.routing_label)}',"
            f"{'true' if row.must_pass else 'false'}"
            ")"
        )
    insert_sql = f"INSERT INTO {SMOKE_RESULTS_TABLE} VALUES {','.join(values)}"
    _post_sql_statement(workspace, WAREHOUSE_ID, insert_sql)


def _p95(values: list[int]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    index = int(round(0.95 * (len(ordered) - 1)))
    return float(ordered[min(index, len(ordered) - 1)])


def _insert_summary(workspace: WorkspaceClient, run_id: str, run_ts: str, results: list[SmokeResult]) -> None:
    rows = []
    by_layer: dict[str, list[SmokeResult]] = {}
    for item in results:
        by_layer.setdefault(item.layer, []).append(item)
    by_layer["overall"] = results

    for layer, layer_rows in by_layer.items():
        total = len(layer_rows)
        passed = sum(1 for r in layer_rows if r.passed)
        pass_rate = float(passed) / float(total) if total else 0.0
        p95_ms = _p95([r.latency_ms for r in layer_rows if r.latency_ms >= 0])
        must_pass_ok = all(r.passed for r in layer_rows if r.must_pass)
        rows.append(
            "("
            f"to_timestamp('{_escape_sql(run_ts)}'),"
            f"'{_escape_sql(run_id)}',"
            f"'{_escape_sql(layer)}',"
            f"{total},"
            f"{passed},"
            f"{pass_rate},"
            f"{p95_ms},"
            f"{'true' if must_pass_ok else 'false'}"
            ")"
        )
    sql = f"INSERT INTO {SMOKE_SUMMARY_TABLE} VALUES {','.join(rows)}"
    _post_sql_statement(workspace, WAREHOUSE_ID, sql)


def _save_snapshot(path: Path, results: list[SmokeResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "results": [row.__dict__ for row in results],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _load_snapshot(path: Path) -> list[SmokeResult]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("results", [])
    loaded: list[SmokeResult] = []
    for row in rows:
        loaded.append(
            SmokeResult(
                run_ts=str(row["run_ts"]),
                run_id=str(row["run_id"]),
                layer=str(row["layer"]),
                test_id=str(row["test_id"]),
                question=str(row["question"]),
                passed=bool(row["passed"]),
                latency_ms=int(row["latency_ms"]),
                error=str(row.get("error", "")),
                response_excerpt=str(row.get("response_excerpt", "")),
                routing_label=str(row.get("routing_label", "")),
                must_pass=bool(row.get("must_pass", False)),
            )
        )
    return loaded


def _invoke_endpoint(workspace: WorkspaceClient, endpoint_name: str, message: str) -> tuple[dict[str, Any], int]:
    last_error: Exception | None = None
    for attempt in range(1, ENDPOINT_RETRY_COUNT + 1):
        start = time.perf_counter()
        try:
            response = requests.post(
                f"{_base_url(workspace)}/serving-endpoints/{endpoint_name}/invocations",
                headers=_headers(workspace),
                json={"input": [{"role": "user", "content": message}]},
                timeout=REQUEST_TIMEOUT_S,
            )
            latency_ms = int((time.perf_counter() - start) * 1000)
            if response.status_code >= 400:
                raise RuntimeError(f"[{response.status_code}] {response.text[:1000]}")
            return (response.json() if response.text else {}), latency_ms
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt == ENDPOINT_RETRY_COUNT:
                break
            time.sleep(1.5 * attempt)
    raise RuntimeError(str(last_error) if last_error else "endpoint invocation failed")


def _run_genie_test(workspace: WorkspaceClient, test: SmokeTest, run_id: str, run_ts: str) -> SmokeResult:
    try:
        start = time.perf_counter()
        result = workspace.genie.start_conversation_and_wait(
            space_id=GENIE_SPACE_ID,
            content=test.question,
            timeout=timedelta(seconds=REQUEST_TIMEOUT_S),
        )
        latency_ms = int((time.perf_counter() - start) * 1000)
        status = str(result.status.value) if result.status else "UNKNOWN"
        sql_text = ""
        text_resp = ""
        row_count = 0
        for att in (result.attachments or []):
            if att.query:
                sql_text = att.query.query or sql_text
                if att.query.query_result_metadata and att.query.query_result_metadata.row_count is not None:
                    row_count = int(att.query.query_result_metadata.row_count)
            if att.text and att.text.content:
                text_resp = att.text.content
        text_for_checks = f"{sql_text}\n{text_resp}".strip()
        passed = (
            status == "COMPLETED"
            and bool(sql_text.strip())
            and row_count > 0
            and (not test.expected_terms or _contains_terms(text_for_checks, test.expected_terms))
        )
        return SmokeResult(
            run_ts=run_ts,
            run_id=run_id,
            layer=test.layer,
            test_id=test.test_id,
            question=test.question,
            passed=passed,
            latency_ms=latency_ms,
            error="" if passed else f"status={status}, row_count={row_count}",
            response_excerpt=_safe_excerpt(text_for_checks),
            routing_label=test.routing_label,
            must_pass=test.must_pass,
        )
    except Exception as exc:  # noqa: BLE001
        return SmokeResult(run_ts, run_id, test.layer, test.test_id, test.question, False, -1, str(exc), "", test.routing_label, test.must_pass)


def _run_ka_test(workspace: WorkspaceClient, test: SmokeTest, run_id: str, run_ts: str) -> SmokeResult:
    try:
        payload, latency_ms = _invoke_endpoint(workspace, _ka_endpoint_name(KA_TILE_ID), test.question)
        text = _extract_assistant_text(payload)
        sources = _extract_doc_sources(payload)
        has_citation = bool(sources) or bool(DOC_ID_PATTERN.search(text)) or any(DOC_ID_PATTERN.search(src) for src in sources)
        passed = len(text) >= 60 and has_citation and (not test.expected_terms or _contains_terms(text, test.expected_terms))
        return SmokeResult(
            run_ts=run_ts,
            run_id=run_id,
            layer=test.layer,
            test_id=test.test_id,
            question=test.question,
            passed=passed,
            latency_ms=latency_ms,
            error="" if passed else "missing citation or too short",
            response_excerpt=_safe_excerpt(text),
            routing_label=test.routing_label,
            must_pass=test.must_pass,
        )
    except Exception as exc:  # noqa: BLE001
        return SmokeResult(run_ts, run_id, test.layer, test.test_id, test.question, False, -1, str(exc), "", test.routing_label, test.must_pass)


def _run_mas_test(workspace: WorkspaceClient, test: SmokeTest, run_id: str, run_ts: str) -> SmokeResult:
    try:
        payload, latency_ms = _invoke_endpoint(workspace, MAS_ENDPOINT, test.question)
        text = _extract_assistant_text(payload)
        tool_calls = sum(
            1 for item in (payload.get("output") if isinstance(payload, dict) else []) if isinstance(item, dict) and item.get("type") == "function_call"
        )
        passed = (
            len(text) >= 60
            and tool_calls <= 2
            and not META_SUPERVISOR_PATTERN.search(text)
            and (not test.expected_terms or _contains_terms(text, test.expected_terms))
        )
        return SmokeResult(
            run_ts=run_ts,
            run_id=run_id,
            layer=test.layer,
            test_id=test.test_id,
            question=test.question,
            passed=passed,
            latency_ms=latency_ms,
            error="" if passed else f"tool_calls={tool_calls} or content mismatch",
            response_excerpt=_safe_excerpt(text),
            routing_label=test.routing_label,
            must_pass=test.must_pass,
        )
    except Exception as exc:  # noqa: BLE001
        return SmokeResult(run_ts, run_id, test.layer, test.test_id, test.question, False, -1, str(exc), "", test.routing_label, test.must_pass)


def _run_app_test(test: SmokeTest, run_id: str, run_ts: str) -> SmokeResult:
    if not APP_BASE_URL:
        return SmokeResult(
            run_ts,
            run_id,
            test.layer,
            test.test_id,
            test.question,
            False,
            -1,
            "APP_BASE_URL not set",
            "",
            test.routing_label,
            test.must_pass,
        )
    try:
        start = time.perf_counter()
        headers = {"Content-Type": "application/json"}
        if APP_AUTH_TOKEN:
            headers["Authorization"] = f"Bearer {APP_AUTH_TOKEN}"
        response = requests.post(
            f"{APP_BASE_URL}/api/chat",
            headers=headers,
            json={"message": test.question},
            timeout=REQUEST_TIMEOUT_S,
        )
        latency_ms = int((time.perf_counter() - start) * 1000)
        if response.status_code >= 400:
            raise RuntimeError(f"[{response.status_code}] {response.text[:1000]}")
        payload = response.json() if response.text else {}
        text = str(payload.get("response_text") or "")
        debug = payload.get("debug") if isinstance(payload, dict) else {}
        path = (debug or {}).get("path")
        error = (debug or {}).get("error")
        no_fallback_label = "demo fallback" not in text.lower()
        passed = (
            len(text) > 30
            and path in {"live", "cache_hit"}
            and no_fallback_label
            and (not error)
            and (not test.expected_terms or _contains_terms(text, test.expected_terms))
        )
        return SmokeResult(
            run_ts=run_ts,
            run_id=run_id,
            layer=test.layer,
            test_id=test.test_id,
            question=test.question,
            passed=passed,
            latency_ms=latency_ms,
            error="" if passed else f"path={path}, error={error}",
            response_excerpt=_safe_excerpt(text),
            routing_label=test.routing_label,
            must_pass=test.must_pass,
        )
    except Exception as exc:  # noqa: BLE001
        return SmokeResult(run_ts, run_id, test.layer, test.test_id, test.question, False, -1, str(exc), "", test.routing_label, test.must_pass)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run BCBS smoke evaluations.")
    parser.add_argument(
        "--resume-file",
        default="",
        help="Optional path to a previously saved smoke snapshot JSON file; when set, inserts prior results only.",
    )
    parser.add_argument(
        "--snapshot-file",
        default=str(DEFAULT_SNAPSHOT_PATH),
        help="Path used to persist latest run payload before SQL inserts.",
    )
    args = parser.parse_args()

    workspace = _workspace_client()
    snapshot_path = Path(args.snapshot_file)
    _ensure_eval_tables(workspace)
    if args.resume_file:
        results = _load_snapshot(Path(args.resume_file))
        if not results:
            raise RuntimeError(f"No results found in resume file: {args.resume_file}")
        run_ts = results[0].run_ts
        run_id = results[0].run_id
    else:
        run_ts = _iso_now()
        run_id = f"smoke-{uuid.uuid4()}"
        results = []
        for test in GENIE_TESTS:
            results.append(_run_genie_test(workspace, test, run_id, run_ts))
        for test in KA_TESTS:
            results.append(_run_ka_test(workspace, test, run_id, run_ts))
        for test in MAS_TESTS:
            results.append(_run_mas_test(workspace, test, run_id, run_ts))
        for test in APP_TESTS:
            results.append(_run_app_test(test, run_id, run_ts))
        _save_snapshot(snapshot_path, results)

    _insert_results(workspace, results)
    _insert_summary(workspace, run_id, run_ts, results)

    total = len(results)
    passed = sum(1 for r in results if r.passed)
    pass_rate = passed / total if total else 0.0
    must_pass_ok = all(r.passed for r in results if r.must_pass)
    latencies = [r.latency_ms for r in results if r.latency_ms >= 0]
    p95_latency = _p95(latencies) if latencies else -1

    report = {
        "run_id": run_id,
        "run_ts": run_ts,
        "total_tests": total,
        "passed": passed,
        "pass_rate": round(pass_rate, 4),
        "must_pass_ok": must_pass_ok,
        "p95_latency_ms": p95_latency,
        "thresholds": {"pass_rate": 0.90, "p95_latency_ms": 12000},
        "failed_tests": [
            {
                "layer": r.layer,
                "test_id": r.test_id,
                "latency_ms": r.latency_ms,
                "error": r.error,
            }
            for r in results
            if not r.passed
        ],
    }
    print(json.dumps(report, indent=2))

    if pass_rate < 0.90 or not must_pass_ok:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
