# BCBS Care Intelligence (v2)

BCBS Care Intelligence is a Databricks App with:
- FastAPI backend
- Static premium UI (`dist/ui`)
- Supervisor-first intelligence via `mas-30532bb0-endpoint`

This rebuild intentionally removes demo fallback behavior and relies on one live intelligent path.

## Architecture

- **Frontend**: static HTML/CSS/JS served by FastAPI (no runtime npm/bun build step)
- **Backend**:
  - `POST /api/chat` (`chatWithSupervisor`)
  - `GET /api/insights/snapshot` (`getInsightsSnapshot`)
  - `GET /healthz`
- **Intelligence layer**: `HLS_Payer_Supervisor` endpoint invocation

## Core Behavior

- Chat sends user prompt to Supervisor endpoint.
- Backend parses final assistant message from Supervisor output.
- If markdown table output is present, backend normalizes it as `data_table`.
- Backend infers `chart_spec` (bar/line/pie) from rows when possible.
- UI renders narrative first, then table, then chart.
- Errors are explicit and visible in the right metadata panel (`Last error`).
- Backend includes a 60s TTL response cache for repeated prompts and exposes debug telemetry:
  - `path` (`live` or `cache_hit`)
  - `latency_ms`
  - `tool_call_count`
  - `latency_warning`

## Required Environment

Configured in `app.yaml`:
- `SUPERVISOR_ENDPOINT_NAME=mas-30532bb0-endpoint`
- `SUPERVISOR_NAME=HLS_Payer_Supervisor`
- `GENIE_SPACE_TITLE=HLS Payer Structured Genie`
- `GENIE_SPACE_ID=01f126e12bda187fb0fa9f49d1c6e585`
- `WAREHOUSE_ID=1e1f63a1a14d1f34`
- `REQUEST_TIMEOUT_S=50`
- `ENABLE_MOCK_FALLBACK=false`
- `PYTHONPATH=src`

## Deploy

```bash
databricks workspace import-dir \
  /Users/will.schweitzer/Documents/Coding/fun-demo/DocSideKick/bcbs-care-intel-v2 \
  /Workspace/Users/will.schweitzer@databricks.com/fun-demo/DocSideKick/bcbs-care-intel-v2 \
  --overwrite

databricks apps create bcbs-care-intel-v2-dev

databricks apps deploy bcbs-care-intel-v2-dev \
  --source-code-path /Workspace/Users/will.schweitzer@databricks.com/fun-demo/DocSideKick/bcbs-care-intel-v2
```

## Smoke Prompts

1. `Explain the dataset`
2. `Which diabetic members are missing A1C tests and why?`
3. `Show top diagnosis categories by claim cost`
4. `What barriers are care managers seeing most often?`
5. `Summarize high-risk members with open gaps and recommended next actions`

Expected behavior:
- No "Demo fallback" labels
- No duplicate assistant text
- Metadata panel shows endpoint/latency/request id/error

## Demo Hardening Utilities

Scripts in `scripts/hardening` support curation and evaluation:

- `curate_agent_bricks.py`: Replaces Genie sample questions and applies KA/MAS examples + MAS instructions.
- `run_smoke_evals.py`: Executes 20 smoke checks (Genie/KA/MAS/App), writes:
  - `main.hls_payer_demo.agent_eval_smoke_results`
  - `main.hls_payer_demo.agent_eval_run_summary`
- `run_formal_mlflow_eval.py`: Runs MLflow GenAI formal evaluation suites for Genie/KA/MAS and logs leaderboard/failure artifacts.
