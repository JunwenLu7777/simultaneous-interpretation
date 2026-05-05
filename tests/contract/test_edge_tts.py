"""Edge-TTS 契约测试。"""

import pytest

from teams_voice_interpreter.data.audio_segment import AudioDirection
from teams_voice_interpreter.errors import EdgeTTSError
from teams_voice_interpreter.tts.edge_tts_client import EdgeTTSClient, sanitize_text


@pytest.mark.asyncio
async def test_stream_synthesize_returns_audio_chunks() -> None:
    """正常路径必须返回 first_byte / audio_chunk / completed。"""
    client = EdgeTTSClient()

    events = [
        event async for event in client.stream_synthesize("hello", direction=AudioDirection.UPLINK)
    ]

    assert [event.kind for event in events] == ["first_byte", "audio_chunk", "completed"]
    assert events[0].audio_chunk


def test_401_403_refresh_then_degrade() -> None:
    """401/403 先刷新 token，连续 3 次失败后降级。"""
    client = EdgeTTSClient()

    assert client.handle_401_403_failures(1) == "retrying"
    assert client.token_refresh_count == 1
    assert client.handle_401_403_failures(3) == "edge_tts_degraded"


def test_voice_validation_and_ssml_sanitize() -> None:
    """音色枚举校验与 SSML 注入防御。"""
    client = EdgeTTSClient(voices={"zh-CN-XiaoxiaoNeural"})

    client.validate_voice("zh-CN-XiaoxiaoNeural")
    with pytest.raises(EdgeTTSError):
        client.validate_voice("missing")
    assert sanitize_text("<speak>hello</speak>") == "hello"
