"""SoundDeviceAudioSink 真实 CoreAudio 写出测试。"""

from __future__ import annotations

import asyncio

import numpy as np

from teams_voice_interpreter.audio import playback as playback_mod
from teams_voice_interpreter.audio.playback import (
    BlackHoleWriter,
    SoundDeviceAudioSink,
    StreamingSoundDeviceAudioSink,
    _resample_pcm_to_rate,
)


def test_sound_device_audio_sink_upsamples_mono_to_device_native_rate(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """耳机原生 48 kHz 时，16 kHz mono PCM 必须先上采样再交给 sd.play 避免 paramErr=-50。"""
    captured: dict[str, object] = {}

    def fake_query_devices(device_index: int) -> dict[str, float]:
        captured["query_index"] = device_index
        return {"default_samplerate": 48000.0}

    def fake_play(samples, *, samplerate, device, blocking):  # type: ignore[no-untyped-def]
        captured["play_samplerate"] = samplerate
        captured["play_device"] = device
        captured["play_blocking"] = blocking
        captured["play_size"] = int(np.asarray(samples).size)
        captured["play_dtype"] = np.asarray(samples).dtype

    monkeypatch.setattr(playback_mod.sd, "query_devices", fake_query_devices)
    monkeypatch.setattr(playback_mod.sd, "play", fake_play)

    sink = SoundDeviceAudioSink(device_index=4, sample_rate_hz=16000)
    sink.write(np.ones(16000, dtype=np.int16) * 1000)

    assert captured == {
        "query_index": 4,
        "play_samplerate": 48000,
        "play_device": 4,
        "play_blocking": True,
        "play_size": 48000,
        "play_dtype": np.dtype("int16"),
    }


def test_sound_device_audio_sink_upsamples_stereo_to_device_native_rate(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """BlackHole 双声道写入也必须按设备原生采样率上采样，避免 paramErr=-50。"""
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        playback_mod.sd,
        "query_devices",
        lambda device_index: {"default_samplerate": 48000.0},
    )

    def fake_play(samples, *, samplerate, device, blocking):  # type: ignore[no-untyped-def]
        captured["play_samplerate"] = samplerate
        captured["play_shape"] = np.asarray(samples).shape

    monkeypatch.setattr(playback_mod.sd, "play", fake_play)

    sink = SoundDeviceAudioSink(device_index=2, sample_rate_hz=16000)
    BlackHoleWriter(sink=sink).write_mono(np.ones(16000, dtype=np.int16) * 500)

    assert captured["play_samplerate"] == 48000
    assert captured["play_shape"] == (48000, 2)


def test_sound_device_audio_sink_skips_resample_when_rates_match(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """耳机原生采样率与源一致时，不应再做线性插值。"""
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        playback_mod.sd,
        "query_devices",
        lambda device_index: {"default_samplerate": 16000.0},
    )

    def fake_play(samples, *, samplerate, device, blocking):  # type: ignore[no-untyped-def]
        captured["play_samplerate"] = samplerate
        captured["play_size"] = int(np.asarray(samples).size)

    monkeypatch.setattr(playback_mod.sd, "play", fake_play)

    sink = SoundDeviceAudioSink(device_index=2, sample_rate_hz=16000)
    sink.write(np.ones(8000, dtype=np.int16))

    assert captured == {"play_samplerate": 16000, "play_size": 8000}


def test_sound_device_audio_sink_falls_back_to_source_rate_when_query_fails(
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    """设备元数据缺 default_samplerate 时回落到源采样率，不应抛异常。"""
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        playback_mod.sd,
        "query_devices",
        lambda device_index: {"name": "Mystery Device"},
    )

    def fake_play(samples, *, samplerate, device, blocking):  # type: ignore[no-untyped-def]
        captured["play_samplerate"] = samplerate
        captured["play_size"] = int(np.asarray(samples).size)

    monkeypatch.setattr(playback_mod.sd, "play", fake_play)

    sink = SoundDeviceAudioSink(device_index=2, sample_rate_hz=16000)
    sink.write(np.ones(8000, dtype=np.int16))

    assert captured == {"play_samplerate": 16000, "play_size": 8000}


def test_sound_device_audio_sink_caches_device_rate(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """连续写多段时 default_samplerate 只查询一次，避免每段开 stream 都重新协商。"""
    query_calls: list[int] = []

    def fake_query_devices(device_index: int) -> dict[str, float]:
        query_calls.append(device_index)
        return {"default_samplerate": 48000.0}

    monkeypatch.setattr(playback_mod.sd, "query_devices", fake_query_devices)
    monkeypatch.setattr(playback_mod.sd, "play", lambda *_a, **_kw: None)

    sink = SoundDeviceAudioSink(device_index=4, sample_rate_hz=16000)
    sink.write(np.ones(1600, dtype=np.int16))
    sink.write(np.ones(1600, dtype=np.int16))
    sink.write(np.ones(1600, dtype=np.int16))

    assert query_calls == [4]


def test_sound_device_audio_sink_skips_when_no_samples(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """空样本不查设备、不调 sd.play。"""
    calls: list[str] = []

    monkeypatch.setattr(
        playback_mod.sd,
        "query_devices",
        lambda device_index: calls.append("query") or {"default_samplerate": 48000.0},
    )
    monkeypatch.setattr(playback_mod.sd, "play", lambda *_a, **_kw: calls.append("play"))

    sink = SoundDeviceAudioSink(device_index=4, sample_rate_hz=16000)
    sink.write(np.array([], dtype=np.int16))

    assert calls == []


def test_resample_pcm_to_rate_keeps_stereo_layout() -> None:
    """`_resample_pcm_to_rate` 对左右声道独立重采样并保留 (frames, 2) 排列。"""
    left = np.ones(160, dtype=np.int16) * 100
    right = np.ones(160, dtype=np.int16) * 300
    stereo = np.column_stack((left, right))

    resampled = _resample_pcm_to_rate(stereo, source_rate_hz=16000, target_rate_hz=48000)

    assert resampled.shape == (480, 2)
    assert int(resampled[0, 0]) == 100
    assert int(resampled[0, 1]) == 300


def test_streaming_sound_device_sink_callback_pulls_fed_pcm(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """feed_pcm 后，OutputStream callback 必须能立即拉出重采样后的 PCM。"""
    stream_factory = _install_fake_output_stream(monkeypatch, device_rate=48000)
    sink = StreamingSoundDeviceAudioSink(device_index=4, sample_rate_hz=16000)
    stream = stream_factory.stream

    asyncio.run(sink.feed_pcm(np.ones(160, dtype=np.int16) * 1000))
    outdata = np.zeros((480, 1), dtype=np.int16)
    stream.callback(outdata, 480, None, None)

    assert stream.started
    assert outdata.shape == (480, 1)
    assert np.any(outdata)
    assert sink.bytes_written == outdata.nbytes
    assert sink.first_payload_latency_s is not None
    assert sink.first_payload_latency_s >= 0
    asyncio.run(sink.flush_and_close())


def test_streaming_sound_device_sink_outputs_silence_when_queue_empty(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """队列空时 callback 必须输出静音帧，不能抛 underrun 异常。"""
    stream_factory = _install_fake_output_stream(monkeypatch, device_rate=16000)
    sink = StreamingSoundDeviceAudioSink(device_index=2, sample_rate_hz=16000)
    stream = stream_factory.stream

    outdata = np.ones((64, 1), dtype=np.int16) * 1234
    stream.callback(outdata, 64, None, None)

    assert outdata.tolist() == [[0] for _ in range(64)]
    assert sink.first_payload_latency_s is None
    asyncio.run(sink.flush_and_close())


def test_streaming_sound_device_sink_flush_waits_until_queue_drains(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """flush_and_close 必须等 callback 消费完队列后才 stop/close stream。"""
    stream_factory = _install_fake_output_stream(monkeypatch, device_rate=16000)
    sink = StreamingSoundDeviceAudioSink(device_index=2, sample_rate_hz=16000)
    stream = stream_factory.stream

    async def scenario() -> None:
        await sink.feed_pcm(np.ones(160, dtype=np.int16) * 500)
        flush_task = asyncio.create_task(sink.flush_and_close())
        await asyncio.sleep(0.01)
        assert not flush_task.done()
        assert not stream.stopped

        outdata = np.zeros((160, 1), dtype=np.int16)
        stream.callback(outdata, 160, None, None)
        await asyncio.wait_for(flush_task, timeout=1)

    asyncio.run(scenario())

    assert stream.stopped
    assert stream.closed


class _FakeOutputStream:
    """测试用 OutputStream，记录 callback 与生命周期。"""

    def __init__(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        del args
        self.callback = kwargs["callback"]
        self.samplerate = kwargs["samplerate"]
        self.device = kwargs["device"]
        self.channels = kwargs["channels"]
        self.dtype = kwargs["dtype"]
        self.started = False
        self.stopped = False
        self.closed = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def close(self) -> None:
        self.closed = True


class _FakeOutputStreamFactory:
    """记录最近创建的 OutputStream。"""

    def __init__(self) -> None:
        self.stream: _FakeOutputStream | None = None

    def __call__(self, *args, **kwargs) -> _FakeOutputStream:  # type: ignore[no-untyped-def]
        stream = _FakeOutputStream(*args, **kwargs)
        self.stream = stream
        return stream


def _install_fake_output_stream(monkeypatch, *, device_rate: int) -> _FakeOutputStreamFactory:  # type: ignore[no-untyped-def]
    stream_factory = _FakeOutputStreamFactory()
    monkeypatch.setattr(
        playback_mod.sd,
        "query_devices",
        lambda device_index: {"default_samplerate": float(device_rate)},
    )
    monkeypatch.setattr(playback_mod.sd, "OutputStream", stream_factory)
    return stream_factory
