"""会话 Markdown 导出器。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from teams_voice_interpreter.data.audio_segment import AudioDirection
from teams_voice_interpreter.session.manager import PipelineResult, SessionManager


class ExportWindowExpiredError(Exception):
    """会话停止后 30 秒导出窗口已过期。"""


def export_markdown(manager: SessionManager, *, stopped_at: datetime | None = None) -> str:
    """导出双向时间戳交错 Markdown，不包含延迟、置信度或音频字节。"""
    if stopped_at and datetime.now(UTC) - stopped_at > timedelta(seconds=30):
        raise ExportWindowExpiredError
    lines = [
        "# Teams 同传会话导出",
        "",
        f"- SessionId: {manager.session.session_id}",
        f"- 导出时间: {datetime.now(UTC).isoformat()}",
        "",
        "## 对话",
        "",
    ]
    for direction in (AudioDirection.UPLINK, AudioDirection.DOWNLINK):
        result = manager.latest_results.get(direction)
        if result is not None:
            lines.extend(_render_result(result))
    return "\n".join(lines)


def _render_result(result: PipelineResult) -> list[str]:
    label = "上行" if result.direction is AudioDirection.UPLINK else "下行"
    return [
        f"### {label}",
        "",
        f"- 原文: {result.source_text}",
        f"- 译文: {result.target_text}",
        "",
    ]
