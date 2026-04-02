# Code Structure

## Backend
- `src/invisalign_growth_copilot/backend/main.py`: FastAPI app + static SPA serving.
- `src/invisalign_growth_copilot/backend/router.py`: `/api/chat` and `/api/health` endpoints + response normalization.
- `src/invisalign_growth_copilot/backend/supervisor_client.py`: MAS invocation and MCP auto-approval loop.
- `src/invisalign_growth_copilot/backend/models.py`: Pydantic request/response contracts.
- `src/invisalign_growth_copilot/backend/config.py`: Runtime settings.
- `src/invisalign_growth_copilot/evaluation/monitoring.py`: Eval helper utilities (intent inference, diverse sampling, failure bucket mapping).

## Frontend
- `src/invisalign_growth_copilot/ui/App.tsx`: Route composition (`/chat`, `/about`).
- `src/invisalign_growth_copilot/ui/routes/_sidebar/chat.tsx`: Chat-first workflow.
- `src/invisalign_growth_copilot/ui/components/apx/ResponseRenderer.tsx`: recommendation + visuals + context zones.
- `src/invisalign_growth_copilot/ui/components/apx/ChartRenderer.tsx`: dynamic chart rendering.
- `src/invisalign_growth_copilot/ui/styles/theme.css`: Invisalign-inspired visual theme.

## Packaging
- `app.yaml`: Databricks Apps runtime command + env vars.
- `requirements.txt`: backend dependencies.
- `package.json`: frontend build dependencies.
- `dist/ui`: generated frontend assets served by FastAPI.

## Hardening / Evaluation
- `scripts/hardening/run_invisalign_mlflow_monitoring_eval.py`: formal monitoring evaluator (seed + traces + MLflow scorers/judges + optional Delta persistence).
- `scripts/hardening/data/invisalign_mas_seed_eval_set.json`: curated seed prompts for stable trend tracking.
- `scripts/hardening/sql/create_monitoring_views.sql`: SQL views for quality trend, failure rate, latency correlation, and weekly fix opportunities.
- `scripts/hardening/requirements.txt`: isolated dependencies for hardening/eval workflows.
