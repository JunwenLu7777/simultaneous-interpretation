"""TTS 输出音频解码（mp3 与 raw PCM 两条路径）。

Edge-TTS 输出 mp3 chunks，走 `decode_mp3_stream_to_pcm16` (PyAV)。
Piper 输出 raw int16 PCM @ 22050 Hz，走 `decode_pcm_stream_to_pcm16`
（仅做线性重采到 16 kHz mono）。两条路径输出形式都是 `Int16Array`
@ 16 kHz mono，以保持下游 (StreamAudioWriter / live_say) 一致。
"""

from __future__ import annotations

import subprocess
import tempfile
import wave
from collections.abc import AsyncIterator, Callable
from io import BytesIO
from pathlib import Path
from typing import Any, cast

import numpy as np
import numpy.typing as npt

from teams_voice_interpreter.audio.resample import resample_int16_mono
from teams_voice_interpreter.errors import UserFacingError

Runner = Callable[[list[str]], None]
Int16Array = npt.NDArray[np.int16]


def warm_up_pyav_decoder() -> None:
    """预加载 PyAV 与 resampler，避免首段播放时才承担 import 成本。"""
    import av  # noqa: F401
    from av.audio.resampler import AudioResampler

    AudioResampler(format="s16", layout="mono", rate=16000)


def decode_mp3_bytes_to_pcm16(
    mp3_bytes: bytes,
    *,
    temp_dir: Path | None = None,
    runner: Runner | None = None,
    sample_rate_hz: int = 16000,
) -> np.ndarray:
    """用 macOS afconvert 将 Edge-TTS MP3 bytes 解码为 16 kHz mono PCM16。"""
    if not mp3_bytes:
        return np.array([], dtype=np.int16)
    run = runner or _run_afconvert
    with tempfile.TemporaryDirectory(dir=temp_dir) as tmp:
        base = Path(tmp)
        mp3_path = base / "edge-tts.mp3"
        wav_path = base / "edge-tts.wav"
        mp3_path.write_bytes(mp3_bytes)
        run(
            [
                "afconvert",
                "-f",
                "WAVE",
                "-d",
                f"LEI16@{sample_rate_hz}",
                "-c",
                "1",
                str(mp3_path),
                str(wav_path),
            ]
        )
        return _read_wav_pcm16(wav_path)


async def decode_mp3_stream_to_pcm16(
    mp3_chunks: AsyncIterator[bytes],
    *,
    sample_rate_hz: int = 16000,
) -> AsyncIterator[Int16Array]:
    """用 PyAV 将 MP3 chunk 流增量解码为 16 kHz mono PCM16。"""
    buffer = bytearray()
    yielded_samples = 0
    last_error: Exception | None = None
    async for chunk in mp3_chunks:
        if not chunk:
            continue
        buffer.extend(chunk)
        try:
            pcm = _decode_mp3_buffer_to_pcm16(bytes(buffer), sample_rate_hz=sample_rate_hz)
        except Exception as error:  # pragma: no cover -具体异常类型由 PyAV/FFmpeg 决定
            last_error = error
            continue
        last_error = None
        if pcm.size > yielded_samples:
            yield pcm[yielded_samples:]
            yielded_samples = pcm.size
    if not buffer:
        return
    try:
        final_pcm = _decode_mp3_buffer_to_pcm16(bytes(buffer), sample_rate_hz=sample_rate_hz)
    except Exception as error:
        raise _decode_failed_error(error) from error
    if final_pcm.size == 0:
        raise _decode_failed_error(last_error)
    if final_pcm.size > yielded_samples:
        yield final_pcm[yielded_samples:]


async def decode_pcm_stream_to_pcm16(
    pcm_chunks: AsyncIterator[bytes],
    *,
    source_sample_rate_hz: int = 22050,
    target_sample_rate_hz: int = 16000,
) -> AsyncIterator[Int16Array]:
    """把 raw int16 PCM mono bytes 流增量重采到目标 sample rate。

    用于 PiperClient 等输出 raw PCM 的 TTS 引擎。**逐 chunk 重采**，不在
    chunk 边界保持滤波器上下文 —— Piper 的 `AudioChunk` 是相对独立的
    短语片段（语段边界由模型决定），在普通商务话术下边界 artifact 不
    可感；如果 chunk 切分细到字 / 子词级别，应改为 stateful resampler
    （留给阶段 3b 集成时根据真实听感决定）。
    """
    async for chunk in pcm_chunks:
        if not chunk:
            continue
        samples = np.frombuffer(chunk, dtype="<i2").astype(np.int16)
        if samples.size == 0:
            continue
        resampled = resample_int16_mono(
            samples,
            source_rate_hz=source_sample_rate_hz,
            target_rate_hz=target_sample_rate_hz,
        )
        if resampled.size > 0:
            yield resampled


def _run_afconvert(command: list[str]) -> None:
    subprocess.run(command, check=True)


def _read_wav_pcm16(path: Path) -> np.ndarray:
    with wave.open(BytesIO(path.read_bytes()), "rb") as wav:
        channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
        if sample_width != 2:
            msg = f"expected 16-bit PCM, got sample width {sample_width}"
            raise ValueError(msg)
        raw = wav.readframes(wav.getnframes())
    samples = np.frombuffer(raw, dtype="<i2").astype(np.int16)
    if channels == 1:
        return samples
    mixed = samples.reshape(-1, channels).mean(axis=1).astype(np.int16)
    return cast(npt.NDArray[np.int16], mixed)


def _decode_mp3_buffer_to_pcm16(mp3_bytes: bytes, *, sample_rate_hz: int) -> Int16Array:
    import av
    from av.audio.resampler import AudioResampler

    resampler = AudioResampler(
        format="s16",
        layout="mono",
        rate=sample_rate_hz,
    )
    pcm_chunks: list[Int16Array] = []
    with av.open(BytesIO(mp3_bytes), mode="r", format="mp3") as container:
        for frame in container.decode(audio=0):
            pcm_chunks.extend(_resample_frame_to_pcm16(frame, resampler))
    if not pcm_chunks:
        return np.array([], dtype=np.int16)
    return np.concatenate(pcm_chunks).astype(np.int16)


def _resample_frame_to_pcm16(frame: Any, resampler: Any) -> list[Int16Array]:
    resampled = resampler.resample(frame)
    frames = resampled if isinstance(resampled, list) else [resampled]
    return [_frame_to_int16_mono(item) for item in frames if item is not None]


def _frame_to_int16_mono(frame: Any) -> Int16Array:
    samples = np.asarray(frame.to_ndarray(), dtype=np.int16)
    if samples.ndim == 1:
        return samples
    return samples.reshape(-1)


def _decode_failed_error(error: Exception | None) -> UserFacingError:
    detail = f" 细节：{error}" if error is not None else ""
    return UserFacingError(
        code="tts.decode_failed",
        what_happened=f"发生了什么：Edge-TTS 返回的 MP3 音频无法用 PyAV 解码。{detail}",
        next_action="下一步如何做：请确认依赖已安装；若持续出现，请重试该段或回退到非流式发声。",
    )
