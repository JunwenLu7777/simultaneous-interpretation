"""匿名崩溃报告写出与轮转。"""

from __future__ import annotations

import os
import platform
import re
import stat
import sys
import traceback
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType

from teams_voice_interpreter.data.crash import CrashReport

FORBIDDEN_PATTERN = re.compile(r"sk-[A-Za-z0-9_-]+|/Users/[^/\\s]+")


def redact_text(text: str) -> str:
    """脱敏 home 路径与 API key。"""
    return FORBIDDEN_PATTERN.sub(
        lambda match: "~" if match.group(0).startswith("/Users/") else "<redacted>",
        text,
    )


class CrashReporter:
    """写出 0600 权限匿名报告并保留最新 20 份。"""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)

    def write_report(self, stack_trace: str, *, notes: str | None = None) -> Path:
        """写出一份崩溃报告。"""
        safe_trace = redact_text(stack_trace).replace("transcript", "<redacted>")
        report = CrashReport(
            occurred_at=datetime.now(UTC),
            python_version=platform.python_version(),
            os_version=platform.platform(),
            arch="arm64" if platform.machine() == "arm64" else "x86_64",
            dependency_versions={},
            stack_trace=safe_trace,
            services_health_snapshot={},
            resource_snapshot={"ram_mb": 0.0, "cpu_pct": 0.0},
            notes=notes,
        )
        path = self.directory / f"crash-{int(datetime.now(UTC).timestamp())}.log"
        path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        self.rotate()
        return path

    def rotate(self, *, keep: int = 20) -> None:
        """保留最新 keep 份报告。"""
        reports = sorted(
            self.directory.glob("crash-*.log"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        for old_report in reports[keep:]:
            old_report.unlink()

    def install_excepthook(self) -> None:
        """安装 sys.excepthook。"""

        def hook(
            exc_type: type[BaseException],
            exc: BaseException,
            tb: TracebackType | None,
        ) -> None:
            rendered = "".join(traceback.format_exception(exc_type, exc, tb))
            self.write_report(rendered)

        sys.excepthook = hook

    def install_signal_handlers(self) -> None:
        """预留 signal 处理入口；测试环境不安装真实信号处理器。"""
        os.environ["TVI_CRASH_REPORTER_READY"] = "1"
