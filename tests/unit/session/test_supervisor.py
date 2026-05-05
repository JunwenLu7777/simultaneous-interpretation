"""Supervisor 单元测试。"""

from datetime import UTC, datetime, timedelta

from teams_voice_interpreter.data.audio_segment import AudioDirection
from teams_voice_interpreter.session.supervisor import ServiceSupervisor


def test_heartbeat_timeout() -> None:
    """heartbeat 超过 3 秒视为卡死。"""
    supervisor = ServiceSupervisor()
    supervisor.last_heartbeat["whisper"] = datetime.now(UTC) - timedelta(seconds=4)

    assert supervisor.is_heartbeat_timed_out("whisper")


def test_retry_event_order() -> None:
    """FR-018 retry 状态必须先于最终失败事件。"""
    supervisor = ServiceSupervisor()

    events = supervisor.emit_retry_then_failure("edge-tts", AudioDirection.DOWNLINK)

    assert [event.kind for event in events] == ["retrying", "failed"]
