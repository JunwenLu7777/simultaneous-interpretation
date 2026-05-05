"""基于 flock 的单实例锁。"""

from __future__ import annotations

import atexit
import fcntl
import os
from pathlib import Path
from types import TracebackType
from typing import TextIO

from teams_voice_interpreter.errors import UserFacingError


class InstanceAlreadyRunningError(UserFacingError):
    """已有会话持有单实例锁。"""

    def __init__(self, lock_path: Path) -> None:
        super().__init__(
            code="session.instance_already_running",
            what_happened=f"发生了什么：已有一个 Teams 同传会话持有锁 {lock_path}。",
            next_action="下一步如何做：请先停止现有会话，或打开状态面板查看当前 SessionId。",
        )


class InstanceLock:
    """写入 PID / SessionId / 端口并通过 flock 排他持有。"""

    def __init__(self, lock_path: Path, *, session_id: str, web_port: int) -> None:
        self.lock_path = lock_path
        self.session_id = session_id
        self.web_port = web_port
        self._file: TextIO | None = None
        self.is_held = False

    def acquire(self) -> None:
        """获取锁；已被其他进程持有时抛两段式错误。"""
        if self.is_held:
            return
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_file = self.lock_path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            lock_file.close()
            raise InstanceAlreadyRunningError(self.lock_path) from error

        lock_file.seek(0)
        lock_file.truncate()
        lock_file.write(
            f"pid={os.getpid()}\nsession_id={self.session_id}\nweb_port={self.web_port}\n"
        )
        lock_file.flush()
        self._file = lock_file
        self.is_held = True
        atexit.register(self.release)

    def release(self) -> None:
        """幂等释放当前持有的锁。"""
        if self._file is None:
            self.is_held = False
            return
        fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        self._file.close()
        self._file = None
        self.is_held = False

    def __enter__(self) -> InstanceLock:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()
