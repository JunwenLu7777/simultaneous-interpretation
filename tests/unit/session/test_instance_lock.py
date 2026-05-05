"""单实例锁测试。"""

from pathlib import Path

import pytest

from teams_voice_interpreter.session.instance_lock import InstanceAlreadyRunningError, InstanceLock


def test_instance_lock_acquire_and_release(tmp_path: Path) -> None:
    """锁可以正常获取与幂等释放。"""
    lock = InstanceLock(tmp_path / "tvi.lock", session_id="session-1", web_port=8765)

    lock.acquire()
    lock.release()
    lock.release()

    assert not lock.is_held


def test_instance_lock_rejects_second_owner(tmp_path: Path) -> None:
    """同一锁文件同一时刻只能有一个持有者。"""
    first = InstanceLock(tmp_path / "tvi.lock", session_id="session-1", web_port=8765)
    second = InstanceLock(tmp_path / "tvi.lock", session_id="session-2", web_port=8766)
    first.acquire()

    with pytest.raises(InstanceAlreadyRunningError):
        second.acquire()

    first.release()


def test_stale_lock_file_is_rewritten(tmp_path: Path) -> None:
    """未被 flock 持有的旧锁文件会被新会话覆盖。"""
    lock_path = tmp_path / "tvi.lock"
    lock_path.write_text("pid=999999\nsession_id=old\nweb_port=8765\n", encoding="utf-8")
    lock = InstanceLock(lock_path, session_id="session-new", web_port=8767)

    lock.acquire()

    assert "session-new" in lock_path.read_text(encoding="utf-8")
    lock.release()
