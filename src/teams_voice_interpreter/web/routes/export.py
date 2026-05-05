"""会话导出端点。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response

from teams_voice_interpreter.session.exporter import ExportWindowExpiredError, export_markdown
from teams_voice_interpreter.session.manager import DEFAULT_MANAGER

router = APIRouter(prefix="/api", tags=["export"])


@router.post("/export")
async def export_session() -> Response:
    """导出当前会话 Markdown。"""
    try:
        content = export_markdown(DEFAULT_MANAGER)
    except ExportWindowExpiredError as error:
        raise HTTPException(status_code=410, detail="导出窗口已过期") from error
    return Response(content=content, media_type="text/markdown; charset=utf-8")
