"""Session 状态机测试。"""

import pytest

from teams_voice_interpreter.data.session import (
    InvalidSessionTransitionError,
    Session,
    SessionState,
)


def test_session_happy_path_transitions() -> None:
    """会话必须按 IDLE → STARTING → ACTIVE → PAUSED → ACTIVE → STOPPING → STOPPED 流转。"""
    session = Session.create()

    session.start()
    session.ready()
    session.pause()
    session.resume()
    session.stop()
    session.cleanup()

    assert session.state is SessionState.STOPPED
    assert session.started_at is not None
    assert session.stopped_at is not None


def test_session_rejects_illegal_transition() -> None:
    """非法状态转换必须被明确拒绝。"""
    session = Session.create()

    with pytest.raises(InvalidSessionTransitionError):
        session.pause()


def test_stopped_session_clears_transcripts() -> None:
    """停止后若未导出，内存中的完整双语对照必须释放。"""
    session = Session.create()
    session.transcripts.append(object())
    session.translations.append(object())

    session.start()
    session.ready()
    session.stop()
    session.cleanup()

    assert session.transcripts == []
    assert session.translations == []
