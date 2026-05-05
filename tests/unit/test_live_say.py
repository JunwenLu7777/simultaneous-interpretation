"""短句真实发声桥测试。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import numpy as np
import pytest

from teams_voice_interpreter import live_say
from teams_voice_interpreter.audio.routing import AudioDevice
from teams_voice_interpreter.data.audio_segment import AudioDirection
from teams_voice_interpreter.errors import EdgeTTSError
from teams_voice_interpreter.live_say import (
    LiveSayBridge,
    PreparedSayResult,
    _preview_text,
    _target_text_from_chunks,
)
from teams_voice_interpreter.mt.deepseek_client import TranslationChunk
from teams_voice_interpreter.tts.edge_tts_client import TTSEvent


def test_target_text_from_chunks_joins_deepseek_delta_text() -> None:
    """DeepSeek delta 分片必须拼成完整译文，不能只取最后一个标点。"""
    chunks = [
        TranslationChunk(kind="delta", text="Hello"),
        TranslationChunk(kind="delta", text=", let's begin"),
        TranslationChunk(kind="delta", text=" the meeting"),
        TranslationChunk(kind="delta", text="."),
    ]

    assert _target_text_from_chunks(chunks) == "Hello, let's begin the meeting."


def test_preview_text_compacts_and_truncates_long_text() -> None:
    """错误提示中的译文预览应保持单行且长度受控。"""
    text = _preview_text("Hello\n\nworld " + "x" * 100, max_length=20)

    assert text == "Hello world xxxxxxxx..."


class _FakeEdgeTTSClient:
    """按 attempt 序列依次返回 EdgeTTSError 或音频事件的桩。"""

    instances: list[_FakeEdgeTTSClient] = []

    def __init__(
        self,
        outcomes: list[list[TTSEvent] | EdgeTTSError],
        *,
        live: bool = False,
        rate: str = "+0%",
    ) -> None:
        del live, rate
        self._outcomes = outcomes
        type(self).instances.append(self)

    def _next_outcome(self) -> list[TTSEvent] | EdgeTTSError:
        if not self._outcomes:
            msg = "FakeEdgeTTSClient outcomes exhausted"
            raise AssertionError(msg)
        return self._outcomes.pop(0)

    async def stream_synthesize(
        self,
        text: str,
        *,
        direction: AudioDirection,
    ) -> AsyncIterator[TTSEvent]:
        del text, direction
        outcome = self._next_outcome()
        if isinstance(outcome, EdgeTTSError):
            raise outcome
        for event in outcome:
            yield event


@pytest.mark.parametrize(
    "error_code",
    ["tts.no_audio", "tts.first_byte_timeout", "tts.synthesis_timeout"],
)
def test_synthesize_with_retry_recovers_from_retryable_tts_errors(  # type: ignore[no-untyped-def]
    monkeypatch,
    error_code: str,
) -> None:
    """Edge-TTS 可恢复错误重试一次应当恢复，不能直接抛错丢段。"""
    success_chunks = [TTSEvent(kind="audio_chunk", audio_chunk=b"mp3-bytes")]
    outcomes: list[list[TTSEvent] | EdgeTTSError] = [
        EdgeTTSError(
            code=error_code,
            what_happened="发生了什么：Edge-TTS 未返回音频数据。",
            next_action="下一步如何做：请重试一次。",
        ),
        success_chunks,
    ]

    factory_calls: list[tuple[bool, str]] = []

    def factory(*, live: bool, rate: str) -> _FakeEdgeTTSClient:
        factory_calls.append((live, rate))
        return _FakeEdgeTTSClient(outcomes, live=live, rate=rate)

    monkeypatch.setattr(live_say, "EdgeTTSClient", factory)

    bridge = LiveSayBridge.__new__(LiveSayBridge)
    events = asyncio.run(
        bridge._synthesize_with_retry(
            target_text="你好",
            direction=AudioDirection.UPLINK,
            rate="+0%",
        )
    )

    assert events == success_chunks
    assert len(factory_calls) == 2
    assert all(call == (True, "+0%") for call in factory_calls)


def test_synthesize_with_retry_propagates_after_exhausted_retries(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """两次都收空音频时必须抛 `tts.no_audio`，且消息里带译文预览与"重试 N 次后仍失败"。"""
    error = EdgeTTSError(
        code="tts.no_audio",
        what_happened="发生了什么：Edge-TTS 未返回音频数据。",
        next_action="下一步如何做：请重试一次。",
    )
    outcomes: list[list[TTSEvent] | EdgeTTSError] = [error, error]

    monkeypatch.setattr(
        live_say,
        "EdgeTTSClient",
        lambda *, live=False, rate="+0%": _FakeEdgeTTSClient(outcomes, live=live, rate=rate),
    )

    bridge = LiveSayBridge.__new__(LiveSayBridge)
    with pytest.raises(EdgeTTSError) as exc_info:
        asyncio.run(
            bridge._synthesize_with_retry(
                target_text="你好",
                direction=AudioDirection.UPLINK,
                rate="+0%",
            )
        )

    assert exc_info.value.code == "tts.no_audio"
    assert "你好" in exc_info.value.what_happened
    assert "重试 1 次后仍失败" in exc_info.value.what_happened


def test_synthesize_with_retry_does_not_retry_on_other_errors(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """非 `tts.no_audio` 错误（如鉴权失败、音色不存在）应当立即抛出，不浪费一次重试。"""
    error = EdgeTTSError(
        code="tts.voice_unknown",
        what_happened="发生了什么：音色不存在。",
        next_action="下一步如何做：请检查音色名称。",
    )
    outcomes: list[list[TTSEvent] | EdgeTTSError] = [error]

    factory_calls: list[bool] = []

    def factory(*, live: bool, rate: str) -> _FakeEdgeTTSClient:
        factory_calls.append(live)
        return _FakeEdgeTTSClient(outcomes, live=live, rate=rate)

    monkeypatch.setattr(live_say, "EdgeTTSClient", factory)

    bridge = LiveSayBridge.__new__(LiveSayBridge)
    with pytest.raises(EdgeTTSError) as exc_info:
        asyncio.run(
            bridge._synthesize_with_retry(
                target_text="你好",
                direction=AudioDirection.UPLINK,
                rate="+0%",
            )
        )

    assert exc_info.value.code == "tts.voice_unknown"
    assert len(factory_calls) == 1


def test_play_prepared_streaming_feeds_pcm_iterator_to_streaming_sink(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """流式播放路径必须逐块 feed PCM，并在结束后 flush_and_close。"""
    sink = _FakeStreamingSink(device_index=1, sample_rate_hz=16000)
    monkeypatch.setattr(live_say, "StreamingSoundDeviceAudioSink", lambda **_: sink)

    async def pcm_iterator() -> AsyncIterator[np.ndarray]:
        yield np.array([1, 2, 3], dtype=np.int16)
        yield np.array([4, 5], dtype=np.int16)

    prepared = PreparedSayResult(
        source_text="你好",
        target_text="Hello",
        target_device=AudioDevice(1, "AirPods Pro", 0, 2),
        target="default",
        translation_latency_s=0.1,
        tts_latency_s=0.0,
        decode_latency_s=0.0,
        pcm=np.array([], dtype=np.int16),
        pcm_iterator=pcm_iterator(),
    )

    bridge = LiveSayBridge.__new__(LiveSayBridge)
    result = asyncio.run(bridge.play_prepared_streaming(prepared))

    assert [item.tolist() for item in sink.writes] == [[1, 2, 3], [4, 5]]
    assert sink.closed
    assert result.bytes_written == 10
    assert result.first_pcm_latency_s >= 0
    assert result.first_playback_write_latency_s == 0.07


def test_play_prepared_streaming_truncates_realtime_audio(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """实时播放设置上限时必须截断 PCM，并关闭上游 iterator。"""
    sink = _FakeStreamingSink(device_index=1, sample_rate_hz=16000)
    monkeypatch.setattr(live_say, "StreamingSoundDeviceAudioSink", lambda **_: sink)
    pcm_iterator = _ClosablePCMIterator(np.array([1, 2, 3, 4], dtype=np.int16))
    prepared = PreparedSayResult(
        source_text="你好",
        target_text="Hello",
        target_device=AudioDevice(1, "AirPods Pro", 0, 2),
        target="default",
        translation_latency_s=0.1,
        tts_latency_s=0.0,
        decode_latency_s=0.0,
        pcm=np.array([], dtype=np.int16),
        pcm_iterator=pcm_iterator,
    )

    bridge = LiveSayBridge.__new__(LiveSayBridge)
    result = asyncio.run(bridge.play_prepared_streaming(prepared, max_playback_seconds=2 / 16000))

    assert [item.tolist() for item in sink.writes] == [[1, 2]]
    assert pcm_iterator.closed
    assert result.playback_truncated
    assert result.bytes_written == 4


class _ClosablePCMIterator:
    def __init__(self, samples: np.ndarray) -> None:
        self._samples = samples
        self._emitted = False
        self.closed = False

    def __aiter__(self) -> _ClosablePCMIterator:
        return self

    async def __anext__(self) -> np.ndarray:
        if self._emitted:
            raise StopAsyncIteration
        self._emitted = True
        return self._samples

    async def aclose(self) -> None:
        self.closed = True


class _FakeStreamingSink:
    """测试用流式 sink。"""

    def __init__(self, *, device_index: int, sample_rate_hz: int) -> None:
        del device_index, sample_rate_hz
        self.writes: list[np.ndarray] = []
        self.closed = False

    async def feed_pcm(self, samples: np.ndarray) -> None:
        self.writes.append(np.asarray(samples, dtype=np.int16).copy())

    async def flush_and_close(self) -> None:
        self.closed = True

    @property
    def bytes_written(self) -> int:
        return sum(item.nbytes for item in self.writes)

    @property
    def first_payload_latency_s(self) -> float:
        return 0.07
