"""Edge-TTS MP3 流到 PCM 流的桥接 helper。"""

from __future__ import annotations

import asyncio
import queue
import threading
from collections.abc import AsyncIterator

import numpy as np
import numpy.typing as npt

from teams_voice_interpreter.data.audio_segment import AudioDirection
from teams_voice_interpreter.errors import EdgeTTSError
from teams_voice_interpreter.tts.audio_decode import decode_mp3_stream_to_pcm16
from teams_voice_interpreter.tts.edge_tts_client import EdgeTTSClient

Int16Array = npt.NDArray[np.int16]
_RETRIABLE_TTS_ERROR_CODES = {
    "tts.no_audio",
    "tts.first_byte_timeout",
    "tts.synthesis_timeout",
}


async def stream_pcm_chunks_with_retry(
    *,
    target_text: str,
    direction: AudioDirection,
    rate: str,
    max_retries: int = 1,
    first_byte_timeout_s: float = 8.0,
    synthesis_timeout_s: float = 15.0,
) -> AsyncIterator[Int16Array]:
    """合成 MP3 chunk 并增量解码成 PCM16 chunk。"""
    async for pcm in decode_mp3_stream_to_pcm16(
        _stream_mp3_chunks_with_retry(
            target_text=target_text,
            direction=direction,
            rate=rate,
            max_retries=max_retries,
            first_byte_timeout_s=first_byte_timeout_s,
            synthesis_timeout_s=synthesis_timeout_s,
        )
    ):
        yield pcm


def start_pcm_stream_with_retry(
    *,
    target_text: str,
    direction: AudioDirection,
    rate: str,
    max_retries: int = 1,
    first_byte_timeout_s: float = 8.0,
    synthesis_timeout_s: float = 15.0,
) -> AsyncIterator[Int16Array]:
    """立即在后台启动 TTS/解码 producer，并返回可异步消费的热 PCM 流。"""
    return _ThreadedPCMStream(
        target_text=target_text,
        direction=direction,
        rate=rate,
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
        max_retries: int,
        first_byte_timeout_s: float,
        synthesis_timeout_s: float,
    ) -> None:
        asyncio.run(
            self._produce(
                target_text=target_text,
                direction=direction,
                rate=rate,
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
        max_retries: int,
        first_byte_timeout_s: float,
        synthesis_timeout_s: float,
    ) -> None:
        try:
            async for pcm in stream_pcm_chunks_with_retry(
                target_text=target_text,
                direction=direction,
                rate=rate,
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


async def _stream_mp3_chunks_with_retry(
    *,
    target_text: str,
    direction: AudioDirection,
    rate: str,
    max_retries: int,
    first_byte_timeout_s: float,
    synthesis_timeout_s: float,
) -> AsyncIterator[bytes]:
    last_error: EdgeTTSError | None = None
    for attempt in range(max_retries + 1):
        emitted_audio = False
        try:
            async for event in EdgeTTSClient(
                live=True,
                rate=rate,
                first_byte_timeout_s=first_byte_timeout_s,
                synthesis_timeout_s=synthesis_timeout_s,
            ).stream_synthesize(target_text, direction=direction):
                if not event.audio_chunk:
                    continue
                emitted_audio = True
                yield event.audio_chunk
            return
        except EdgeTTSError as error:
            last_error = error
            if not _should_retry_stream_error(error, emitted_audio, attempt, max_retries):
                break
            await asyncio.sleep(0.3 * (attempt + 1))
    assert last_error is not None
    raise EdgeTTSError(
        code=last_error.code,
        what_happened=(
            f"{last_error.what_happened} 译文预览：{_preview_text(target_text)}"
            f"（重试 {max_retries} 次后仍失败）"
        ),
        next_action=last_error.next_action,
    ) from last_error


def _should_retry_stream_error(
    error: EdgeTTSError,
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
