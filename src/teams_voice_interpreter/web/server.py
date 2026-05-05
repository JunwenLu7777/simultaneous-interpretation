"""本地 FastAPI Web 控制台。"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from teams_voice_interpreter.web.routes import control, export, status


def create_app() -> FastAPI:
    """创建仅本地使用的 FastAPI 应用。"""
    app = FastAPI(title="Teams Voice Interpreter", docs_url=None, redoc_url=None)
    app.include_router(control.router)
    app.include_router(status.router)
    app.include_router(export.router)
    static_dir = Path(__file__).with_name("static")
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    return app


app = create_app()
