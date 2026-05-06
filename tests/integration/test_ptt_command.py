"""tvi ptt push-to-talk 命令测试。"""

import numpy as np
from typer.testing import CliRunner

from teams_voice_interpreter.audio.routing import AudioDevice
from teams_voice_interpreter.cli import app as cli_app
from teams_voice_interpreter.data.audio_segment import AudioDirection
from teams_voice_interpreter.errors import UserFacingError
from teams_voice_interpreter.live_say import PreparedSayResult, SayResult
from teams_voice_interpreter.stt.whisper_streaming import OnlineASRProcessor, WhisperStreamingConfig

runner = CliRunner()


def _rms_only_speech_decider(frame: np.ndarray, *, vad: object, rms_threshold: float) -> bool:
    """测试桩：只用 RMS 决定一帧是否为人声。"""
    del vad
    samples = np.asarray(frame, dtype=np.int16).reshape(-1)
    if samples.size == 0:
        return False
    rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))
    return rms >= rms_threshold


def _speech_frames(count: int, *, amplitude: int = 1000):
    for _ in range(count):
        yield np.ones(480, dtype=np.int16) * amplitude


def _silence_frames(count: int):
    for _ in range(count):
        yield np.zeros(480, dtype=np.int16)


def test_ptt_command_invokes_push_to_talk_bridge(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """ptt 命令应录音、识别并复用短句发声桥。"""
    captured: dict[str, object] = {}

    class FakeRecorder:
        def record(self, *, seconds: float) -> object:
            captured["seconds"] = seconds
            return object()

    class FakeTranscriber:
        def transcribe(self, samples: object) -> str:
            captured["samples"] = samples
            return "你好"

    class FakeSayBridge:
        async def say(self, text: str, *, direction: AudioDirection, target: str) -> SayResult:
            captured.update({"text": text, "direction": direction, "target": target})
            return SayResult(
                source_text=text,
                target_text="Hello",
                bytes_written=320,
                target_device_name="BlackHole 2ch",
            )

    class FakeBridge:
        recorder = FakeRecorder()
        transcriber = FakeTranscriber()
        say_bridge = FakeSayBridge()

    monkeypatch.setattr(cli_app, "LivePushToTalkBridge", lambda **kwargs: FakeBridge())

    result = runner.invoke(cli_app.app, ["ptt", "--seconds", "2", "--target", "blackhole"])

    assert result.exit_code == 0
    assert captured == {
        "seconds": 2.0,
        "samples": captured["samples"],
        "text": "你好",
        "direction": AudioDirection.UPLINK,
        "target": "blackhole",
    }
    assert "识别：你好" in result.output
    assert "译文：Hello" in result.output


def test_ptt_command_can_override_direction_for_local_output(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """ptt 本机输出测试可指定中文到英文方向。"""
    captured: dict[str, object] = {}

    class FakeRecorder:
        def record(self, *, seconds: float) -> object:
            captured["seconds"] = seconds
            return object()

    class FakeTranscriber:
        def transcribe(self, samples: object) -> str:
            captured["samples"] = samples
            return "你好"

    class FakeSayBridge:
        async def say(self, text: str, *, direction: AudioDirection, target: str) -> SayResult:
            captured.update({"text": text, "direction": direction, "target": target})
            return SayResult(
                source_text=text,
                target_text="Hello",
                bytes_written=320,
                target_device_name="AirPods Pro",
            )

    class FakeBridge:
        recorder = FakeRecorder()
        transcriber = FakeTranscriber()
        say_bridge = FakeSayBridge()

    monkeypatch.setattr(cli_app, "LivePushToTalkBridge", lambda **kwargs: FakeBridge())

    result = runner.invoke(
        cli_app.app,
        ["ptt", "--seconds", "2", "--target", "default", "--direction", "uplink"],
    )

    assert result.exit_code == 0
    assert captured["direction"] is AudioDirection.UPLINK
    assert captured["target"] == "default"
    assert "AirPods Pro" in result.output


def test_listen_rejects_online_asr_early_prepare_without_online_asr() -> None:
    """early prepare 开关必须绑定 online-asr，避免用户误以为已启用低延迟实验路径。"""
    result = runner.invoke(
        cli_app.app,
        [
            "listen",
            "--online-asr-early-prepare",
            "--chunks",
            "1",
        ],
    )

    assert result.exit_code == 1
    assert "必须同时启用 `--online-asr`" in result.output


def test_duplex_rejects_online_asr_early_prepare_without_online_asr() -> None:
    """duplex 在设备探测前就应拒绝无效 online-asr 参数组合。"""
    result = runner.invoke(
        cli_app.app,
        [
            "duplex",
            "--online-asr-early-prepare",
            "--chunks",
            "1",
        ],
    )

    assert result.exit_code == 1
    assert "必须同时启用 `--online-asr`" in result.output


def test_duplex_prints_route_errors_before_exit(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """duplex 路由探测失败时也必须输出两段式提示，而不是静默带异常退出。"""

    def fail_route(*, allow_shared_virtual_device: bool) -> cli_app._DuplexRoute:
        del allow_shared_virtual_device
        raise UserFacingError(
            code="audio.route_missing",
            what_happened="发生了什么：没有找到下行虚拟设备。",
            next_action="下一步如何做：请先配置 BlackHole 16ch。",
        )

    monkeypatch.setattr(cli_app, "_duplex_route", fail_route)

    result = runner.invoke(cli_app.app, ["duplex", "--chunks", "1"])

    assert result.exit_code == 1
    assert "发生了什么：没有找到下行虚拟设备。" in result.output
    assert "下一步如何做：请先配置 BlackHole 16ch。" in result.output
    assert result.exception is not None


def test_listen_command_processes_continuous_chunks(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """listen 命令应持续采集分片，不依赖每句说完后手动停顿。"""
    captured: dict[str, object] = {}

    class FakeStreamRecorder:
        def segments(
            self,
            *,
            max_segment_seconds: float,
            end_silence_ms: int,
            min_speech_ms: int,
            overlap_seconds: float,
            rms_threshold: float,
            max_segments: int | None,
            vad_backend: object = None,
        ):
            captured["max_segment_seconds"] = max_segment_seconds
            captured["end_silence_ms"] = end_silence_ms
            captured["min_speech_ms"] = min_speech_ms
            captured["overlap_seconds"] = overlap_seconds
            captured["rms_threshold"] = rms_threshold
            captured["max_segments"] = max_segments
            captured["vad_backend"] = vad_backend
            yield object()

    class FakeTranscriber:
        def transcribe(self, samples: object) -> str:
            captured["samples"] = samples
            return "你好吗，我今年三十岁，你爱我吗"

    class FakeSayBridge:
        async def prepare(
            self,
            text: str,
            *,
            direction: AudioDirection,
            target: str,
            streaming: bool = False,
            context_text: str = "",
        ) -> PreparedSayResult:
            del streaming, context_text
            captured.update({"text": text, "direction": direction, "target": target})
            return PreparedSayResult(
                source_text=text,
                target_text="How are you? I am 30. Do you love me?",
                pcm=np.ones(160, dtype=np.int16),
                target_device=AudioDevice(
                    index=1,
                    name="AirPods Pro",
                    max_input_channels=0,
                    max_output_channels=2,
                ),
                target=target,
                translation_latency_s=0.1,
                tts_latency_s=0.2,
                decode_latency_s=0.01,
            )

        def play_prepared(self, prepared: PreparedSayResult) -> SayResult:
            return SayResult(
                source_text=prepared.source_text,
                target_text=prepared.target_text,
                bytes_written=320,
                target_device_name=prepared.target_device.name,
                translation_latency_s=prepared.translation_latency_s,
                tts_latency_s=prepared.tts_latency_s,
                decode_latency_s=prepared.decode_latency_s,
                playback_latency_s=0.3,
            )

    class FakeBridge:
        transcriber = FakeTranscriber()
        say_bridge = FakeSayBridge()

    monkeypatch.setattr(cli_app, "LivePushToTalkBridge", lambda **kwargs: FakeBridge())
    monkeypatch.setattr(cli_app, "StreamingMicrophoneRecorder", lambda: FakeStreamRecorder())

    result = runner.invoke(
        cli_app.app,
        [
            "listen",
            "--chunk-seconds",
            "4",
            "--chunks",
            "1",
            "--end-silence-ms",
            "500",
            "--min-speech-ms",
            "300",
            "--overlap-seconds",
            "0.5",
            "--speech-rms-threshold",
            "180",
            "--hide-latency",
            "--target",
            "default",
            "--direction",
            "uplink",
        ],
    )

    assert result.exit_code == 0
    assert captured["max_segment_seconds"] == 4.0
    assert captured["end_silence_ms"] == 500
    assert captured["min_speech_ms"] == 300
    assert captured["overlap_seconds"] == 0.5
    assert captured["rms_threshold"] == 180.0
    assert captured["max_segments"] == 1
    assert captured["direction"] is AudioDirection.UPLINK
    assert "你好吗，我今年三十岁，你爱我吗" in result.output
    assert "How are you? I am 30. Do you love me?" in result.output


def test_listen_command_online_asr_prepares_stable_partial(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """显式开启 early-prepare 后，online-asr 才能在 VAD final 前准备稳定 partial。"""
    prepared_sources: list[str] = []
    prepare_contexts: list[str] = []
    scripted_texts = iter(
        [
            "我们今天讨论现金流预测",
            "我们今天讨论现金流预测方案",
            "我们今天讨论现金流预测方案和预算",
        ]
    )

    class FakeStreamRecorder:
        sample_rate_hz = 16000

        def chunks(self, *, chunk_seconds: float, max_chunks: int | None = None):
            del chunk_seconds, max_chunks
            for _ in range(20):
                yield np.ones(480, dtype=np.int16) * 1000
            yield np.zeros(480, dtype=np.int16)

    class FakeTranscriber:
        def transcribe(self, samples: object) -> str:
            del samples
            return next(scripted_texts)

    class FakeSayBridge:
        async def prepare(
            self,
            text: str,
            *,
            direction: AudioDirection,
            target: str,
            streaming: bool = False,
            context_text: str = "",
        ) -> PreparedSayResult:
            assert direction is AudioDirection.UPLINK
            assert target == "default"
            assert streaming
            prepared_sources.append(text)
            prepare_contexts.append(context_text)
            return PreparedSayResult(
                source_text=text,
                target_text=f"译:{text}",
                pcm=np.ones(160, dtype=np.int16),
                target_device=AudioDevice(1, "AirPods Pro", 0, 2),
                target=target,
                translation_latency_s=0.1,
                tts_latency_s=0.2,
                decode_latency_s=0.01,
            )

        def play_prepared(self, prepared: PreparedSayResult) -> SayResult:
            return SayResult(
                source_text=prepared.source_text,
                target_text=prepared.target_text,
                bytes_written=320,
                target_device_name=prepared.target_device.name,
                translation_latency_s=prepared.translation_latency_s,
                tts_latency_s=prepared.tts_latency_s,
                decode_latency_s=prepared.decode_latency_s,
                playback_latency_s=0.1,
            )

    class FakeBridge:
        transcriber = FakeTranscriber()
        say_bridge = FakeSayBridge()

    monkeypatch.setattr(cli_app, "LivePushToTalkBridge", lambda **kwargs: FakeBridge())
    monkeypatch.setattr(cli_app, "StreamingMicrophoneRecorder", lambda: FakeStreamRecorder())
    monkeypatch.setattr(cli_app, "_is_speech_frame", _rms_only_speech_decider)

    result = runner.invoke(
        cli_app.app,
        [
            "listen",
            "--online-asr",
            "--online-asr-early-prepare",
            "--chunks",
            "1",
            "--end-silence-ms",
            "30",
            "--min-speech-ms",
            "30",
            "--hide-latency",
            "--target",
            "default",
            "--direction",
            "uplink",
        ],
    )

    assert result.exit_code == 0
    assert prepared_sources == ["我们今天讨论现金流预测", "方案和预算"]
    assert prepare_contexts == ["", "我们今天讨论现金流预测"]
    assert "在线识别：我们今天讨论现金流预测方案和预算" in result.output
    assert "稳定译文：译:我们今天讨论现金流预测" in result.output


def test_listen_command_online_asr_does_not_prepare_partials_by_default(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """online-asr 默认不得用未真正确认的 partial 提前打 MT/TTS。"""
    prepared_sources: list[str] = []
    scripted_texts = iter(
        [
            "我们今天讨论现金流预测",
            "我们今天讨论现金流预测方案",
            "我们今天讨论现金流预测方案和预算",
        ]
    )

    class FakeStreamRecorder:
        sample_rate_hz = 16000

        def chunks(self, *, chunk_seconds: float, max_chunks: int | None = None):
            del chunk_seconds, max_chunks
            for _ in range(20):
                yield np.ones(480, dtype=np.int16) * 1000
            yield np.zeros(480, dtype=np.int16)

    class FakeTranscriber:
        def transcribe(self, samples: object) -> str:
            del samples
            return next(scripted_texts)

    class FakeSayBridge:
        async def prepare(
            self,
            text: str,
            *,
            direction: AudioDirection,
            target: str,
            streaming: bool = False,
            context_text: str = "",
        ) -> PreparedSayResult:
            del context_text
            assert direction is AudioDirection.UPLINK
            assert target == "default"
            assert streaming
            prepared_sources.append(text)
            return PreparedSayResult(
                source_text=text,
                target_text=f"译:{text}",
                pcm=np.ones(160, dtype=np.int16),
                target_device=AudioDevice(1, "AirPods Pro", 0, 2),
                target=target,
                translation_latency_s=0.1,
                tts_latency_s=0.2,
                decode_latency_s=0.01,
            )

        def play_prepared(self, prepared: PreparedSayResult) -> SayResult:
            return SayResult(
                source_text=prepared.source_text,
                target_text=prepared.target_text,
                bytes_written=320,
                target_device_name=prepared.target_device.name,
                translation_latency_s=prepared.translation_latency_s,
                tts_latency_s=prepared.tts_latency_s,
                decode_latency_s=prepared.decode_latency_s,
                playback_latency_s=0.1,
            )

    class FakeBridge:
        transcriber = FakeTranscriber()
        say_bridge = FakeSayBridge()

    monkeypatch.setattr(cli_app, "LivePushToTalkBridge", lambda **kwargs: FakeBridge())
    monkeypatch.setattr(cli_app, "StreamingMicrophoneRecorder", lambda: FakeStreamRecorder())
    monkeypatch.setattr(cli_app, "_is_speech_frame", _rms_only_speech_decider)

    result = runner.invoke(
        cli_app.app,
        [
            "listen",
            "--online-asr",
            "--chunks",
            "1",
            "--end-silence-ms",
            "30",
            "--min-speech-ms",
            "30",
            "--hide-latency",
            "--target",
            "default",
            "--direction",
            "uplink",
        ],
    )

    assert result.exit_code == 0
    assert prepared_sources == ["我们今天讨论现金流预测方案和预算"]
    assert "默认不让 stable partial 提前调用 MT/TTS" in result.output
    assert "稳定译文" not in result.output


def test_listen_command_online_asr_reuses_stable_suffix_after_forced_split(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """强切续段被 overlap 去重后，仍应复用已 final 确认的 stable suffix prepare。"""
    prepared_sources: list[str] = []
    prepare_contexts: list[str] = []
    scripted_texts = iter(
        [
            "我们今天讨论现金流预测方案",
            "我们今天讨论现金流预测方案",
            "我们今天讨论现金流预测方案",
            "预测方案和预算安排以及风险缓冲",
            "预测方案和预算安排以及风险缓冲",
            "预测方案和预算安排以及风险缓冲",
        ]
    )

    class FakeStreamRecorder:
        sample_rate_hz = 16000

        def chunks(self, *, chunk_seconds: float, max_chunks: int | None = None):
            del chunk_seconds, max_chunks
            yield from _speech_frames(3)
            yield from _speech_frames(3)
            yield from _silence_frames(1)

    class FakeTranscriber:
        def transcribe(self, samples: object) -> str:
            del samples
            return next(scripted_texts)

    class FakeSayBridge:
        async def prepare(
            self,
            text: str,
            *,
            direction: AudioDirection,
            target: str,
            streaming: bool = False,
            context_text: str = "",
        ) -> PreparedSayResult:
            assert direction is AudioDirection.UPLINK
            assert target == "default"
            assert streaming
            prepared_sources.append(text)
            prepare_contexts.append(context_text)
            return PreparedSayResult(
                source_text=text,
                target_text=f"译:{text}",
                pcm=np.ones(160, dtype=np.int16),
                target_device=AudioDevice(1, "AirPods Pro", 0, 2),
                target=target,
                translation_latency_s=0.1,
                tts_latency_s=0.2,
                decode_latency_s=0.01,
            )

        def play_prepared(self, prepared: PreparedSayResult) -> SayResult:
            return SayResult(
                source_text=prepared.source_text,
                target_text=prepared.target_text,
                bytes_written=320,
                target_device_name=prepared.target_device.name,
                translation_latency_s=prepared.translation_latency_s,
                tts_latency_s=prepared.tts_latency_s,
                decode_latency_s=prepared.decode_latency_s,
                playback_latency_s=0.1,
            )

    class FakeBridge:
        transcriber = FakeTranscriber()
        say_bridge = FakeSayBridge()

    monkeypatch.setattr(cli_app, "LivePushToTalkBridge", lambda **kwargs: FakeBridge())
    monkeypatch.setattr(cli_app, "StreamingMicrophoneRecorder", lambda: FakeStreamRecorder())
    monkeypatch.setattr(cli_app, "_is_speech_frame", _rms_only_speech_decider)
    monkeypatch.setattr(
        cli_app,
        "_new_online_asr_processor",
        lambda *, direction, bridge: OnlineASRProcessor(
            direction=direction,
            transcribe_buffer=bridge.transcriber.transcribe,
            config=WhisperStreamingConfig(step_ms=30),
        ),
    )

    result = runner.invoke(
        cli_app.app,
        [
            "listen",
            "--online-asr",
            "--online-asr-early-prepare",
            "--chunks",
            "2",
            "--chunk-seconds",
            "0.09",
            "--end-silence-ms",
            "30",
            "--min-speech-ms",
            "30",
            "--hide-latency",
            "--target",
            "default",
            "--direction",
            "uplink",
        ],
    )

    assert result.exit_code == 0
    assert prepared_sources == ["我们今天讨论现金流预测方案", "和预算安排以及风险缓冲"]
    assert prepare_contexts == ["", "我们今天讨论现金流预测方案"]
    assert "去重后：和预算安排以及风险缓冲" in result.output
    assert "稳定译文：译:和预算安排以及风险缓冲" in result.output


def test_listen_command_online_asr_flushes_tail_when_stream_ends(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """online-asr 输入流结束时必须收口已有人声尾段，不能等不到静音就丢。"""
    prepared_sources: list[str] = []

    class FakeStreamRecorder:
        sample_rate_hz = 16000

        def chunks(self, *, chunk_seconds: float, max_chunks: int | None = None):
            del chunk_seconds, max_chunks
            for _ in range(5):
                yield np.ones(480, dtype=np.int16) * 1000

    class FakeTranscriber:
        def transcribe(self, samples: object) -> str:
            assert np.asarray(samples).size > 0
            return "客户续费风险缓冲"

    class FakeSayBridge:
        async def prepare(
            self,
            text: str,
            *,
            direction: AudioDirection,
            target: str,
            streaming: bool = False,
            context_text: str = "",
        ) -> PreparedSayResult:
            del context_text
            assert direction is AudioDirection.UPLINK
            assert target == "default"
            assert streaming
            prepared_sources.append(text)
            return PreparedSayResult(
                source_text=text,
                target_text=f"译:{text}",
                pcm=np.ones(160, dtype=np.int16),
                target_device=AudioDevice(1, "AirPods Pro", 0, 2),
                target=target,
                translation_latency_s=0.1,
                tts_latency_s=0.2,
                decode_latency_s=0.01,
            )

        def play_prepared(self, prepared: PreparedSayResult) -> SayResult:
            return SayResult(
                source_text=prepared.source_text,
                target_text=prepared.target_text,
                bytes_written=320,
                target_device_name=prepared.target_device.name,
                translation_latency_s=prepared.translation_latency_s,
                tts_latency_s=prepared.tts_latency_s,
                decode_latency_s=prepared.decode_latency_s,
                playback_latency_s=0.1,
            )

    class FakeBridge:
        transcriber = FakeTranscriber()
        say_bridge = FakeSayBridge()

    monkeypatch.setattr(cli_app, "LivePushToTalkBridge", lambda **kwargs: FakeBridge())
    monkeypatch.setattr(cli_app, "StreamingMicrophoneRecorder", lambda: FakeStreamRecorder())
    monkeypatch.setattr(cli_app, "_is_speech_frame", _rms_only_speech_decider)

    result = runner.invoke(
        cli_app.app,
        [
            "listen",
            "--online-asr",
            "--chunks",
            "1",
            "--min-speech-ms",
            "30",
            "--hide-latency",
            "--target",
            "default",
            "--direction",
            "uplink",
        ],
    )

    assert result.exit_code == 0
    assert prepared_sources == ["客户续费风险缓冲"]
    assert "在线识别：客户续费风险缓冲" in result.output
    assert "译文：译:客户续费风险缓冲" in result.output


def test_duplex_command_runs_uplink_and_downlink_pipelines(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """duplex 命令应同时启动上行和下行，并按方向选择 ASR 语种与输出目标。"""
    languages: list[str] = []
    prepared: list[tuple[str, AudioDirection, str]] = []

    class FakeGate:
        def suppress_for(self, seconds: float) -> None:
            pass

        def is_suppressed(self) -> bool:
            return False

    class FakeStreamRecorder:
        def segments(
            self,
            *,
            max_segment_seconds: float,
            end_silence_ms: int,
            min_speech_ms: int,
            overlap_seconds: float,
            rms_threshold: float,
            max_segments: int | None,
            vad_backend: object = None,
        ):
            del (
                max_segment_seconds,
                end_silence_ms,
                min_speech_ms,
                overlap_seconds,
                rms_threshold,
                max_segments,
                vad_backend,
            )
            yield np.ones(160, dtype=np.int16)

    class FakeTranscriber:
        def __init__(self, language: str) -> None:
            self.language = language

        def transcribe(self, samples: object) -> str:
            del samples
            return "你好" if self.language == "zh" else "hello"

    class FakeSayBridge:
        async def prepare(
            self,
            text: str,
            *,
            direction: AudioDirection,
            target: str,
            streaming: bool = False,
            context_text: str = "",
        ) -> PreparedSayResult:
            del streaming, context_text
            prepared.append((text, direction, target))
            return PreparedSayResult(
                source_text=text,
                target_text="Hello" if direction is AudioDirection.UPLINK else "你好",
                pcm=np.ones(160, dtype=np.int16),
                target_device=AudioDevice(
                    index=1,
                    name="BlackHole 2ch" if target == "blackhole" else "AirPods Pro",
                    max_input_channels=2,
                    max_output_channels=2,
                ),
                target=target,
                translation_latency_s=0.1,
                tts_latency_s=0.2,
                decode_latency_s=0.01,
            )

        def play_prepared(self, item: PreparedSayResult) -> SayResult:
            return SayResult(
                source_text=item.source_text,
                target_text=item.target_text,
                bytes_written=320,
                target_device_name=item.target_device.name,
                translation_latency_s=item.translation_latency_s,
                tts_latency_s=item.tts_latency_s,
                decode_latency_s=item.decode_latency_s,
                playback_latency_s=0.1,
            )

    class FakeBridge:
        def __init__(self, *, source_language: str) -> None:
            languages.append(source_language)
            self.transcriber = FakeTranscriber(source_language)
            self.say_bridge = FakeSayBridge()

    monkeypatch.setattr(cli_app, "_PlaybackGate", FakeGate)
    monkeypatch.setattr(
        cli_app,
        "_duplex_route",
        lambda **kwargs: cli_app._DuplexRoute(
            uplink_device=AudioDevice(1, "TVI Uplink", 2, 2),
            downlink_device=AudioDevice(2, "TVI Downlink", 2, 2),
            shared_virtual_device=False,
        ),
    )
    monkeypatch.setattr(cli_app, "LivePushToTalkBridge", FakeBridge)
    monkeypatch.setattr(cli_app, "StreamingMicrophoneRecorder", lambda: FakeStreamRecorder())
    monkeypatch.setattr(
        cli_app,
        "StreamingBlackHoleRecorder",
        lambda **kwargs: FakeStreamRecorder(),
    )

    result = runner.invoke(cli_app.app, ["duplex", "--chunks", "1", "--hide-latency"])

    assert result.exit_code == 0
    assert sorted(languages) == ["en", "zh"]
    assert ("你好", AudioDirection.UPLINK, "blackhole") in prepared
    assert ("hello", AudioDirection.DOWNLINK, "default") in prepared
    assert "[上行 1] 译文：Hello" in result.output
    assert "[下行 1] 译文：你好" in result.output
