from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api import router
from .config import get_settings
from .database import init_database
from .runner import init_runner


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    runner = init_runner(settings)
    await init_database()
    await runner.recover_interrupted()
    try:
        yield
    finally:
        await runner.shutdown()


app = FastAPI(title="操作手册生成器", version="0.1.0", lifespan=lifespan)
app.include_router(router)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


frontend = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if frontend.exists():
    app.mount("/assets", StaticFiles(directory=frontend / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        candidate = frontend / path
        return FileResponse(candidate if candidate.is_file() else frontend / "index.html")
