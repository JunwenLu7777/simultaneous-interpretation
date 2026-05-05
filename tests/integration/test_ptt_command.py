"""tvi ptt push-to-talk 命令测试。"""

import numpy as np
from typer.testing import CliRunner

from teams_voice_interpreter.audio.routing import AudioDevice
from teams_voice_interpreter.cli import app as cli_app
from teams_voice_interpreter.data.audio_segment import AudioDirection
from teams_voice_interpreter.live_say import PreparedSayResult, SayResult

runner = CliRunner()


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
        ):
            captured["max_segment_seconds"] = max_segment_seconds
            captured["end_silence_ms"] = end_silence_ms
            captured["min_speech_ms"] = min_speech_ms
            captured["overlap_seconds"] = overlap_seconds
            captured["rms_threshold"] = rms_threshold
            captured["max_segments"] = max_segments
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
        ) -> PreparedSayResult:
            del streaming
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
        ):
            del (
                max_segment_seconds,
                end_silence_ms,
                min_speech_ms,
                overlap_seconds,
                rms_threshold,
                max_segments,
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
        ) -> PreparedSayResult:
            del streaming
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
