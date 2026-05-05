"""tvi say 最小真实发声路径测试。"""

from typer.testing import CliRunner

from teams_voice_interpreter.cli import app as cli_app
from teams_voice_interpreter.data.audio_segment import AudioDirection
from teams_voice_interpreter.live_say import SayResult

runner = CliRunner()


def test_say_command_invokes_live_bridge(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """say 命令应调用短句发声桥并输出译文。"""
    captured: dict[str, object] = {}

    class FakeBridge:
        async def say(self, text: str, *, direction: AudioDirection, target: str) -> SayResult:
            captured.update({"text": text, "direction": direction, "target": target})
            return SayResult(
                source_text=text,
                target_text="Hello",
                bytes_written=320,
                target_device_name="BlackHole 2ch",
            )

    monkeypatch.setattr(cli_app, "LiveSayBridge", lambda: FakeBridge())

    result = runner.invoke(cli_app.app, ["say", "你好", "--target", "blackhole"])

    assert result.exit_code == 0
    assert captured["text"] == "你好"
    assert captured["direction"] is AudioDirection.UPLINK
    assert "Hello" in result.output


def test_say_command_can_override_direction_for_local_output(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """本机输出测试时可指定中文到英文，不受 target=default 推断限制。"""
    captured: dict[str, object] = {}

    class FakeBridge:
        async def say(self, text: str, *, direction: AudioDirection, target: str) -> SayResult:
            captured.update({"text": text, "direction": direction, "target": target})
            return SayResult(
                source_text=text,
                target_text="Hello",
                bytes_written=320,
                target_device_name="AirPods Pro",
            )

    monkeypatch.setattr(cli_app, "LiveSayBridge", lambda: FakeBridge())

    result = runner.invoke(
        cli_app.app,
        ["say", "你好", "--target", "default", "--direction", "uplink"],
    )

    assert result.exit_code == 0
    assert captured["target"] == "default"
    assert captured["direction"] is AudioDirection.UPLINK
    assert "AirPods Pro" in result.output
