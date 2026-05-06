"""CLI doctor / serve 命令契约测试。"""

from dataclasses import dataclass

from typer.testing import CliRunner

from teams_voice_interpreter.audio.routing import AudioDevice
from teams_voice_interpreter.cli import app as cli_app
from teams_voice_interpreter.readiness import CheckStatus, ReadinessCheck, ReadinessReport

runner = CliRunner()


@dataclass
class FakeChecker:
    """测试用 readiness checker。"""

    report: ReadinessReport

    def run(self) -> ReadinessReport:
        return self.report


class FakeSettings:
    """测试用本地配置。"""

    uplink_virtual_device_name = "BlackHole 2ch"

    def resolved_downlink_virtual_device_name(self) -> str:
        return "BlackHole 16ch"


def test_doctor_exits_nonzero_when_not_ready(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """readiness 未通过时 doctor 必须非 0 退出。"""
    report = ReadinessReport(
        checks=[
            ReadinessCheck(
                key="blackhole",
                title="BlackHole 2ch",
                status=CheckStatus.FAIL,
                detail="未找到",
                next_action="下一步如何做：请安装 BlackHole 2ch。",
            )
        ]
    )
    monkeypatch.setattr(cli_app, "ReadinessChecker", lambda **_: FakeChecker(report))

    result = runner.invoke(cli_app.app, ["doctor"])

    assert result.exit_code == 1
    assert "未就绪" in result.output
    assert "BlackHole 2ch" in result.output


def test_doctor_accepts_teams_route_confirmation(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """用户确认 Teams 路由后，doctor 应把确认传入 checker。"""
    captured: dict[str, object] = {}
    report = ReadinessReport(
        checks=[
            ReadinessCheck(
                key="blackhole",
                title="BlackHole 2ch",
                status=CheckStatus.PASS,
                detail="OK",
                next_action="",
            )
        ]
    )

    def build_checker(**kwargs: object) -> FakeChecker:
        captured.update(kwargs)
        return FakeChecker(report)

    monkeypatch.setattr(cli_app, "ReadinessChecker", build_checker)

    result = runner.invoke(cli_app.app, ["doctor", "--confirm-teams-route"])

    assert result.exit_code == 0
    assert captured["teams_route_confirmed"] is True
    assert captured["mode"] == "phrase"
    assert "已就绪" in result.output


def test_doctor_prints_info_checks_without_failing(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """INFO 检查项应展示为说明，不得让 doctor 非 0。"""
    report = ReadinessReport(
        checks=[
            ReadinessCheck(
                key="latency_scope",
                title="低延迟状态说明",
                status=CheckStatus.INFO,
                detail="doctor 不证明低延迟达标",
                required=False,
            )
        ]
    )
    monkeypatch.setattr(cli_app, "ReadinessChecker", lambda **_: FakeChecker(report))

    result = runner.invoke(cli_app.app, ["doctor"])

    assert result.exit_code == 0
    assert "[INFO] 低延迟状态说明: doctor 不证明低延迟达标" in result.output


def test_serve_invokes_uvicorn_with_local_binding(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """serve 命令必须使用 localhost 绑定启动 Web 控制台。"""
    captured: dict[str, object] = {}

    def fake_run(app_path: str, *, host: str, port: int) -> None:
        captured.update({"app_path": app_path, "host": host, "port": port})

    monkeypatch.setattr(cli_app.uvicorn, "run", fake_run)

    result = runner.invoke(cli_app.app, ["serve", "--port", "8877"])

    assert result.exit_code == 0
    assert captured == {
        "app_path": "teams_voice_interpreter.web.server:app",
        "host": "127.0.0.1",
        "port": 8877,
    }


def test_wizard_command_is_available(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """quickstart 中声明的 wizard 命令必须存在。"""
    report = ReadinessReport(
        checks=[
            ReadinessCheck(
                key="blackhole",
                title="BlackHole 2ch",
                status=CheckStatus.PASS,
                detail="OK",
                next_action="",
            )
        ]
    )
    monkeypatch.setattr(cli_app, "ReadinessChecker", lambda **_: FakeChecker(report))

    result = runner.invoke(cli_app.app, ["wizard"])

    assert result.exit_code == 0
    assert "首次使用向导" in result.output
    assert "已就绪" in result.output


def test_devices_lists_defaults_and_physical_candidates(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """devices 命令应列出默认设备、虚拟路由和真实候选。"""

    class FakeProbe:
        def get_default_input(self) -> AudioDevice:
            return AudioDevice(2, "BlackHole 2ch", 2, 2)

        def get_default_output(self) -> AudioDevice:
            return AudioDevice(3, "Mac mini扬声器", 0, 2)

        def input_devices(self) -> list[AudioDevice]:
            return [
                AudioDevice(1, "BlackHole 16ch", 16, 16),
                AudioDevice(2, "BlackHole 2ch", 2, 2),
            ]

        def output_devices(self) -> list[AudioDevice]:
            return [
                AudioDevice(2, "BlackHole 2ch", 2, 2),
                AudioDevice(3, "Mac mini扬声器", 0, 2),
            ]

    monkeypatch.setattr(cli_app, "AudioDeviceProbe", FakeProbe)
    monkeypatch.setattr(cli_app, "load_settings", lambda **_: FakeSettings())

    result = runner.invoke(cli_app.app, ["devices"])

    assert result.exit_code == 0
    assert "#2 BlackHole 2ch" in result.output
    assert "[default, virtual-route]" in result.output
    assert "真实输入候选：未检测到" in result.output
    assert "真实输出候选：Mac mini扬声器 (index=3)" in result.output
