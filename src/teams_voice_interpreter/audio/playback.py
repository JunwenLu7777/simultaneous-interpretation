"""音频写出到 BlackHole 或默认输出设备。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import numpy as np
import numpy.typing as npt
import sounddevice as sd

from teams_voice_interpreter.audio.resample import resample_int16_mono

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
    """通过 sounddevice 写入真实 CoreAudio 设备。

    macOS 大多数耳机/聚合设备原生采样率是 48 kHz，PortAudio 不会自动做采样率
    转换；如果直接喂 16 kHz PCM，会持续打印 `PaMacCore err='-50'` 的 paramErr，
    导致每段开头有 click 或丢字。本类在首次写入时查询设备 `default_samplerate`，
    必要时把 mono / stereo PCM 上采样到设备原生采样率后再 `sd.play`。
    """

    device_index: int
    sample_rate_hz: int = 16000
    _bytes_written: int = 0
    _device_sample_rate_hz: int | None = None

    def write(self, samples: Int16Array) -> None:
        """阻塞写入 PCM16 样本，必要时按设备原生采样率上采样。"""
        pcm = np.asarray(samples, dtype=np.int16)
        if pcm.size == 0:
            return
        device_rate = self._resolve_device_sample_rate()
        if device_rate != self.sample_rate_hz:
            pcm = _resample_pcm_to_rate(
                pcm,
                source_rate_hz=self.sample_rate_hz,
                target_rate_hz=device_rate,
            )
        sd.play(pcm, samplerate=device_rate, device=self.device_index, blocking=True)
        self._bytes_written += pcm.nbytes

    @property
    def bytes_written(self) -> int:
        """累计写出字节数。"""
        return self._bytes_written

    def _resolve_device_sample_rate(self) -> int:
        if self._device_sample_rate_hz is not None:
            return self._device_sample_rate_hz
        info = sd.query_devices(self.device_index)
        try:
            rate = int(float(info["default_samplerate"]))
        except (KeyError, TypeError, ValueError):
            return self.sample_rate_hz
        if rate <= 0:
            return self.sample_rate_hz
        self._device_sample_rate_hz = rate
        return rate


def _resample_pcm_to_rate(
    pcm: Int16Array,
    *,
    source_rate_hz: int,
    target_rate_hz: int,
) -> Int16Array:
    """对 mono 或 stereo int16 PCM 整体重采样到目标采样率。"""
    samples = np.asarray(pcm, dtype=np.int16)
    if samples.ndim == 1:
        return resample_int16_mono(
            samples,
            source_rate_hz=source_rate_hz,
            target_rate_hz=target_rate_hz,
        )
    channels = [
        resample_int16_mono(
            samples[:, channel],
            source_rate_hz=source_rate_hz,
            target_rate_hz=target_rate_hz,
        )
        for channel in range(samples.shape[1])
    ]
    return np.column_stack(channels).astype(np.int16)


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
