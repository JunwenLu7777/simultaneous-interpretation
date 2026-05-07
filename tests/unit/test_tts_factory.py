"""TTS factory 单测：按 settings.tts_engine 选 backend。"""

from __future__ import annotations

from pathlib import Path

from teams_voice_interpreter.config import Settings
from teams_voice_interpreter.tts.edge_tts_client import EdgeTTSClient
from teams_voice_interpreter.tts.factory import build_tts_client
from teams_voice_interpreter.tts.piper_client import PiperClient


def test_default_settings_yield_piper_client() -> None:
    """v1 默认 tts_engine="piper" 必须返回 PiperClient（与阶段 2 默认一致）。"""
    settings = Settings()
    client = build_tts_client(settings)

    assert isinstance(client, PiperClient)


def test_edge_tts_engine_yields_edge_tts_client() -> None:
    """显式 tts_engine="edge_tts" 必须返回 EdgeTTSClient（降级路径）。"""
    settings = Settings(tts_engine="edge_tts")
    client = build_tts_client(settings)

    assert isinstance(client, EdgeTTSClient)


def test_piper_client_uses_settings_models_dir(tmp_path: Path) -> None:
    """PiperClient 使用 settings.resolved_piper_models_dir() 而不是硬编码默认。"""
    settings = Settings(
        tts_engine="piper",
        piper_models_dir=str(tmp_path / "voices"),
    )
    client = build_tts_client(settings)

    assert isinstance(client, PiperClient)
    assert client._models_dir == tmp_path / "voices"


def test_piper_client_falls_back_to_default_models_dir_when_blank() -> None:
    """piper_models_dir 为空时回落到 ~/.cache/teams-voice-interpreter/piper-models。"""
    settings = Settings(tts_engine="piper")  # piper_models_dir 默认空字符串
    client = build_tts_client(settings)

    assert isinstance(client, PiperClient)
    assert client._models_dir == Path.home() / ".cache/teams-voice-interpreter/piper-models"


def test_edge_tts_client_uses_settings_rate() -> None:
    """EdgeTTSClient 应当使用 settings.tts_rate（不是 EdgeTTSClient 自身默认）。"""
    settings = Settings(tts_engine="edge_tts", tts_rate="+30%")
    client = build_tts_client(settings)

    assert isinstance(client, EdgeTTSClient)
    assert client.rate == "+30%"


def test_edge_tts_client_runs_in_live_mode() -> None:
    """生产 factory 必须把 EdgeTTSClient 切到 live=True，不留 stub 路径漏入生产。"""
    settings = Settings(tts_engine="edge_tts")
    client = build_tts_client(settings)

    assert isinstance(client, EdgeTTSClient)
    assert client.live is True
