# Invisalign Growth Copilot (Databricks APX)

Chat-first Databricks app that serves the existing MAS endpoint:
- MAS endpoint: `mas-47342197-endpoint`
- Existing Genie space remains unchanged: `01f12ab84fe81e3690ea089379537b87`

The app is designed for rep usability:
- one primary action path (`Run analysis`)
- guided prompt chips
- normalized response contract
- dynamic visuals (animated KPI tiles + auto chart inference)

## API

### `POST /api/chat`
Input:
```json
{ "message": "Which providers should we target for Comprehensive expansion?" }
```

Output shape:
```json
{
  "response_text": "string",
  "data_table": [],
  "chart_spec": { "type": "bar|line|pie", "x": "...", "y": "...", "data": [] },
  "kpis": [{ "label": "...", "value": 0, "format": "number|currency|percent", "decimals": 0 }],
  "sources": ["..."],
  "notices": ["..."],
  "debug": {
    "supervisor_endpoint": "mas-47342197-endpoint",
    "latency_ms": 0,
    "approval_hops": 0,
    "total_round_trips": 1,
    "external_trend_status": "not_used|ok|unavailable",
    "error": null,
    "fallback_used": false
  }
}
```

### `GET /api/health`
Returns endpoint readiness and health metadata for the app shell.

## MCP auto-approval behavior

The backend auto-approves `mcp_approval_request` items for configured server labels (default: `will_you`).
If external tool calls fail, chat still returns a usable internal recommendation and surfaces a notice instead of failing the full request.

## Local development

```bash
cd /Users/will.schweitzer/Documents/Coding/fun-demo/invisalign-growth-copilot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
npm install
npm run build
export PYTHONPATH=src
uvicorn invisalign_growth_copilot.backend.main:app --reload --port 8000
```

## Databricks Apps deployment

Create app once:
```bash
databricks apps create invisalign-growth-copilot
```

Deploy:
```bash
databricks apps deploy invisalign-growth-copilot --source-code-path /Workspace/Users/will.schweitzer@databricks.com/fun-demo/invisalign-growth-copilot
```

If needed, grant query permission to endpoint resource for the app identity:
```bash
databricks serving-endpoints put-permissions mas-47342197-endpoint --json '{"access_control_list":[{"service_principal_name":"<app-spn>","permission_level":"CAN_QUERY"}]}'
```

## MLflow monitoring + debug evaluation (Invisalign MAS)

This repo includes a formal monitoring evaluator that combines:
- curated seed prompts (`scripts/hardening/data/invisalign_mas_seed_eval_set.json`)
- recent production traces sampled for diversity
- `mlflow.genai.evaluate()` with built-in scorers + optional LLM judges
- optional persistence to UC Delta tables for trend/debug dashboards

### Install eval dependencies

```bash
cd /Users/will.schweitzer/Documents/Coding/fun-demo/invisalign-growth-copilot
python3 -m venv .venv
source .venv/bin/activate
pip install -r scripts/hardening/requirements.txt
```

### Dry run (build dataset + scorers only)

```bash
export PYTHONPATH=src
python scripts/hardening/run_invisalign_mlflow_monitoring_eval.py \
  --experiment-path "/Users/will.schweitzer@databricks.com/invisalign-growth-copilot-evals" \
  --supervisor-endpoint "mas-47342197-endpoint" \
  --mode daily \
  --sample-size 24 \
  --dry-run
```

### Full run (monitoring mode)

```bash
export PYTHONPATH=src
python scripts/hardening/run_invisalign_mlflow_monitoring_eval.py \
  --experiment-path "/Users/will.schweitzer@databricks.com/invisalign-growth-copilot-evals" \
  --supervisor-endpoint "mas-47342197-endpoint" \
  --dataset-name "invisalign_mas_eval_set" \
  --mode weekly \
  --sample-size 80
```

### Persist to Delta + create monitoring views

```bash
export PYTHONPATH=src
python scripts/hardening/run_invisalign_mlflow_monitoring_eval.py \
  --experiment-path "/Users/will.schweitzer@databricks.com/invisalign-growth-copilot-evals" \
  --warehouse-id "<sql-warehouse-id>" \
  --results-table "main.invisalign_growth_eval.monitoring_eval_results" \
  --runs-table "main.invisalign_growth_eval.monitoring_eval_runs" \
  --infra-table "main.invisalign_growth_eval.monitoring_eval_infra_failures" \
  --apply-monitoring-sql
```

### Key env vars

- `MLFLOW_EXPERIMENT_PATH` (or pass `--experiment-path`)
- `SUPERVISOR_ENDPOINT_NAME` (or pass `--supervisor-endpoint`)
- `EVAL_DATASET_NAME`
- `EVAL_WAREHOUSE_ID`
- `EVAL_RESULTS_TABLE`, `EVAL_RUNS_TABLE`, `EVAL_INFRA_TABLE`
- `EVAL_JUDGE_MODEL` (optional; if missing/unavailable, judge scorers are skipped)
