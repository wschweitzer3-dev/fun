from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .router import api

app = FastAPI(
    title="Invisalign Growth Copilot API",
    version="0.1.0",
    description=(
        "FastAPI backend for Invisalign MAS chat workflows. "
        "Provides normalized chat responses with inferred charts and KPI tiles."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(api)


@app.get("/healthz", include_in_schema=False)
async def healthcheck() -> JSONResponse:
    return JSONResponse({"status": "ok", "app_env": settings.app_env, "app_name": settings.app_name})


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DIST_UI_DIR = PROJECT_ROOT / "dist" / "ui"

if DIST_UI_DIR.exists():
    assets_dir = DIST_UI_DIR / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(DIST_UI_DIR / "index.html")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str) -> FileResponse:
        if full_path.startswith("api"):
            raise HTTPException(status_code=404, detail="Route not found")
        return FileResponse(DIST_UI_DIR / "index.html")

else:
    @app.get("/", include_in_schema=False)
    async def root() -> JSONResponse:
        return JSONResponse(
            {
                "message": (
                    "Invisalign Growth Copilot backend is running. "
                    "Build frontend assets with `npm run build` to serve UI from FastAPI."
                )
            }
        )
