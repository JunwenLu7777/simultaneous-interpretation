"""Markdown 导出集成测试。"""

from datetime import UTC, datetime, timedelta

import pytest

from teams_voice_interpreter.data.audio_segment import AudioDirection
from teams_voice_interpreter.session.exporter import ExportWindowExpiredError, export_markdown
from teams_voice_interpreter.session.manager import PipelineResult, SessionManager


def test_export_markdown_schema() -> None:
    """导出包含元数据和双向文本，但不包含延迟 / 置信度 / 音频字节。"""
    manager = SessionManager()
    manager.latest_results[AudioDirection.UPLINK] = PipelineResult(
        direction=AudioDirection.UPLINK,
        source_text="你好",
        target_text="hello",
        bytes_written=10,
        first_segment_latency_ms=1,
        full_latency_ms=2,
    )

    markdown = export_markdown(manager)

    assert "SessionId" in markdown
    assert "你好" in markdown
    assert "hello" in markdown
    assert "latency" not in markdown.lower()
    assert "bytes" not in markdown.lower()


def test_export_window_expired() -> None:
    """停止 30 秒后导出窗口过期。"""
    with pytest.raises(ExportWindowExpiredError):
        export_markdown(SessionManager(), stopped_at=datetime.now(UTC) - timedelta(seconds=31))
