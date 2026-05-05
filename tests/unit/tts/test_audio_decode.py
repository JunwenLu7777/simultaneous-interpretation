"""Edge-TTS 音频解码测试。"""

import wave
from collections.abc import AsyncIterator
from io import BytesIO
from pathlib import Path

import av
import numpy as np
import pytest

from teams_voice_interpreter.errors import UserFacingError
from teams_voice_interpreter.tts.audio_decode import (
    decode_mp3_bytes_to_pcm16,
    decode_mp3_stream_to_pcm16,
)


def test_decode_mp3_bytes_to_pcm16_uses_afconvert_runner(tmp_path: Path) -> None:
    """解码器必须通过可替换 runner 转成 16 kHz mono PCM。"""

    def fake_runner(command: list[str]) -> None:
        output_path = Path(command[-1])
        with wave.open(str(output_path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(np.array([0, 1000, -1000], dtype=np.int16).tobytes())

    pcm = decode_mp3_bytes_to_pcm16(b"mp3", temp_dir=tmp_path, runner=fake_runner)

    assert pcm.tolist() == [0, 1000, -1000]


@pytest.mark.asyncio
async def test_decode_mp3_stream_to_pcm16_yields_pcm_from_single_chunk() -> None:
    """单个 MP3 chunk 必须解出非空 16 kHz mono PCM。"""
    pcm_chunks = await _collect_pcm(_mp3_chunks([_fixture_mp3_bytes()]))

    assert pcm_chunks
    assert all(chunk.dtype == np.int16 for chunk in pcm_chunks)
    assert sum(chunk.size for chunk in pcm_chunks) > 0


@pytest.mark.asyncio
async def test_decode_mp3_stream_to_pcm16_matches_full_decode_with_split_chunks() -> None:
    """多 chunk 分次喂入时，累计 PCM 应与一次性喂入基本等价。"""
    mp3_bytes = _fixture_mp3_bytes()
    split = len(mp3_bytes) // 2
    streaming_pcm = np.concatenate(
        await _collect_pcm(_mp3_chunks([mp3_bytes[:split], mp3_bytes[split:]]))
    )
    full_pcm = np.concatenate(await _collect_pcm(_mp3_chunks([mp3_bytes])))

    assert streaming_pcm.dtype == np.int16
    assert abs(streaming_pcm.size - full_pcm.size) <= 160


@pytest.mark.asyncio
async def test_decode_mp3_stream_to_pcm16_wraps_corrupt_mp3_as_user_error() -> None:
    """损坏 MP3 流必须转成声明过的用户可见错误，不能裸抛 PyAV 异常。"""
    with pytest.raises(UserFacingError) as exc_info:
        await _collect_pcm(_mp3_chunks([b"not-an-mp3"]))

    assert exc_info.value.code == "tts.decode_failed"
    assert exc_info.value.what_happened.startswith("发生了什么")
    assert exc_info.value.next_action.startswith("下一步如何做")


async def _collect_pcm(mp3_chunks: AsyncIterator[bytes]) -> list[np.ndarray]:
    return [chunk async for chunk in decode_mp3_stream_to_pcm16(mp3_chunks)]


async def _mp3_chunks(chunks: list[bytes]) -> AsyncIterator[bytes]:
    for chunk in chunks:
        yield chunk


def _fixture_mp3_bytes() -> bytes:
    """生成一段短 MP3，作为 PyAV 解码测试输入。"""
    buffer = BytesIO()
    with av.open(buffer, mode="w", format="mp3") as container:
        stream = container.add_stream("mp3", rate=16000)
        stream.layout = "mono"
        samples = (np.sin(np.linspace(0, 80, 1600)) * 12000).astype(np.int16)
        frame = av.AudioFrame.from_ndarray(samples.reshape(1, -1), format="s16", layout="mono")
        frame.sample_rate = 16000
        for packet in stream.encode(frame):
            container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    return buffer.getvalue()
