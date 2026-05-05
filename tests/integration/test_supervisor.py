"""US4 supervisor 集成测试。"""

from teams_voice_interpreter.data.audio_segment import AudioDirection
from teams_voice_interpreter.session.manager import SessionManager
from teams_voice_interpreter.session.supervisor import ServiceSupervisor


def test_supervisor_recovery_scenarios() -> None:
    """设备切换、暂停继续、retry、respawn 均保留上下文。"""
    manager = SessionManager()
    manager.start()
    manager.pause()
    manager.start()

    context = {"session_id": str(manager.session.session_id), "glossary": ["DeepSeek"]}
    restored = manager.supervisor.respawn_preserving_context("whisper", context)
    events = manager.supervisor.emit_retry_then_failure("deepseek", AudioDirection.UPLINK)

    assert manager.session.state.value == "active"
    assert restored == context
    assert [event.kind for event in events] == ["retrying", "failed"]


def test_supervisor_circuit_break_after_three_crashes() -> None:
    """60 秒内 3 次崩溃触发熔断。"""
    supervisor = ServiceSupervisor()

    assert not supervisor.record_crash("whisper")
    assert not supervisor.record_crash("whisper")
    assert supervisor.record_crash("whisper")
