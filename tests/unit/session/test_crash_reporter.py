"""匿名崩溃报告测试。"""

import stat
from pathlib import Path

from teams_voice_interpreter.session.crash_reporter import CrashReporter, redact_text


def test_redact_text() -> None:
    """家目录与 API Key 必须脱敏。"""
    assert "/Users/alice" not in redact_text("/Users/alice/app sk-live-secret")
    assert "sk-live-secret" not in redact_text("/Users/alice/app sk-live-secret")


def test_write_report_permissions_and_rotation(tmp_path: Path) -> None:
    """报告权限为 0600，且只保留最新 20 份。"""
    reporter = CrashReporter(tmp_path)
    for index in range(25):
        reporter.write_report(f"stack {index}")

    reports = list(tmp_path.glob("crash-*.log"))
    assert len(reports) <= 20
    assert stat.S_IMODE(reports[0].stat().st_mode) == stat.S_IRUSR | stat.S_IWUSR
