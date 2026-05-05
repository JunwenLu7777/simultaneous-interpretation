"""Push-to-talk 识别文本处理测试。"""

import numpy as np
import pytest

from teams_voice_interpreter import live_ptt
from teams_voice_interpreter.audio.routing import AudioDevice
from teams_voice_interpreter.errors import UserFacingError
from teams_voice_interpreter.live_ptt import (
    MicrophoneRecorder,
    StreamingBlackHoleRecorder,
    StreamingMicrophoneRecorder,
    WhisperOneShotTranscriber,
    _resample_int16_mono,
    _transcript_text_from_segments,
)


class Segment:
    """测试用 Whisper 片段。"""

    def __init__(self, text: str) -> None:
        self.text = text


def test_transcript_text_from_segments_blocks_blank_audio_marker() -> None:
    """Whisper 空音频占位符不得继续送入翻译和 TTS。"""
    with pytest.raises(UserFacingError) as exc_info:
        _transcript_text_from_segments([Segment("[BLANK_AUDIO]")])

    assert exc_info.value.code == "ptt.blank_audio"
    assert exc_info.value.what_happened.startswith("发生了什么")
    assert exc_info.value.next_action.startswith("下一步如何做")


def test_transcript_text_from_segments_keeps_real_text() -> None:
    """正常识别文本应原样进入翻译链路。"""
    text = _transcript_text_from_segments([Segment("你好"), Segment("，开始会议")])

    assert text == "你好，开始会议"


def test_resample_int16_mono_converts_native_input_rate_to_whisper_rate() -> None:
    """AirPods 这类 24 kHz 输入必须重采样到 Whisper 需要的 16 kHz。"""
    source = np.ones(24000, dtype=np.int16) * 1000

    samples = _resample_int16_mono(source, source_rate_hz=24000, target_rate_hz=16000)

    assert samples.dtype == np.int16
    assert samples.shape == (16000,)
    assert int(samples[0]) == 1000


def test_microphone_recorder_uses_device_native_rate_then_resamples(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """录音必须按设备原生采样率打开，再交给 Whisper 16 kHz PCM。"""
    captured: dict[str, object] = {}

    class FakeProbe:
        def get_default_input(self) -> AudioDevice:
            return AudioDevice(
                index=7,
                name="AirPods Pro",
                max_input_channels=1,
                max_output_channels=0,
            )

    def fake_query_devices(device_index: int) -> dict[str, float]:
        captured["query_device_index"] = device_index
        return {"default_samplerate": 24000.0}

    def fake_rec(
        frames: int,
        *,
        samplerate: int,
        channels: int,
        dtype: str,
        device: int,
    ) -> np.ndarray:
        captured.update(
            {
                "frames": frames,
                "samplerate": samplerate,
                "channels": channels,
                "dtype": dtype,
                "device": device,
            }
        )
        return np.ones((frames, 1), dtype=np.int16)

    monkeypatch.setattr(live_ptt.sd, "query_devices", fake_query_devices)
    monkeypatch.setattr(live_ptt.sd, "rec", fake_rec)
    monkeypatch.setattr(live_ptt.sd, "wait", lambda: None)

    samples = MicrophoneRecorder(sample_rate_hz=16000, device_probe=FakeProbe()).record(seconds=1)

    assert captured == {
        "query_device_index": 7,
        "frames": 24000,
        "samplerate": 24000,
        "channels": 1,
        "dtype": "int16",
        "device": 7,
    }
    assert samples.shape == (16000,)


def test_streaming_microphone_recorder_keeps_input_stream_open_while_yielding(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """连续监听必须用后台输入流采集，并按时间片吐出 16 kHz PCM。"""
    captured: dict[str, object] = {}

    class FakeProbe:
        def get_default_input(self) -> AudioDevice:
            return AudioDevice(
                index=7,
                name="AirPods Pro",
                max_input_channels=1,
                max_output_channels=0,
            )

    class FakeInputStream:
        def __init__(
            self,
            *,
            samplerate: int,
            channels: int,
            dtype: str,
            device: int,
            callback: object,
        ) -> None:
            captured.update(
                {
                    "samplerate": samplerate,
                    "channels": channels,
                    "dtype": dtype,
                    "device": device,
                }
            )
            self.callback = callback

        def __enter__(self) -> "FakeInputStream":
            self.callback(np.ones((12000, 1), dtype=np.int16), 12000, None, None)
            captured["entered"] = True
            return self

        def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
            captured["exited"] = True

    monkeypatch.setattr(
        live_ptt.sd,
        "query_devices",
        lambda device_index: {"default_samplerate": 24000.0},
    )
    monkeypatch.setattr(live_ptt.sd, "InputStream", FakeInputStream)

    chunks = list(
        StreamingMicrophoneRecorder(sample_rate_hz=16000, device_probe=FakeProbe()).chunks(
            chunk_seconds=0.5,
            max_chunks=1,
        )
    )

    assert captured == {
        "samplerate": 24000,
        "channels": 1,
        "dtype": "int16",
        "device": 7,
        "entered": True,
        "exited": True,
    }
    assert chunks[0].shape == (8000,)


def test_streaming_blackhole_recorder_downmixes_stereo_input(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """下行监听必须从 BlackHole 2ch 读取并 downmix 为 16 kHz mono。"""
    captured: dict[str, object] = {}

    class FakeProbe:
        def find_blackhole_2ch(self) -> AudioDevice:
            return AudioDevice(
                index=9,
                name="BlackHole 2ch",
                max_input_channels=2,
                max_output_channels=2,
            )

    class FakeInputStream:
        def __init__(
            self,
            *,
            samplerate: int,
            channels: int,
            dtype: str,
            device: int,
            callback: object,
        ) -> None:
            captured.update(
                {
                    "samplerate": samplerate,
                    "channels": channels,
                    "dtype": dtype,
                    "device": device,
                }
            )
            self.callback = callback

        def __enter__(self) -> "FakeInputStream":
            left = np.ones(12000, dtype=np.int16) * 100
            right = np.ones(12000, dtype=np.int16) * 300
            self.callback(np.column_stack((left, right)), 12000, None, None)
            return self

        def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
            captured["exited"] = True

    monkeypatch.setattr(
        live_ptt.sd,
        "query_devices",
        lambda device_index: {"default_samplerate": 24000.0},
    )
    monkeypatch.setattr(live_ptt.sd, "InputStream", FakeInputStream)

    chunks = list(
        StreamingBlackHoleRecorder(sample_rate_hz=16000, device_probe=FakeProbe()).chunks(
            chunk_seconds=0.5,
            max_chunks=1,
        )
    )

    assert captured == {
        "samplerate": 24000,
        "channels": 2,
        "dtype": "int16",
        "device": 9,
        "exited": True,
    }
    assert chunks[0].shape == (8000,)
    assert int(chunks[0][0]) == 200


def test_streaming_microphone_recorder_segments_speech_after_tail_silence(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """连续监听应只输出有效人声段，并裁掉尾部静音。"""
    recorder = StreamingMicrophoneRecorder(sample_rate_hz=16000)
    silence = np.zeros(480, dtype=np.int16)
    speech = np.ones(480, dtype=np.int16) * 1000

    def fake_chunks(*, chunk_seconds: float, max_chunks: int | None = None):
        assert chunk_seconds == 0.03
        assert max_chunks is None
        yield silence
        yield silence
        yield speech
        yield speech
        yield speech
        yield silence
        yield silence

    monkeypatch.setattr(recorder, "chunks", fake_chunks)

    segments = list(
        recorder.segments(
            max_segment_seconds=2,
            end_silence_ms=60,
            min_speech_ms=60,
            overlap_seconds=0,
            frame_ms=30,
            rms_threshold=160,
            max_segments=1,
        )
    )

    assert len(segments) == 1
    assert segments[0].shape == (1440,)
    assert np.all(segments[0] == 1000)


def test_streaming_microphone_recorder_discards_too_short_speech(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """短促噪声不足最短人声阈值时不得送入 Whisper。"""
    recorder = StreamingMicrophoneRecorder(sample_rate_hz=16000)
    silence = np.zeros(480, dtype=np.int16)
    speech = np.ones(480, dtype=np.int16) * 1000

    def fake_chunks(*, chunk_seconds: float, max_chunks: int | None = None):
        del chunk_seconds, max_chunks
        yield speech
        yield silence
        yield silence
        yield silence

    monkeypatch.setattr(recorder, "chunks", fake_chunks)

    segments = list(
        recorder.segments(
            max_segment_seconds=2,
            end_silence_ms=60,
            min_speech_ms=90,
            overlap_seconds=0,
            frame_ms=30,
            rms_threshold=160,
            max_segments=1,
        )
    )

    assert segments == []


def test_whisper_transcriber_forces_chinese_language_and_multi_segment() -> None:
    """中文输入不得依赖自动语种判断，也不得强制压成单段。"""
    captured: dict[str, object] = {}

    class FakeModel:
        def transcribe(self, audio: np.ndarray, **params: object) -> list[Segment]:
            captured["audio_dtype"] = audio.dtype
            captured.update(params)
            return [Segment("你好，我们开始测试")]

    transcriber = WhisperOneShotTranscriber.__new__(WhisperOneShotTranscriber)
    transcriber.model_name = "small-q5_1"
    transcriber.language = "zh"
    transcriber.initial_prompt = "同声传译软件"
    transcriber._model = FakeModel()

    text = transcriber.transcribe(np.array([0, 32767], dtype=np.int16))

    assert text == "你好，我们开始测试"
    assert captured == {
        "audio_dtype": np.dtype("float32"),
        "language": "zh",
        "no_context": True,
        "initial_prompt": "同声传译软件",
        "print_progress": False,
    }
