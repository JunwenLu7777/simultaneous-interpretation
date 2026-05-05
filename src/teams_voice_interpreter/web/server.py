"""本地 FastAPI Web 控制台。"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from teams_voice_interpreter.web.routes import control, export, status


def create_app() -> FastAPI:
    """创建仅本地使用的 FastAPI 应用。"""
    app = FastAPI(title="Teams Voice Interpreter", docs_url=None, redoc_url=None)
    app.include_router(control.router)
    app.include_router(status.router)
    app.include_router(export.router)
    static_dir = Path(__file__).with_name("static")

    @app.get("/", include_in_schema=False)
    async def web_console() -> FileResponse:
        """返回本地 Web 控制台入口页。"""
        return FileResponse(static_dir / "index.html")

    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    return app


app = create_app()
