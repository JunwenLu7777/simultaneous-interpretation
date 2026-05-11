"""TTS 客户端 factory：按 `Settings.tts_engine` 选择 backend。

阶段 3b-1 提供独立的实例化点，让后续 caller（`live_say` / `streaming` /
`session.manager` / `readiness` / `cli/wizard`）从直接 `EdgeTTSClient(...)`
迁移到 `build_tts_client(settings)`，无需关心 backend 类型。

返回值用 `Any` 是有意为之 —— 阶段 3b-1 不引入 `TTSClient` Protocol 抽象
（避免提前抽象），调用方按 duck typing 使用 `stream_synthesize`、
`validate_voice` 即可。阶段 3b-2 引入 Protocol 后可把返回类型收窄。
"""

from __future__ import annotations

import threading
from typing import Any

from teams_voice_interpreter.config import Settings
from teams_voice_interpreter.data.audio_segment import AudioDirection
from teams_voice_interpreter.tts.edge_tts_client import EdgeTTSClient
from teams_voice_interpreter.tts.piper_client import PiperClient

_PIPER_CLIENTS: dict[str, PiperClient] = {}
_PIPER_CLIENTS_LOCK = threading.Lock()


def build_tts_client(settings: Settings) -> Any:
    """根据 `settings.tts_engine` 实例化合适的 TTS 客户端。

    - `"piper"`：本地 ONNX，生产 v1 默认；`models_dir` 取自
      `settings.resolved_piper_models_dir()`。
    - `"edge_tts"`：Microsoft Edge 浏览器免费接口（保留作降级路径）；
      `live=True` 走真实 HTTP，`rate` 取自 `settings.tts_rate`。

    Args:
        settings: 应用配置；`tts_engine` 字段决定 backend。

    Returns:
        TTS 客户端实例（PiperClient 或 EdgeTTSClient）。
    """
    if settings.tts_engine == "piper":
        return _cached_piper_client(settings)
    return EdgeTTSClient(live=True, rate=settings.tts_rate)


def prewarm_tts_client(settings: Settings, *, direction: AudioDirection) -> None:
    """预热当前方向的 TTS backend；非 Piper backend 无需预热。"""
    if settings.tts_engine != "piper":
        return
    _cached_piper_client(settings).preload_voice(direction=direction)


def clear_tts_client_cache() -> None:
    """测试辅助：清空进程级 Piper client cache。"""
    with _PIPER_CLIENTS_LOCK:
        _PIPER_CLIENTS.clear()


def _cached_piper_client(settings: Settings) -> PiperClient:
    models_dir = settings.resolved_piper_models_dir()
    cache_key = str(models_dir)
    with _PIPER_CLIENTS_LOCK:
        client = _PIPER_CLIENTS.get(cache_key)
        if client is None:
            client = PiperClient(models_dir=models_dir, pool_size=settings.piper_pool_size)
            _PIPER_CLIENTS[cache_key] = client
        return client
