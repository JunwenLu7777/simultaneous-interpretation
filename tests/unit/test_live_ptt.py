"""Push-to-talk 识别文本处理测试。"""

import numpy as np
import pytest

from teams_voice_interpreter import live_ptt
from teams_voice_interpreter.audio.routing import AudioDevice
from teams_voice_interpreter.data.audio_segment import AudioDirection
from teams_voice_interpreter.data.transcript import TranscriptKind
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


def _rms_only_speech_decider(frame: np.ndarray, *, vad: object, rms_threshold: float) -> bool:
    """测试桩：只用 RMS 决定一帧是否为人声，避开真实 webrtcvad 行为。"""
    del vad
    samples = np.asarray(frame, dtype=np.int16).reshape(-1)
    if samples.size == 0:
        return False
    rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))
    return rms >= rms_threshold


def test_streaming_microphone_recorder_segments_speech_after_tail_silence(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """连续监听应只输出有效人声段，并裁掉尾部静音。"""
    monkeypatch.setattr(live_ptt, "_is_speech_frame", _rms_only_speech_decider)
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


def test_streaming_microphone_recorder_flushes_open_speech_on_stream_end(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """输入流结束但尚未等到尾部静音时，已达最短人声的尾段不得被丢弃。"""
    monkeypatch.setattr(live_ptt, "_is_speech_frame", _rms_only_speech_decider)
    recorder = StreamingMicrophoneRecorder(sample_rate_hz=16000)
    speech = np.ones(480, dtype=np.int16) * 1000

    def fake_chunks(*, chunk_seconds: float, max_chunks: int | None = None):
        assert chunk_seconds == 0.03
        assert max_chunks is None
        yield speech
        yield speech
        yield speech

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


def test_streaming_microphone_recorder_marks_forced_split_continuation(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """长句因最大段长强切后，下一段必须标记为延续同一语流。"""
    monkeypatch.setattr(live_ptt, "_is_speech_frame", _rms_only_speech_decider)
    recorder = StreamingMicrophoneRecorder(sample_rate_hz=16000)
    speech = np.ones(480, dtype=np.int16) * 1000

    def fake_chunks(*, chunk_seconds: float, max_chunks: int | None = None):
        assert chunk_seconds == 0.03
        assert max_chunks is None
        for _ in range(5):
            yield speech

    monkeypatch.setattr(recorder, "chunks", fake_chunks)

    segments = list(
        recorder.speech_segments(
            max_segment_seconds=0.06,
            end_silence_ms=300,
            min_speech_ms=30,
            overlap_seconds=0.03,
            frame_ms=30,
            rms_threshold=160,
            max_segments=2,
        )
    )

    assert len(segments) == 2
    assert [segment.closed_by for segment in segments] == ["max_length", "max_length"]
    assert [segment.continues_previous for segment in segments] == [False, True]


def test_streaming_microphone_recorder_resets_forced_split_after_boundary_silence(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """强切后若先出现足够静音，下一句话不得误归入上一句 burst。"""
    monkeypatch.setattr(live_ptt, "_is_speech_frame", _rms_only_speech_decider)
    recorder = StreamingMicrophoneRecorder(sample_rate_hz=16000)
    silence = np.zeros(480, dtype=np.int16)
    speech = np.ones(480, dtype=np.int16) * 1000

    def fake_chunks(*, chunk_seconds: float, max_chunks: int | None = None):
        assert chunk_seconds == 0.03
        assert max_chunks is None
        yield speech
        yield speech
        yield silence
        yield silence
        yield speech
        yield speech

    monkeypatch.setattr(recorder, "chunks", fake_chunks)

    segments = list(
        recorder.speech_segments(
            max_segment_seconds=0.06,
            end_silence_ms=60,
            min_speech_ms=30,
            overlap_seconds=0,
            frame_ms=30,
            rms_threshold=160,
            max_segments=2,
        )
    )

    assert len(segments) == 2
    assert [segment.closed_by for segment in segments] == ["max_length", "max_length"]
    assert [segment.continues_previous for segment in segments] == [False, False]


def test_streaming_microphone_recorder_discards_too_short_speech(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """短促噪声不足最短人声阈值时不得送入 Whisper。"""
    monkeypatch.setattr(live_ptt, "_is_speech_frame", _rms_only_speech_decider)
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


@pytest.mark.parametrize(
    "raw_text",
    [
        "-",
        "。",
        "...",
        "!?",
        "*phone rings*",
        "*PHONE RINGING*",
        "(noise)",
        "[Music]",
        "[INAUDIBLE]",
        "12345",
        "[00:00:00]",
    ],
)
def test_transcript_text_from_segments_blocks_hallucination(raw_text: str) -> None:
    """单字符 / 纯标点 / 数字 / 已知音效占位都视为 Whisper 幻觉，不得送入 DeepSeek。"""
    with pytest.raises(UserFacingError) as exc_info:
        _transcript_text_from_segments([Segment(raw_text)])

    assert exc_info.value.code == "ptt.hallucinated_transcript"
    assert exc_info.value.what_happened.startswith("发生了什么")
    assert exc_info.value.next_action.startswith("下一步如何做")


def test_transcript_text_from_segments_keeps_two_chinese_chars() -> None:
    """两字以上汉字视为有效输入。"""
    assert _transcript_text_from_segments([Segment("好的")]) == "好的"


def test_transcript_text_from_segments_keeps_short_english_words() -> None:
    """`Hi` / `OK` 这类短英文回答必须保留，不能误判为幻觉。"""
    assert _transcript_text_from_segments([Segment("Hi")]) == "Hi"
    assert _transcript_text_from_segments([Segment("OK")]) == "OK"


@pytest.mark.parametrize(
    "raw_text",
    [
        "音声",
        "声音声",
        "字幕:BiC, 李宗盛",
        "字幕组提供",
        "謝謝觀看",
        "谢谢观看大家下次再见",
        "Subtitles by Bob",
        "Thanks for watching!",
        "请不吝点赞 订阅 转发 打赏支持明镜与点点栏目",
        "請不吝點讚 訂閱 轉發",
        "如果您喜欢本视频请点赞订阅",
        "记得点赞订阅支持一下",
        "Please subscribe and like!",
        "Like and subscribe for more videos",
        "Don't forget to subscribe to my channel",
        "感谢您的观看",
        "明镜与点点栏目期待您的关注",
        # ---- 路径 b 扩充：sachaarbonel/whisper-hallucinations 数据集与真测观测变体 ----
        "欢迎订阅我们的频道!",
        "欢迎收看!",
        "欢迎收听本期节目",
        "歡迎訂閱我們的頻道",
        "歡迎收看本期節目",
        "订阅我们的频道",
        "订阅频道获取更多",
        "關注我們获取更多",
        "关注我们获取更多",
        "请大家订阅",
        "請大家訂閱",
        "点击关注",
        "點擊關注",
        "点击订阅",
        "點擊訂閱",
        "一键三连",
        "一鍵三連",
        "三连支持",
        "三連支持",
        "本期视频到此结束",
        "本期節目到此結束",
        "本期视频就到这里",
        "本视频内容仅供参考",
        "以上是本期内容",
        "See you next time!",
        "See you in the next video",
        "Hit the bell icon",
        "Smash that like button",
        "Don't forget to hit subscribe",
        "If you enjoyed this video",
        "If you liked this video please",
    ],
)
def test_transcript_text_from_segments_blocks_known_training_set_hallucination(
    raw_text: str,
) -> None:
    """Whisper 在训练集片头/片尾上的常见幻觉必须被识别拦截，不送 DeepSeek。"""
    with pytest.raises(UserFacingError) as exc_info:
        _transcript_text_from_segments([Segment(raw_text)])

    assert exc_info.value.code == "ptt.hallucinated_transcript"


@pytest.mark.parametrize(
    "raw_text",
    [
        "直接直接直接直接直接直接接受你的语言",
        "性能。性能。性能。",
        "性格的性格的性格",
        "嗯嗯嗯嗯嗯嗯",
        "好的好的好的好的好的好的",
        "no no no no no",
        "the the the cat",
    ],
)
def test_transcript_text_from_segments_blocks_runaway_repeats(raw_text: str) -> None:
    """量化 Whisper 在 chunk 边界吐出的 N-gram 重复必须被拦截。"""
    with pytest.raises(UserFacingError) as exc_info:
        _transcript_text_from_segments([Segment(raw_text)])

    assert exc_info.value.code == "ptt.hallucinated_transcript"


@pytest.mark.parametrize(
    "raw_text",
    [
        "喂喂喂喂",  # 用户实际呼叫"喂喂喂喂"必须保留
        "嗯嗯嗯嗯",  # 4 个嗯属于真实填充语，长度尚未触发 ratio 规则
        "你好你好",
        "我们今天开始第一次测试",
        "Hello, can you hear me?",
    ],
)
def test_transcript_text_from_segments_keeps_legitimate_short_emphasis(raw_text: str) -> None:
    """真实用户的短重复 / 强调 / 正常对话不能被新过滤误伤。"""
    assert _transcript_text_from_segments([Segment(raw_text)]) == raw_text


def test_transcript_text_from_segments_returns_empty_on_pure_whitespace() -> None:
    """全空白由调用方统一抛 ptt.empty_transcript，不应在此处误升级为幻觉。"""
    assert _transcript_text_from_segments([Segment("   ")]) == ""


def test_is_speech_frame_rejects_high_energy_noise_when_vad_says_no() -> None:
    """Teams 提示音 / 风扇这类高 RMS 但 VAD 不认可的输入必须被拒。"""

    class FakeVad:
        def accept(self, _samples: np.ndarray) -> object:
            from teams_voice_interpreter.stt.vad import VadDecision

            return VadDecision(is_speech=False, should_close_segment=False)

    high_energy = np.ones(480, dtype=np.int16) * 5000
    assert (
        live_ptt._is_speech_frame(high_energy, vad=FakeVad(), rms_threshold=180)  # type: ignore[arg-type]
        is False
    )


def test_is_speech_frame_accepts_when_vad_and_rms_both_pass() -> None:
    """VAD 判定为人声且 RMS 高于一半阈值，应被识别为人声。"""

    class FakeVad:
        def accept(self, _samples: np.ndarray) -> object:
            from teams_voice_interpreter.stt.vad import VadDecision

            return VadDecision(is_speech=True, should_close_segment=False)

    speech_like = np.ones(480, dtype=np.int16) * 1000
    assert (
        live_ptt._is_speech_frame(speech_like, vad=FakeVad(), rms_threshold=180)  # type: ignore[arg-type]
        is True
    )


def test_is_speech_frame_rejects_low_energy_even_if_vad_says_speech() -> None:
    """RMS 低于一半阈值，即便 VAD 误判为人声也必须被拒，避免远端噪声触发幻觉。"""

    class FakeVad:
        def accept(self, _samples: np.ndarray) -> object:
            from teams_voice_interpreter.stt.vad import VadDecision

            return VadDecision(is_speech=True, should_close_segment=False)

    low_energy = np.ones(480, dtype=np.int16) * 30
    assert (
        live_ptt._is_speech_frame(low_energy, vad=FakeVad(), rms_threshold=180)  # type: ignore[arg-type]
        is False
    )


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


def test_whisper_transcriber_emits_stable_chunks_from_model_callback() -> None:
    """pywhispercpp new_segment_callback 必须进入 LocalAgreement 稳定增量边界。"""
    captured: dict[str, object] = {}

    class FakeModel:
        def transcribe(self, audio: np.ndarray, **params: object) -> list[Segment]:
            captured["audio_dtype"] = audio.dtype
            captured.update(params)
            callback = params["new_segment_callback"]
            assert callable(callback)
            callback(Segment("我们今天"))
            callback(Segment("讨论现金流"))
            callback(Segment("预测"))
            return [Segment("我们今天"), Segment("讨论现金流"), Segment("预测")]

    transcriber = WhisperOneShotTranscriber.__new__(WhisperOneShotTranscriber)
    transcriber.model_name = "small-q5_1"
    transcriber.language = "zh"
    transcriber.initial_prompt = "同声传译软件"
    transcriber._model = FakeModel()
    chunks = []

    text = transcriber.transcribe(
        np.array([0, 32767], dtype=np.int16),
        stable_chunk_callback=chunks.append,
    )

    assert text == "我们今天讨论现金流预测"
    assert [chunk.kind for chunk in chunks] == [
        TranscriptKind.PARTIAL,
        TranscriptKind.PARTIAL,
        TranscriptKind.FINAL,
    ]
    assert [chunk.text for chunk in chunks] == [
        "我们今天",
        "我们今天讨论现金流",
        "我们今天讨论现金流预测",
    ]
    assert [chunk.delta_text for chunk in chunks] == ["我们今天", "讨论现金流", "预测"]
    assert [chunk.revision for chunk in chunks] == [False, False, False]
    assert {chunk.direction for chunk in chunks} == {AudioDirection.UPLINK}
    assert captured["language"] == "zh"
    assert "new_segment_callback" in captured


def test_whisper_transcriber_marks_final_revision_without_duplicate_delta() -> None:
    """final 改写已提交前缀时，callback final 必须标记 revision 且不伪造 delta。"""

    class FakeModel:
        def transcribe(self, audio: np.ndarray, **params: object) -> list[Segment]:
            del audio
            callback = params["new_segment_callback"]
            assert callable(callback)
            callback(Segment("我们今天"))
            callback(Segment("讨论"))
            return [Segment("今天我们讨论")]

    transcriber = WhisperOneShotTranscriber.__new__(WhisperOneShotTranscriber)
    transcriber.model_name = "small-q5_1"
    transcriber.language = "zh"
    transcriber.initial_prompt = ""
    transcriber._model = FakeModel()
    chunks = []

    text = transcriber.transcribe(
        np.array([0, 32767], dtype=np.int16),
        stable_chunk_callback=chunks.append,
    )

    assert text == "今天我们讨论"
    assert [(chunk.kind, chunk.text, chunk.delta_text, chunk.revision) for chunk in chunks] == [
        (TranscriptKind.PARTIAL, "我们今天", "我们今天", False),
        (TranscriptKind.FINAL, "今天我们讨论", "", True),
    ]
