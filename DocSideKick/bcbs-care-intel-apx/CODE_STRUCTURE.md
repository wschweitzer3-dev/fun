# Code Structure

This document describes the BCBS Care Intelligence APX app layout and how to extend it safely.

## Root

- `requirements.txt`: Python backend dependencies for Databricks App runtime.
- `package.json`: React/Vite frontend dependencies and scripts.
- `app.yaml`: Databricks App runtime command/env.
- `.env.example`: runtime configuration template.
- `README.md`: product and run/deploy guide.
- `CODE_STRUCTURE.md`: this file.

## Backend (`src/bcbs_care_intelligence/backend`)

- `main.py`
- FastAPI app initialization
- CORS configuration
- Router inclusion
- Optional static frontend serving from `dist/ui`

- `models.py`
- Typed request/response contracts:
  - `ChatRequest`
  - `ChatResponse`
  - `ChartSpec`
  - `InsightsSnapshot`

- `config.py`
- Environment-driven settings for supervisor/genie resolution and timeouts.

- `supervisor_client.py`
- Databricks authentication headers
- Supervisor endpoint resolution by MAS tile name
- Endpoint invocation with TTL caching

- `genie_client.py`
- Genie space resolution by title
- Conversation call wrapper
- Generic extraction of text/sql/rows from Genie response payload

- `router.py`
- API routes:
  - `POST /api/chat` (`chatWithSupervisor`)
  - `GET /api/insights/snapshot` (`getInsightsSnapshot`)
- Core behavior:
  - supervisor response parsing
  - optional Genie enrichment
  - chart inference
  - deterministic fallback selection

## Frontend (`src/bcbs_care_intelligence/ui`)

### Entry

- `index.html`: Vite entry HTML.
- `main.tsx`: React root, router, QueryClient provider.
- `App.tsx`: route registration and Suspense boundaries.

### Routes

- `routes/_sidebar/route.tsx`
- Left navigation shell with exact sections:
  - Chat
  - Member Insights
  - Population Trends

- `routes/_sidebar/chat.tsx`
- Main chat workflow:
  - message state
  - send mutation via `chatWithSupervisor`
  - loading bubble and error handling
  - right context panel

- `routes/_sidebar/insights.tsx`
- Insights and population trend page rendering from `getInsightsSnapshot`.

### Components

- `components/apx/ChatComposer.tsx`: bottom chat input and submit action.
- `components/apx/MessageStream.tsx`: animated user/system message stream + skeleton.
- `components/apx/ResponseRenderer.tsx`: text/table/chart/source renderer.
- `components/apx/SortableDataTable.tsx`: click-sort table for dynamic row sets.
- `components/apx/ChartRenderer.tsx`: dynamic `bar|line|pie` chart renderer (Recharts).

- `components/ui/table.tsx`, `components/ui/skeleton.tsx`, `components/ui/badge.tsx`
- lightweight shadcn-style primitives used by APX components.

### Styling and API

- `styles/theme.css`
- BCBS palette and layout rules:
  - Primary `#003A8F`
  - Secondary `#005EB8`
  - Background `#F7F9FC`
  - Text `#1A1A1A`
- Includes mobile responsiveness and reduced-motion handling.

- `lib/api.ts`
- Typed API models + hooks:
  - `useChatWithSupervisor()`
  - `useGetInsightsSnapshotSuspense()`

- `lib/selector.ts`, `lib/utils.ts`
- helper utilities for APX-like conventions and class composition.

## Tests

- `tests/backend/test_router.py`
- Focused tests for:
  - chart inference behavior
  - deterministic fallback mapping

## Generated vs Editable Boundaries

- This project does not currently include generated OpenAPI client files.
- All files in `src/bcbs_care_intelligence/ui/lib/` are editable in this implementation.
- If later integrated with Orval/TanStack route generation, treat generated files as read-only.

## Extending the App

1. Add new backend operation:
- Define typed model in `models.py`.
- Add route in `router.py` with explicit `operation_id`.
- Add hook in `ui/lib/api.ts`.

2. Add chart type:
- Extend `ChartType` in backend and frontend.
- Add renderer branch in `ChartRenderer.tsx`.
- Update chart inference logic in `router.py`.

3. Add new sidebar page:
- Create route component under `routes/_sidebar`.
- Register in `App.tsx`.
- Add nav entry in `routes/_sidebar/route.tsx`.

## Troubleshooting

- Supervisor endpoint not found:
- Set `SUPERVISOR_ENDPOINT_NAME` explicitly.

- Genie space resolution fails:
- Set `GENIE_SPACE_ID` explicitly.

- Live calls fail in deployment:
- Verify Databricks App service principal has access to:
  - `mas-30532bb0-endpoint`
  - `HLS Payer Structured Genie`
  - required SQL warehouse permissions.

- Frontend not served by FastAPI:
- Run `npm run build` to generate `dist/ui`.
