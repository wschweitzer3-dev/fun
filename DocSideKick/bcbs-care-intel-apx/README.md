# BCBS Care Intelligence

BCBS Care Intelligence is a Databricks-native APX-style full-stack application (FastAPI + React) that provides a unified healthcare analytics copilot experience over:

- `HLS_Payer_Supervisor` for multi-agent orchestration
- `HLS Payer Structured Genie` for structured SQL analytics
- Knowledge Assistant-backed unstructured notes and guideline insights

The app is designed to feel like a production payer analytics product, not a demo dashboard.

## What It Does

1. Accepts conversational prompts through a premium chat UI.
2. Calls `HLS_Payer_Supervisor` via Databricks Model Serving.
3. Enriches analytics-heavy prompts with Genie tabular/SQL context when needed.
4. Returns:
- Natural language explanation
- Structured table output
- Dynamic chart render (`bar`, `line`, `pie`)
- Source citations
5. Falls back to deterministic mock responses for demo continuity if live systems are unavailable.

## Architecture

- Backend: FastAPI with typed Pydantic contracts and explicit `operation_id`s.
- Frontend: React with route shell + Suspense + skeleton states.
- Data integration:
- Supervisor endpoint invocation: `/serving-endpoints/{endpoint}/invocations`
- Genie enrichment: `start_conversation_and_wait(...)`

## API Contract

### `POST /api/chat` (`operation_id="chatWithSupervisor"`)

Request:

```json
{ "message": "Which diabetic members are missing A1C tests and why?" }
```

Response:

```json
{
  "response_text": "string",
  "data_table": [{ "col": "value" }],
  "chart_spec": { "type": "bar", "x": "condition", "y": "member_count", "data": [] },
  "sources": ["genie_space:..."],
  "debug": {
    "supervisor_endpoint": "mas-30532bb0-endpoint",
    "genie_space_id": "01f126e12bda187fb0fa9f49d1c6e585",
    "sql_text": "SELECT ...",
    "fallback_used": false
  }
}
```

### `GET /api/insights/snapshot` (`operation_id="getInsightsSnapshot"`)

Returns static executive cards + chart/table payload for Insights pages.

## Local Development

## 1) Backend

From project root:

```bash
cd /Users/will.schweitzer/Documents/Coding/fun-demo/DocSideKick/bcbs-care-intel-apx
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export PYTHONPATH=src
uvicorn bcbs_care_intelligence.backend.main:app --reload --port 8000
```

## 2) Frontend

In a second shell:

```bash
cd /Users/will.schweitzer/Documents/Coding/fun-demo/DocSideKick/bcbs-care-intel-apx
npm install
npm run dev
```

If backend is not at default origin, set:

```bash
VITE_API_BASE_URL=http://localhost:8000/api
```

## Environment Variables

Use `.env.example` as baseline:

- `SUPERVISOR_NAME=HLS_Payer_Supervisor`
- `SUPERVISOR_ENDPOINT_NAME` (optional hard override)
- `GENIE_SPACE_TITLE=HLS Payer Structured Genie`
- `GENIE_SPACE_ID` (optional hard override)
- `ENABLE_MOCK_FALLBACK=true`
- `REQUEST_TIMEOUT_S=120`

## Databricks App Deployment

This folder includes `app.yaml` for Databricks Apps runtime.

Recommended deploy target:

- App name: `bcbs-care-intel-dev`

Typical flow:

```bash
databricks apps create bcbs-care-intel-dev
databricks apps deploy bcbs-care-intel-dev --source-code-path /Workspace/Users/will.schweitzer@databricks.com/fun-demo/DocSideKick/bcbs-care-intel-apx
databricks apps get bcbs-care-intel-dev
databricks apps logs bcbs-care-intel-dev
```

## Executive Demo Prompts

- `Which diabetic members are missing A1C tests and why?`
- `Show high-risk members with open gaps and total spend impact.`
- `What barriers are care managers seeing for overdue A1C completion?`

Expected behavior: narrative + table + visualization + source context in one response.
