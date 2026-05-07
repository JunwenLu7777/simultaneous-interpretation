"""短句真实发声桥测试。"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator

import numpy as np
import pytest

from teams_voice_interpreter import live_say
from teams_voice_interpreter.audio.routing import AudioDevice
from teams_voice_interpreter.config import Settings
from teams_voice_interpreter.data.audio_segment import AudioDirection
from teams_voice_interpreter.errors import EdgeTTSError, PiperTTSError
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


class _FakeTTSClient:
    """按 attempt 序列依次返回 TTS 错误或音频事件的桩。"""

    instances: list[_FakeTTSClient] = []

    def __init__(
        self,
        outcomes: list[list[TTSEvent] | EdgeTTSError | PiperTTSError],
        *,
        settings: Settings | None = None,
    ) -> None:
        del settings
        self._outcomes = outcomes
        type(self).instances.append(self)

    def _next_outcome(self) -> list[TTSEvent] | EdgeTTSError | PiperTTSError:
        if not self._outcomes:
            msg = "FakeTTSClient outcomes exhausted"
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
        if isinstance(outcome, EdgeTTSError | PiperTTSError):
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
    outcomes: list[list[TTSEvent] | EdgeTTSError | PiperTTSError] = [
        EdgeTTSError(
            code=error_code,
            what_happened="发生了什么：Edge-TTS 未返回音频数据。",
            next_action="下一步如何做：请重试一次。",
        ),
        success_chunks,
    ]

    factory_calls: list[Settings] = []

    def factory(settings: Settings) -> _FakeTTSClient:
        factory_calls.append(settings)
        return _FakeTTSClient(outcomes, settings=settings)

    monkeypatch.setattr(live_say, "build_tts_client", factory)

    bridge = LiveSayBridge.__new__(LiveSayBridge)
    settings = Settings(tts_engine="edge_tts", tts_rate="+0%")
    events = asyncio.run(
        bridge._synthesize_with_retry(
            target_text="你好",
            direction=AudioDirection.UPLINK,
            settings=settings,
        )
    )

    assert events == success_chunks
    assert len(factory_calls) == 2
    assert all(call is settings for call in factory_calls)


def test_synthesize_with_retry_propagates_after_exhausted_retries(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """两次都收空音频时必须抛 `tts.no_audio`，且消息里带译文预览与"重试 N 次后仍失败"。"""
    error = EdgeTTSError(
        code="tts.no_audio",
        what_happened="发生了什么：Edge-TTS 未返回音频数据。",
        next_action="下一步如何做：请重试一次。",
    )
    outcomes: list[list[TTSEvent] | EdgeTTSError | PiperTTSError] = [error, error]

    monkeypatch.setattr(
        live_say,
        "build_tts_client",
        lambda settings: _FakeTTSClient(outcomes, settings=settings),
    )

    bridge = LiveSayBridge.__new__(LiveSayBridge)
    with pytest.raises(EdgeTTSError) as exc_info:
        asyncio.run(
            bridge._synthesize_with_retry(
                target_text="你好",
                direction=AudioDirection.UPLINK,
                settings=Settings(tts_engine="edge_tts", tts_rate="+0%"),
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
    outcomes: list[list[TTSEvent] | EdgeTTSError | PiperTTSError] = [error]

    factory_calls: list[Settings] = []

    def factory(settings: Settings) -> _FakeTTSClient:
        factory_calls.append(settings)
        return _FakeTTSClient(outcomes, settings=settings)

    monkeypatch.setattr(live_say, "build_tts_client", factory)

    bridge = LiveSayBridge.__new__(LiveSayBridge)
    settings = Settings(tts_engine="edge_tts", tts_rate="+0%")
    with pytest.raises(EdgeTTSError) as exc_info:
        asyncio.run(
            bridge._synthesize_with_retry(
                target_text="你好",
                direction=AudioDirection.UPLINK,
                settings=settings,
            )
        )

    assert exc_info.value.code == "tts.voice_unknown"
    assert len(factory_calls) == 1


def test_synthesize_with_retry_preserves_piper_error_type(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Piper 默认路径失败时必须保留 PiperTTSError 类型，便于调用方区分模型问题。"""
    error = PiperTTSError(
        code="tts.piper_synthesize_failed",
        what_happened="发生了什么：Piper 合成失败。",
        next_action="下一步如何做：请重新下载模型。",
    )
    outcomes: list[list[TTSEvent] | EdgeTTSError | PiperTTSError] = [error, error]
    monkeypatch.setattr(
        live_say,
        "build_tts_client",
        lambda settings: _FakeTTSClient(outcomes, settings=settings),
    )

    bridge = LiveSayBridge.__new__(LiveSayBridge)
    with pytest.raises(PiperTTSError) as exc_info:
        asyncio.run(
            bridge._synthesize_with_retry(
                target_text="你好",
                direction=AudioDirection.UPLINK,
                settings=Settings(tts_engine="piper"),
            )
        )

    assert exc_info.value.code == "tts.piper_synthesize_failed"
    assert "重试 1 次后仍失败" in exc_info.value.what_happened


def test_decode_tts_events_handles_piper_pcm_chunks() -> None:
    """非 streaming 的 say/ptt 路径必须能解码 Piper raw PCM。"""
    events = [
        TTSEvent(
            kind="first_byte",
            audio_chunk=np.array([1, 2], dtype="<i2").tobytes(),
            audio_format="pcm_s16le_16000",
        ),
        TTSEvent(
            kind="audio_chunk",
            audio_chunk=np.array([3], dtype="<i2").tobytes(),
            audio_format="pcm_s16le_16000",
        ),
    ]

    pcm = asyncio.run(live_say._decode_tts_events_to_pcm16(events))

    assert pcm.tolist() == [1, 2, 3]


def test_prepare_streaming_uses_one_tts_retry_to_recover_short_text_no_audio(  # type: ignore[no-untyped-def]
    monkeypatch,
) -> None:
    """实时 streaming 路径必须把 TTS retry 参数传给 early PCM producer。"""
    captured: dict[str, object] = {}
    prewarmed_directions: list[AudioDirection] = []

    class _FakeDeepSeek:
        def __init__(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

        async def stream_translate(
            self, text: str, *, direction: AudioDirection, context_text: str = ""
        ) -> AsyncIterator[TranslationChunk]:
            del text, direction, context_text
            yield TranslationChunk(kind="delta", text="Hello")
            yield TranslationChunk(kind="completed", text="")

    class _FakeSettings:
        deepseek_model = "deepseek-chat"
        tts_rate = "+0%"
        tts_engine = "edge_tts"

        def resolved_deepseek_api_key(self) -> str:
            return "sk-test"

    async def _fake_pcm() -> AsyncIterator[np.ndarray]:
        yield np.array([1, 2], dtype=np.int16)

    def _spy(**kwargs: object) -> AsyncIterator[np.ndarray]:
        captured.update(kwargs)
        return _fake_pcm()

    monkeypatch.setattr(live_say, "DeepSeekStreamingClient", _FakeDeepSeek)
    monkeypatch.setattr(live_say, "load_settings", lambda **_: _FakeSettings())
    monkeypatch.setattr(live_say, "stream_pcm_chunks_with_retry", _spy)
    monkeypatch.setattr(
        live_say,
        "prewarm_tts_client",
        lambda settings, *, direction: prewarmed_directions.append(direction),
    )
    monkeypatch.setattr(
        LiveSayBridge,
        "_target_device",
        lambda self, target: AudioDevice(1, "AirPods", 0, 2),
    )

    bridge = LiveSayBridge.__new__(LiveSayBridge)
    prepared = asyncio.run(
        bridge.prepare(
            "你好",
            direction=AudioDirection.UPLINK,
            target="default",
            streaming=True,
        )
    )
    pcm = asyncio.run(prepared.pcm_iterator.__anext__())

    assert captured["max_retries"] == 1
    assert captured["target_text"] == "Hello"
    assert pcm.tolist() == [1, 2]
    assert prewarmed_directions == [AudioDirection.UPLINK]


def test_prepare_streaming_returns_after_first_mt_delta_before_completed(  # type: ignore[no-untyped-def]
    monkeypatch,
) -> None:
    """首个 MT delta 到达后 prepare 应可返回，不再等待 completed。"""
    release_completed = threading.Event()

    class _SlowCompletedDeepSeek:
        def __init__(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

        async def stream_translate(
            self, text: str, *, direction: AudioDirection, context_text: str = ""
        ) -> AsyncIterator[TranslationChunk]:
            del text, direction, context_text
            yield TranslationChunk(kind="delta", text="Hello")
            await asyncio.to_thread(release_completed.wait)
            yield TranslationChunk(kind="completed", text="")

    class _FakeSettings:
        deepseek_model = "deepseek-chat"
        tts_rate = "+0%"
        tts_engine = "edge_tts"

        def resolved_deepseek_api_key(self) -> str:
            return "sk-test"

    async def _fake_pcm(**kwargs: object) -> AsyncIterator[np.ndarray]:
        del kwargs
        yield np.array([1], dtype=np.int16)

    monkeypatch.setattr(live_say, "DeepSeekStreamingClient", _SlowCompletedDeepSeek)
    monkeypatch.setattr(live_say, "load_settings", lambda **_: _FakeSettings())
    monkeypatch.setattr(live_say, "stream_pcm_chunks_with_retry", _fake_pcm)
    monkeypatch.setattr(
        LiveSayBridge,
        "_target_device",
        lambda self, target: AudioDevice(1, "AirPods", 0, 2),
    )

    bridge = LiveSayBridge.__new__(LiveSayBridge)
    try:
        prepared = asyncio.run(
            asyncio.wait_for(
                bridge.prepare(
                    "你好",
                    direction=AudioDirection.UPLINK,
                    target="default",
                    streaming=True,
                ),
                timeout=1.0,
            )
        )
    finally:
        release_completed.set()

    assert prepared.target_text == "Hello"


def test_prepare_streaming_aborts_when_deepseek_stream_exceeds_budget(  # type: ignore[no-untyped-def]
    monkeypatch,
) -> None:
    """DeepSeek 服务端长时间无响应必须按 stream budget 主动放弃，避免拖死管线。"""
    monkeypatch.setattr(live_say, "DEEPSEEK_STREAM_BUDGET_S", 0.1)

    class _StuckDeepSeek:
        def __init__(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

        async def stream_translate(
            self, text: str, *, direction: AudioDirection, context_text: str = ""
        ) -> AsyncIterator[TranslationChunk]:
            del text, direction, context_text
            await asyncio.sleep(10)  # 模拟 DeepSeek 卡死
            if False:  # pragma: no cover - 永远到不了
                yield TranslationChunk(kind="delta", text="")

    class _FakeSettings:
        deepseek_model = "deepseek-chat"
        tts_rate = "+0%"
        tts_engine = "edge_tts"

        def resolved_deepseek_api_key(self) -> str:
            return "sk-test"

    monkeypatch.setattr(live_say, "DeepSeekStreamingClient", _StuckDeepSeek)
    monkeypatch.setattr(live_say, "load_settings", lambda **_: _FakeSettings())
    monkeypatch.setattr(
        LiveSayBridge,
        "_target_device",
        lambda self, target: AudioDevice(1, "AirPods", 0, 2),
    )

    bridge = LiveSayBridge.__new__(LiveSayBridge)
    with pytest.raises(live_say.DeepSeekError) as exc:
        asyncio.run(
            bridge.prepare(
                "你好",
                direction=AudioDirection.UPLINK,
                target="default",
                streaming=True,
            )
        )

    assert exc.value.code == "mt.stream_budget_exceeded"
    assert "DeepSeek" in exc.value.what_happened
    assert "丢弃" in exc.value.next_action


def test_prepare_reuses_single_httpx_client_across_nonstreaming_calls(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """非 streaming prepare 仍复用同一个 httpx.AsyncClient，省掉 DeepSeek TLS 握手。"""
    captured_http_clients: list[object] = []

    class _FakeDeepSeek:
        def __init__(self, *args: object, http_client: object = None, **kwargs: object) -> None:
            del args, kwargs
            captured_http_clients.append(http_client)

        async def stream_translate(
            self, text: str, *, direction: AudioDirection, context_text: str = ""
        ) -> AsyncIterator[TranslationChunk]:
            del text, direction, context_text
            yield TranslationChunk(kind="delta", text="Hello")
            yield TranslationChunk(kind="completed", text="")

    class _FakeSettings:
        deepseek_model = "deepseek-chat"
        tts_rate = "+0%"
        tts_engine = "edge_tts"

        def resolved_deepseek_api_key(self) -> str:
            return "sk-test"

    async def _fake_synthesize(*args: object, **kwargs: object) -> list[TTSEvent]:
        del args, kwargs
        return [
            TTSEvent(
                kind="first_byte",
                audio_chunk=np.array([1], dtype="<i2").tobytes(),
                audio_format="pcm_s16le_16000",
            )
        ]

    monkeypatch.setattr(live_say, "DeepSeekStreamingClient", _FakeDeepSeek)
    monkeypatch.setattr(live_say, "load_settings", lambda **_: _FakeSettings())
    monkeypatch.setattr(
        LiveSayBridge,
        "_synthesize_with_retry",
        _fake_synthesize,
    )
    monkeypatch.setattr(
        LiveSayBridge,
        "_target_device",
        lambda self, target: AudioDevice(1, "AirPods", 0, 2),
    )

    bridge = LiveSayBridge.__new__(LiveSayBridge)
    asyncio.run(
        bridge.prepare(
            "你好",
            direction=AudioDirection.UPLINK,
            target="default",
        )
    )
    asyncio.run(
        bridge.prepare(
            "再见",
            direction=AudioDirection.UPLINK,
            target="default",
        )
    )

    assert len(captured_http_clients) == 2
    assert captured_http_clients[0] is not None
    assert captured_http_clients[0] is captured_http_clients[1]


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
