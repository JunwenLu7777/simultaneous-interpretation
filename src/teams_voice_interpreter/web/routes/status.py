"""状态查询与 WebSocket 推送。"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket
from starlette.websockets import WebSocketDisconnect

from teams_voice_interpreter.session.manager import DEFAULT_MANAGER

router = APIRouter(tags=["status"])


@router.get("/api/status")
async def get_status() -> dict[str, object]:
    """返回状态面板快照。"""
    return DEFAULT_MANAGER.status_payload()


@router.websocket("/ws/status")
async def websocket_status(websocket: WebSocket) -> None:
    """以 5 Hz 推送状态面板事件。"""
    await websocket.accept()
    try:
        for _ in range(5):
            await websocket.send_json(DEFAULT_MANAGER.status_payload())
            await asyncio.sleep(0.2)
    except WebSocketDisconnect:
        return
    await websocket.close()
