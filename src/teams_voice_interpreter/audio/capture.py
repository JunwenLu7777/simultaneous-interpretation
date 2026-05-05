"""音频采集与 BlackHole 输入读取。"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import cast

import numpy as np
import numpy.typing as npt

Int16Array = npt.NDArray[np.int16]


@dataclass(frozen=True)
class AudioFrame:
    """一帧 16 kHz mono PCM 音频。"""

    samples: Int16Array
    sample_rate_hz: int = 16000

    @property
    def duration_ms(self) -> float:
        """返回该帧的毫秒时长。"""
        return len(self.samples) / self.sample_rate_hz * 1000


class MicrophoneCapture:
    """麦克风 30 ms 帧采集器的可测试封装。"""

    def __init__(self, *, sample_rate_hz: int = 16000, frame_ms: int = 30) -> None:
        self.sample_rate_hz = sample_rate_hz
        self.frame_ms = frame_ms
        self.samples_per_frame = int(sample_rate_hz * frame_ms / 1000)

    def frames_from_samples(self, samples: Int16Array) -> list[AudioFrame]:
        """把已有 PCM 样本切成固定 30 ms 帧。"""
        mono = np.asarray(samples, dtype=np.int16).reshape(-1)
        frames: list[AudioFrame] = []
        for start in range(0, len(mono), self.samples_per_frame):
            chunk = mono[start : start + self.samples_per_frame]
            if len(chunk) == self.samples_per_frame:
                frames.append(AudioFrame(samples=chunk, sample_rate_hz=self.sample_rate_hz))
        return frames

    def frames_from_iterable(self, chunks: Iterable[Int16Array]) -> list[AudioFrame]:
        """把多个 PCM chunk 合并为固定帧，供 fixture / mock 输入使用。"""
        if not chunks:
            return []
        return self.frames_from_samples(np.concatenate([np.asarray(item) for item in chunks]))


class BlackHoleReader(MicrophoneCapture):
    """从 BlackHole 2ch 输入读取下行音频的封装。"""

    def downmix_stereo(self, stereo_samples: Int16Array) -> Int16Array:
        """把双通道 BlackHole 输入平均为 mono。"""
        samples = np.asarray(stereo_samples, dtype=np.int16)
        if samples.ndim == 1:
            return samples
        if samples.shape[1] != 2:
            msg = "BlackHoleReader expects mono or stereo int16 samples"
            raise ValueError(msg)
        mixed = samples.astype(np.int32).mean(axis=1)
        return cast(Int16Array, mixed.astype(np.int16))

    def frames_from_stereo(self, stereo_samples: Int16Array) -> list[AudioFrame]:
        """把 BlackHole 双通道样本转成下行 STT mono 帧。"""
        return self.frames_from_samples(self.downmix_stereo(stereo_samples))
