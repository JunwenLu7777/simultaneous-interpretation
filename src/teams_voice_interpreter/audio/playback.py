"""音频写出到 BlackHole 或默认输出设备。"""

from __future__ import annotations

import asyncio
import queue
import time
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


class OutputStreamLike(Protocol):
    """sounddevice OutputStream 的最小生命周期协议。"""

    def start(self) -> None:
        """启动输出流。"""

    def stop(self) -> None:
        """停止输出流。"""

    def close(self) -> None:
        """关闭输出流。"""


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


@dataclass
class StreamingSoundDeviceAudioSink:
    """通过 sounddevice OutputStream 流式写入真实 CoreAudio 设备。"""

    device_index: int
    sample_rate_hz: int = 16000
    queue_max_chunks: int = 16
    _bytes_written: int = 0
    _device_sample_rate_hz: int | None = None
    _queue: queue.Queue[bytes] = field(init=False)
    _stream: OutputStreamLike = field(init=False)
    _pending: bytearray = field(default_factory=bytearray, init=False)
    _started_at: float = field(default=0.0, init=False)
    _first_payload_at: float | None = field(default=None, init=False)
    _pending_task_open: bool = False
    _closed: bool = False

    def __post_init__(self) -> None:
        device_rate = self._resolve_device_sample_rate()
        self._queue = queue.Queue(maxsize=self.queue_max_chunks)
        self._stream = sd.OutputStream(
            samplerate=device_rate,
            device=self.device_index,
            channels=1,
            dtype="int16",
            callback=self._output_callback,
        )
        self._started_at = time.perf_counter()
        self._stream.start()

    async def feed_pcm(self, samples: Int16Array) -> None:
        """异步喂入 16 kHz mono PCM16，必要时上采样到设备原生采样率。"""
        pcm = np.asarray(samples, dtype=np.int16).reshape(-1)
        if pcm.size == 0:
            return
        device_rate = self._resolve_device_sample_rate()
        if device_rate != self.sample_rate_hz:
            pcm = resample_int16_mono(
                pcm,
                source_rate_hz=self.sample_rate_hz,
                target_rate_hz=device_rate,
            )
        data = pcm.astype(np.int16).tobytes()
        self._bytes_written += len(data)
        await asyncio.to_thread(self._queue.put, data)

    async def flush_and_close(self) -> None:
        """等待已喂入 PCM 被 callback 消费完，再关闭 OutputStream。"""
        if self._closed:
            return
        await asyncio.to_thread(self._queue.join)
        self._stream.stop()
        self._stream.close()
        self._closed = True

    @property
    def bytes_written(self) -> int:
        """累计喂给 OutputStream 的有效音频字节数，不包含静音填充。"""
        return self._bytes_written

    @property
    def first_payload_latency_s(self) -> float | None:
        """从 OutputStream 启动到 callback 首次取到有效 payload 的耗时。"""
        if self._first_payload_at is None:
            return None
        return self._first_payload_at - self._started_at

    def _output_callback(
        self,
        outdata: npt.NDArray[np.int16],
        frames: int,
        time_info: object,
        status: object,
    ) -> None:
        del time_info, status
        required_bytes = frames * np.dtype(np.int16).itemsize
        payload = self._read_payload(required_bytes)
        if payload and self._first_payload_at is None:
            self._first_payload_at = time.perf_counter()
        if len(payload) < required_bytes:
            payload += bytes(required_bytes - len(payload))
        outdata[:] = np.frombuffer(payload, dtype=np.int16).reshape(frames, 1)

    def _read_payload(self, required_bytes: int) -> bytes:
        payload = bytearray()
        while len(payload) < required_bytes:
            self._fill_pending_if_empty()
            if not self._pending:
                break
            take = min(required_bytes - len(payload), len(self._pending))
            payload.extend(self._pending[:take])
            del self._pending[:take]
            if not self._pending:
                self._mark_pending_done()
        return bytes(payload)

    def _fill_pending_if_empty(self) -> None:
        if self._pending:
            return
        try:
            self._pending.extend(self._queue.get_nowait())
        except queue.Empty:
            return
        self._pending_task_open = True

    def _mark_pending_done(self) -> None:
        if not self._pending_task_open:
            return
        self._queue.task_done()
        self._pending_task_open = False

    def _resolve_device_sample_rate(self) -> int:
        if self._device_sample_rate_hz is not None:
            return self._device_sample_rate_hz
        rate = _device_sample_rate_or_default(self.device_index, self.sample_rate_hz)
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


def _device_sample_rate_or_default(device_index: int, fallback_rate_hz: int) -> int:
    info = sd.query_devices(device_index)
    try:
        rate = int(float(info["default_samplerate"]))
    except (KeyError, TypeError, ValueError):
        return fallback_rate_hz
    if rate <= 0:
        return fallback_rate_hz
    return rate


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
