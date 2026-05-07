"""Edge-TTS 热 PCM 流测试。"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator

import numpy as np

from teams_voice_interpreter.config import Settings
from teams_voice_interpreter.data.audio_segment import AudioDirection
from teams_voice_interpreter.tts import streaming as streaming_mod
from teams_voice_interpreter.tts.streaming import start_pcm_stream_with_retry


def test_start_pcm_stream_with_retry_starts_producer_before_first_consume(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """创建热流后即启动 producer，播放 worker 排队时也能提前合成。"""
    started = threading.Event()

    async def fake_stream_pcm_chunks_with_retry(
        *,
        target_text: str,
        direction: AudioDirection,
        rate: str,
        settings: Settings | None = None,
        max_retries: int,
        first_byte_timeout_s: float,
        synthesis_timeout_s: float,
    ) -> AsyncIterator[np.ndarray]:
        del (
            target_text,
            direction,
            rate,
            settings,
            max_retries,
            first_byte_timeout_s,
            synthesis_timeout_s,
        )
        started.set()
        yield np.array([1, 2, 3], dtype=np.int16)

    monkeypatch.setattr(
        streaming_mod,
        "stream_pcm_chunks_with_retry",
        fake_stream_pcm_chunks_with_retry,
    )

    async def scenario() -> list[int]:
        stream = start_pcm_stream_with_retry(
            target_text="Hello",
            direction=AudioDirection.DOWNLINK,
            rate="+0%",
        )
        await asyncio.wait_for(asyncio.to_thread(started.wait), timeout=1)
        await asyncio.sleep(0.01)
        chunks = [chunk async for chunk in stream]
        return chunks[0].tolist()

    assert asyncio.run(scenario()) == [1, 2, 3]


def test_threaded_pcm_stream_can_be_closed_before_consuming_all_chunks(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """播放截断后关闭热流时，后台 producer 必须能退出。"""
    closed = threading.Event()

    async def fake_stream_pcm_chunks_with_retry(
        *,
        target_text: str,
        direction: AudioDirection,
        rate: str,
        settings: Settings | None = None,
        max_retries: int,
        first_byte_timeout_s: float,
        synthesis_timeout_s: float,
    ) -> AsyncIterator[np.ndarray]:
        del (
            target_text,
            direction,
            rate,
            settings,
            max_retries,
            first_byte_timeout_s,
            synthesis_timeout_s,
        )
        try:
            while True:
                yield np.array([1, 2, 3], dtype=np.int16)
                await asyncio.sleep(0)
        finally:
            closed.set()

    monkeypatch.setattr(
        streaming_mod,
        "stream_pcm_chunks_with_retry",
        fake_stream_pcm_chunks_with_retry,
    )

    async def scenario() -> list[int]:
        stream = start_pcm_stream_with_retry(
            target_text="Hello",
            direction=AudioDirection.DOWNLINK,
            rate="+0%",
        )
        first = await anext(stream)
        await stream.aclose()  # type: ignore[attr-defined]
        await asyncio.wait_for(asyncio.to_thread(closed.wait), timeout=1)
        return first.tolist()

    assert asyncio.run(scenario()) == [1, 2, 3]
