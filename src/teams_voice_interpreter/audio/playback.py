"""音频写出到 BlackHole 或默认输出设备。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import numpy as np
import numpy.typing as npt
import sounddevice as sd

Int16Array = npt.NDArray[np.int16]


class AudioSink(Protocol):
    """音频写出目标协议。"""

    def write(self, samples: Int16Array) -> None:
        """写入一段 PCM16 样本。"""

    @property
    def bytes_written(self) -> int:
        """累计写出字节数。"""


@dataclass
class InMemoryAudioSink:
    """测试用内存音频接收器。"""

    writes: list[Int16Array] = field(default_factory=list)

    def write(self, samples: Int16Array) -> None:
        """记录一次写入。"""
        self.writes.append(np.asarray(samples, dtype=np.int16).copy())

    @property
    def bytes_written(self) -> int:
        """累计写出字节数。"""
        return sum(item.nbytes for item in self.writes)


@dataclass
class SoundDeviceAudioSink:
    """通过 sounddevice 写入真实 CoreAudio 设备。"""

    device_index: int
    sample_rate_hz: int = 16000
    _bytes_written: int = 0

    def write(self, samples: Int16Array) -> None:
        """阻塞写入 PCM16 样本。"""
        pcm = np.asarray(samples, dtype=np.int16)
        if pcm.size == 0:
            return
        sd.play(pcm, samplerate=self.sample_rate_hz, device=self.device_index, blocking=True)
        self._bytes_written += pcm.nbytes

    @property
    def bytes_written(self) -> int:
        """累计写出字节数。"""
        return self._bytes_written


class BlackHoleWriter:
    """把 mono PCM 复制为双通道并写入 BlackHole。"""

    def __init__(self, sink: AudioSink | None = None) -> None:
        self.sink = sink or InMemoryAudioSink()

    def write_mono(self, samples: Int16Array) -> Int16Array:
        """写入 mono 样本并返回实际写出的 stereo 样本。"""
        mono = np.asarray(samples, dtype=np.int16).reshape(-1)
        stereo = np.column_stack((mono, mono)).astype(np.int16)
        self.sink.write(stereo)
        return stereo


class DefaultOutputWriter:
    """写入 Mac 默认输出设备的可测试封装。"""

    def __init__(self, sink: AudioSink | None = None) -> None:
        self.sink = sink or InMemoryAudioSink()

    def write_mono(self, samples: Int16Array) -> Int16Array:
        """写入 mono 样本并返回实际写出的样本。"""
        mono = np.asarray(samples, dtype=np.int16).reshape(-1)
        self.sink.write(mono)
        return mono
