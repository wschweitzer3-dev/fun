from __future__ import annotations

import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .models import HealthDiagnostics
from .permission_checks import BootstrapPermissionValidator
from .router import api, supervisor_client


app = FastAPI(
    title="BCBS Care Intelligence API",
    version="2.0.0",
    description="FastAPI backend for BCBS Care Intelligence with Supervisor-first intelligence.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(api)

validator = BootstrapPermissionValidator(settings)


@app.on_event("startup")
async def startup_event() -> None:
    # Run checks + endpoint warmup in a background thread so startup remains non-blocking.
    def _startup_tasks() -> None:
        validator.run_checks(force=True)
        supervisor_client.warmup_sync()

    threading.Thread(target=_startup_tasks, daemon=True).start()


@app.get("/healthz", response_model=HealthDiagnostics, include_in_schema=False)
async def healthz(refresh: bool = Query(default=False)) -> HealthDiagnostics:
    return validator.run_checks(force=refresh)


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DIST_UI_DIR = PROJECT_ROOT / "dist" / "ui"
ASSETS_DIR = DIST_UI_DIR / "assets"

if ASSETS_DIR.exists():
    app.mount("/assets", StaticFiles(directory=ASSETS_DIR), name="assets")


@app.get("/", include_in_schema=False)
async def root() -> FileResponse:
    index = DIST_UI_DIR / "index.html"
    if not index.exists():
        raise HTTPException(status_code=500, detail="Static UI bundle missing at dist/ui/index.html")
    return FileResponse(index)


@app.get("/{full_path:path}", include_in_schema=False)
async def spa_fallback(full_path: str) -> FileResponse:
    if full_path.startswith("api") or full_path.startswith("healthz"):
        raise HTTPException(status_code=404, detail="Route not found")
    return FileResponse(DIST_UI_DIR / "index.html")
