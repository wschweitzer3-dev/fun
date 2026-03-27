# BCBS Demo Hardening Scripts

These scripts harden the existing BCBS demo stack in place:

1. Curate Genie/KA/MAS sample questions and examples.
2. Run fast smoke evaluations and persist results to Delta tables.
3. Run formal MLflow GenAI evaluations for Genie/KA/MAS layers.

## Prerequisites

- Databricks authentication available to `databricks-sdk` (`WorkspaceClient()`).
- Access to:
  - Genie space `01f126e12bda187fb0fa9f49d1c6e585`
  - KA tile `c7630b94-d330-49bd-8d4c-0b6e3a2d6a03`
  - MAS endpoint `mas-30532bb0-endpoint`
  - SQL warehouse `1e1f63a1a14d1f34`
- For formal eval: `mlflow[databricks]` installed.

## Install Eval Dependencies

```bash
pip install -r scripts/hardening/requirements-eval.txt
```

## 1) Apply Curation

```bash
python scripts/hardening/curate_agent_bricks.py
```

## 2) Run Smoke Evals

Optional app API check:

```bash
export APP_BASE_URL="https://<your-app-url>"
```

Run:

```bash
python scripts/hardening/run_smoke_evals.py
```

Writes:

- `main.hls_payer_demo.agent_eval_smoke_results`
- `main.hls_payer_demo.agent_eval_run_summary`

Gate:

- Pass rate must be >= 90%
- All `must_pass=true` tests must pass

## 3) Run Formal MLflow Evals

Optional overrides:

```bash
export MLFLOW_EXPERIMENT_PATH="/Users/will.schweitzer@databricks.com/bcbs-care-intel-evals"
```

Run:

```bash
python scripts/hardening/run_formal_mlflow_eval.py
```

Produces:

- One MLflow run per layer (`genie`, `ka`, `mas`)
- Rollup run with:
  - `leaderboard.json`
  - `top_failure_patterns.md`
