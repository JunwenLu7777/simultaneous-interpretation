"""Web 控制端点。"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from teams_voice_interpreter.data.audio_segment import AudioDirection
from teams_voice_interpreter.data.session import InvalidSessionTransitionError
from teams_voice_interpreter.session.manager import DEFAULT_MANAGER

router = APIRouter(prefix="/api/control", tags=["control"])


@router.post("/start")
async def start() -> dict[str, object]:
    """启动会话并跑通双向模拟管线。"""
    try:
        DEFAULT_MANAGER.start()
    except OSError as error:
        raise HTTPException(status_code=409, detail="已有会话正在运行") from error
    await asyncio.gather(
        DEFAULT_MANAGER.run_pipeline(direction=AudioDirection.UPLINK),
        DEFAULT_MANAGER.run_pipeline(direction=AudioDirection.DOWNLINK),
    )
    return DEFAULT_MANAGER.status_payload()


@router.post("/stop")
async def stop() -> dict[str, object]:
    """停止会话。"""
    DEFAULT_MANAGER.stop()
    return DEFAULT_MANAGER.status_payload()


@router.post("/pause")
async def pause() -> dict[str, object]:
    """暂停会话。"""
    try:
        DEFAULT_MANAGER.pause()
    except InvalidSessionTransitionError as error:
        raise HTTPException(status_code=409, detail=error.to_dict()) from error
    return DEFAULT_MANAGER.status_payload()


@router.post("/resume")
async def resume() -> dict[str, object]:
    """继续会话。"""
    try:
        DEFAULT_MANAGER.resume()
    except InvalidSessionTransitionError as error:
        raise HTTPException(status_code=409, detail=error.to_dict()) from error
    return DEFAULT_MANAGER.status_payload()
