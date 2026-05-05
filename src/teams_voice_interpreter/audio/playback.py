"""音频写出到 BlackHole 或默认输出设备。"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt

Int16Array = npt.NDArray[np.int16]


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


class BlackHoleWriter:
    """把 mono PCM 复制为双通道并写入 BlackHole。"""

    def __init__(self, sink: InMemoryAudioSink | None = None) -> None:
        self.sink = sink or InMemoryAudioSink()

    def write_mono(self, samples: Int16Array) -> Int16Array:
        """写入 mono 样本并返回实际写出的 stereo 样本。"""
        mono = np.asarray(samples, dtype=np.int16).reshape(-1)
        stereo = np.column_stack((mono, mono)).astype(np.int16)
        self.sink.write(stereo)
        return stereo


class DefaultOutputWriter:
    """写入 Mac 默认输出设备的可测试封装。"""

    def __init__(self, sink: InMemoryAudioSink | None = None) -> None:
        self.sink = sink or InMemoryAudioSink()

    def write_mono(self, samples: Int16Array) -> Int16Array:
        """写入 mono 样本并返回实际写出的样本。"""
        mono = np.asarray(samples, dtype=np.int16).reshape(-1)
        self.sink.write(mono)
        return mono
