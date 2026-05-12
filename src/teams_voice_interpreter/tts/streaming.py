"""TTS audio chunk 流到 PCM 流的桥接 helper（mp3 + raw PCM 双路径）。

按 `event.audio_format` 选解码路径：
- `"mp3"` → `decode_mp3_stream_to_pcm16`（PyAV 解码 + 重采到 16 kHz）
- `"pcm_s16le_<rate>"` → `decode_pcm_stream_to_pcm16`（线性重采到 16 kHz）

`settings` 参数为 `None` 时退回到旧行为（强制 EdgeTTS / mp3 路径），让
stage 3b-2 上线时不破坏 caller；stage 3b-3 改 caller 注入 `settings` 后
自动切到 `factory.build_tts_client(settings)` 决定的 backend。
"""

from __future__ import annotations

import asyncio
import queue
import threading
from collections.abc import AsyncIterator
from typing import Any

import numpy as np
import numpy.typing as npt

from teams_voice_interpreter.config import Settings
from teams_voice_interpreter.data.audio_segment import AudioDirection
from teams_voice_interpreter.errors import (
    EdgeTTSError,
    PiperTTSError,
    UserFacingError,
)
from teams_voice_interpreter.tts.audio_decode import (
    decode_mp3_stream_to_pcm16,
    decode_pcm_stream_to_pcm16,
)
from teams_voice_interpreter.tts.edge_tts_client import EdgeTTSClient, TTSEvent
from teams_voice_interpreter.tts.factory import build_tts_client

Int16Array = npt.NDArray[np.int16]
_RETRIABLE_TTS_ERROR_CODES = {
    "tts.no_audio",
    "tts.first_byte_timeout",
    "tts.synthesis_timeout",
    # Piper backend：ONNX runtime 偶发 / IO 错误也允许一次重试。
    "tts.piper_synthesize_failed",
}


async def stream_pcm_chunks_with_retry(
    *,
    target_text: str,
    direction: AudioDirection,
    rate: str,
    settings: Settings | None = None,
    max_retries: int = 1,
    first_byte_timeout_s: float = 8.0,
    synthesis_timeout_s: float = 15.0,
) -> AsyncIterator[Int16Array]:
    """合成 audio chunk 并按 `event.audio_format` 选解码路径。

    `settings=None` 时强制走 EdgeTTSClient mp3 路径（stage 3b-2 backward
    compat）；`settings` 给定时按 `settings.tts_engine` 选 backend，
    `rate` 仅在 settings=None 时生效。
    """
    events = _stream_events_with_retry(
        target_text=target_text,
        direction=direction,
        rate=rate,
        settings=settings,
        max_retries=max_retries,
        first_byte_timeout_s=first_byte_timeout_s,
        synthesis_timeout_s=synthesis_timeout_s,
    )
    first_event = await _take_first_audio_event(events)
    if first_event is None:
        return
    audio_chunks = _chained_audio_chunks(first_event, events)
    async for pcm in _decode_audio_chunks(audio_chunks, audio_format=first_event.audio_format):
        yield pcm


def start_pcm_stream_with_retry(
    *,
    target_text: str,
    direction: AudioDirection,
    rate: str,
    settings: Settings | None = None,
    max_retries: int = 1,
    first_byte_timeout_s: float = 8.0,
    synthesis_timeout_s: float = 15.0,
) -> AsyncIterator[Int16Array]:
    """立即在后台启动 TTS/解码 producer，并返回可异步消费的热 PCM 流。

    `settings` 透传给 `stream_pcm_chunks_with_retry`。
    """
    return _ThreadedPCMStream(
        target_text=target_text,
        direction=direction,
        rate=rate,
        settings=settings,
        max_retries=max_retries,
        first_byte_timeout_s=first_byte_timeout_s,
        synthesis_timeout_s=synthesis_timeout_s,
    )


class _ThreadedPCMStream:
    """跨 `asyncio.run()` 生命周期保活的后台 PCM producer。"""

    def __init__(
        self,
        *,
        target_text: str,
        direction: AudioDirection,
        rate: str,
        settings: Settings | None = None,
        max_retries: int,
        first_byte_timeout_s: float,
        synthesis_timeout_s: float,
        max_queue_chunks: int = 16,
    ) -> None:
        self._closed = threading.Event()
        self._queue: queue.Queue[Int16Array | BaseException | None] = queue.Queue(
            maxsize=max_queue_chunks
        )
        self._thread = threading.Thread(
            target=self._run,
            kwargs={
                "target_text": target_text,
                "direction": direction,
                "rate": rate,
                "settings": settings,
                "max_retries": max_retries,
                "first_byte_timeout_s": first_byte_timeout_s,
                "synthesis_timeout_s": synthesis_timeout_s,
            },
            daemon=True,
        )
        self._thread.start()

    def __aiter__(self) -> _ThreadedPCMStream:
        return self

    async def __anext__(self) -> Int16Array:
        item = await asyncio.to_thread(self._queue.get)
        if item is None:
            raise StopAsyncIteration
        if isinstance(item, BaseException):
            raise item
        return item

    async def aclose(self) -> None:
        """停止后台 producer；调用方提前截断播放时用于释放队列压力。"""
        self._closed.set()
        await asyncio.to_thread(self._drain_queue)
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        await asyncio.to_thread(self._thread.join, 0.2)

    def _run(
        self,
        *,
        target_text: str,
        direction: AudioDirection,
        rate: str,
        settings: Settings | None,
        max_retries: int,
        first_byte_timeout_s: float,
        synthesis_timeout_s: float,
    ) -> None:
        asyncio.run(
            self._produce(
                target_text=target_text,
                direction=direction,
                rate=rate,
                settings=settings,
                max_retries=max_retries,
                first_byte_timeout_s=first_byte_timeout_s,
                synthesis_timeout_s=synthesis_timeout_s,
            )
        )

    async def _produce(
        self,
        *,
        target_text: str,
        direction: AudioDirection,
        rate: str,
        settings: Settings | None,
        max_retries: int,
        first_byte_timeout_s: float,
        synthesis_timeout_s: float,
    ) -> None:
        try:
            async for pcm in stream_pcm_chunks_with_retry(
                target_text=target_text,
                direction=direction,
                rate=rate,
                settings=settings,
                max_retries=max_retries,
                first_byte_timeout_s=first_byte_timeout_s,
                synthesis_timeout_s=synthesis_timeout_s,
            ):
                if self._closed.is_set() or not self._put_item(pcm):
                    break
        except Exception as error:
            self._put_item(error)
        finally:
            self._put_item(None)

    def _put_item(self, item: Int16Array | BaseException | None) -> bool:
        while not self._closed.is_set():
            try:
                self._queue.put(item, timeout=0.05)
                return True
            except queue.Full:
                continue
        return False

    def _drain_queue(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return


async def _stream_events_with_retry(
    *,
    target_text: str,
    direction: AudioDirection,
    rate: str,
    settings: Settings | None,
    max_retries: int,
    first_byte_timeout_s: float,
    synthesis_timeout_s: float,
) -> AsyncIterator[TTSEvent]:
    """从 backend client yield TTSEvent；按 retriable code 重试一次。"""
    last_error: UserFacingError | None = None
    for attempt in range(max_retries + 1):
        emitted_audio = False
        client = _build_client_for_stream(
            settings=settings,
            rate=rate,
            first_byte_timeout_s=first_byte_timeout_s,
            synthesis_timeout_s=synthesis_timeout_s,
        )
        try:
            async for event in client.stream_synthesize(target_text, direction=direction, rate=rate):
                if event.audio_chunk:
                    emitted_audio = True
                yield event
            return
        except (EdgeTTSError, PiperTTSError) as error:
            last_error = error
            if not _should_retry_stream_error(error, emitted_audio, attempt, max_retries):
                break
            await asyncio.sleep(0.3 * (attempt + 1))
    assert last_error is not None
    raise type(last_error)(
        code=last_error.code,
        what_happened=(
            f"{last_error.what_happened} 译文预览：{_preview_text(target_text)}"
            f"（重试 {max_retries} 次后仍失败）"
        ),
        next_action=last_error.next_action,
    ) from last_error


def _build_client_for_stream(
    *,
    settings: Settings | None,
    rate: str,
    first_byte_timeout_s: float,
    synthesis_timeout_s: float,
) -> Any:
    """`settings` 给定时走 factory；否则保持 stage 3b-2 之前的 EdgeTTS 行为。"""
    if settings is not None:
        return build_tts_client(settings)
    return EdgeTTSClient(
        live=True,
        rate=rate,
        first_byte_timeout_s=first_byte_timeout_s,
        synthesis_timeout_s=synthesis_timeout_s,
    )


async def _take_first_audio_event(events: AsyncIterator[TTSEvent]) -> TTSEvent | None:
    """消费 events 直到首个非空 audio_chunk；找不到则返回 None。"""
    async for event in events:
        if event.audio_chunk:
            return event
    return None


async def _chained_audio_chunks(
    first_event: TTSEvent,
    rest: AsyncIterator[TTSEvent],
) -> AsyncIterator[bytes]:
    """先 yield first_event 的 chunk，再继续消费 rest 的非空 chunk。"""
    yield first_event.audio_chunk
    async for event in rest:
        if event.audio_chunk:
            yield event.audio_chunk


async def _decode_audio_chunks(
    audio_chunks: AsyncIterator[bytes],
    *,
    audio_format: str,
) -> AsyncIterator[Int16Array]:
    """按 audio_format 分流到 mp3 或 raw PCM decoder。"""
    if audio_format == "mp3":
        async for pcm in decode_mp3_stream_to_pcm16(audio_chunks):
            yield pcm
        return
    if audio_format.startswith("pcm_s16le_"):
        source_rate = int(audio_format.removeprefix("pcm_s16le_"))
        async for pcm in decode_pcm_stream_to_pcm16(
            audio_chunks,
            source_sample_rate_hz=source_rate,
        ):
            yield pcm
        return
    raise UserFacingError(
        code="tts.unsupported_audio_format",
        what_happened=f"发生了什么：未知 TTS audio_format `{audio_format}`。",
        next_action=(
            "下一步如何做：检查 TTS backend 是否声明了正确的 audio_format；"
            "当前支持 `mp3` 与 `pcm_s16le_<sample_rate>`。"
        ),
    )


def _should_retry_stream_error(
    error: UserFacingError,
    emitted_audio: bool,
    attempt: int,
    max_retries: int,
) -> bool:
    return not emitted_audio and error.code in _RETRIABLE_TTS_ERROR_CODES and attempt < max_retries


def _preview_text(text: str, *, max_length: int = 80) -> str:
    compact = " ".join(text.split())
    if len(compact) <= max_length:
        return compact
    return f"{compact[:max_length]}..."
