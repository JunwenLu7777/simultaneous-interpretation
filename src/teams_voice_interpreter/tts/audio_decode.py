"""Edge-TTS 输出音频解码。"""

from __future__ import annotations

import subprocess
import tempfile
import wave
from collections.abc import Callable
from io import BytesIO
from pathlib import Path
from typing import cast

import numpy as np
import numpy.typing as npt

Runner = Callable[[list[str]], None]


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
