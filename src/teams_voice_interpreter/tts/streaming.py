"""Edge-TTS MP3 流到 PCM 流的桥接 helper。"""

from __future__ import annotations

import asyncio
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
) -> AsyncIterator[Int16Array]:
    """合成 MP3 chunk 并增量解码成 PCM16 chunk。"""
    async for pcm in decode_mp3_stream_to_pcm16(
        _stream_mp3_chunks_with_retry(
            target_text=target_text,
            direction=direction,
            rate=rate,
            max_retries=max_retries,
        )
    ):
        yield pcm


async def _stream_mp3_chunks_with_retry(
    *,
    target_text: str,
    direction: AudioDirection,
    rate: str,
    max_retries: int,
) -> AsyncIterator[bytes]:
    last_error: EdgeTTSError | None = None
    for attempt in range(max_retries + 1):
        emitted_audio = False
        try:
            async for event in EdgeTTSClient(live=True, rate=rate).stream_synthesize(
                target_text,
                direction=direction,
            ):
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
