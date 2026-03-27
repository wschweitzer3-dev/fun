# Code Structure

## Root

- `app.yaml`: Databricks App runtime command and env vars
- `requirements.txt`: Python runtime dependencies
- `dist/ui/`: static frontend bundle served by FastAPI
- `src/bcbs_care_intelligence/backend/`: backend API and integrations
- `tests/backend/`: parser-focused backend unit tests
- `scripts/hardening/`: curation and evaluation automation for demo readiness

## Backend

- `config.py`
  - Centralized environment config and defaults.
- `models.py`
  - API schemas: request/response, charts, debug metadata, health diagnostics.
- `supervisor_client.py`
  - Direct invocation of `mas-30532bb0-endpoint`.
  - Applies system prompt and returns payload + latency + request id.
- `parser.py`
  - Extracts final assistant narrative.
  - Parses markdown tables from supervisor output.
  - Extracts citations/doc_ids and infers chart specs.
  - Includes dataset-overview shortcut helpers.
- `permission_checks.py`
  - Startup/admin diagnostics for Supervisor, Genie, warehouse, and UC table access checks.
  - Powers `/healthz` details.
- `router.py`
  - `/api/chat` + `/api/insights/snapshot` handlers.
  - 60s in-memory TTL cache for identical prompts.
  - Normalizes known high-latency intents before supervisor invocation.
  - Emits debug telemetry (`path`, `latency_ms`, `tool_call_count`, `latency_warning`).
- `main.py`
  - FastAPI app setup, static asset serving, startup checks, supervisor warm-up ping, and `/healthz`.

## Hardening Scripts

- `scripts/hardening/curate_agent_bricks.py`
  - Applies fixed 5-question Genie curation and KA/MAS example packs.
- `scripts/hardening/run_smoke_evals.py`
  - Runs 20 smoke tests across Genie/KA/MAS/App and persists detailed + summary metrics in Delta.
- `scripts/hardening/run_formal_mlflow_eval.py`
  - Creates/uses `genie_eval_set`, `ka_eval_set`, `mas_eval_set` and runs `mlflow.genai.evaluate()` with quality scorers.

## Frontend

- `dist/ui/index.html`
  - App shell with left nav and main content mount point.
- `dist/ui/assets/app.css`
  - BCBS-inspired design tokens and responsive layout styles.
- `dist/ui/assets/app.js`
  - Chat + insights pages
  - message rendering
  - sources/context metadata
  - sortable table
  - SVG chart renderer (bar/line/pie)

## Extending

### Add a chart type

1. Add enum value in `models.py` (`ChartType`).
2. Extend `infer_chart_spec` in `parser.py`.
3. Implement rendering branch in `dist/ui/assets/app.js` `renderChart()`.

### Add a new intelligence capability

1. Keep Supervisor as top-level path.
2. Add parsing logic in `parser.py` for new response structure.
3. Add optional UI sections in `app.js` only when data is present.
4. Do not add mock fallback in `/api/chat`.

## Troubleshooting

- Use `/healthz?refresh=true` to rerun permission checks.
- If chat fails, inspect right panel `Last error`.
- Validate:
  - Supervisor endpoint permissions (`CAN_QUERY`)
  - Genie (`CAN_RUN`)
  - warehouse (`CAN_USE`)
  - UC catalog/schema/table access.
