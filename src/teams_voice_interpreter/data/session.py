"""Session 状态机模型。"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from teams_voice_interpreter.errors import UserFacingError


class SessionState(StrEnum):
    """会话生命周期状态。"""

    IDLE = "idle"
    STARTING = "starting"
    ACTIVE = "active"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERRORED = "errored"


class InvalidSessionTransitionError(UserFacingError):
    """非法会话状态转换。"""

    def __init__(self, *, current: SessionState, requested: str) -> None:
        super().__init__(
            code="session.invalid_transition",
            what_happened=f"发生了什么：当前会话状态 {current.value} 不允许执行 {requested}。",
            next_action="下一步如何做：请刷新状态后再选择可用操作。",
        )


class Session(BaseModel):
    """一次开始到停止之间的同传上下文。"""

    session_id: UUID
    state: SessionState = SessionState.IDLE
    started_at: datetime | None = None
    stopped_at: datetime | None = None
    uplink_enabled: bool = True
    downlink_enabled: bool = True
    web_port: int = Field(default=8765, ge=1024, le=65535)
    credential_ref_ids: list[str] = Field(default_factory=list)
    glossary_path: str | None = None
    glossary_loaded_count: int = Field(default=0, ge=0)
    transcripts: list[Any] = Field(default_factory=list)
    translations: list[Any] = Field(default_factory=list)
    latency_window: list[Any] = Field(default_factory=list)
    panel_recent_window_size: int = 100

    @classmethod
    def create(cls) -> Session:
        """创建一个新的本地会话。"""
        return cls(session_id=uuid4())

    def start(self) -> None:
        self._transition({SessionState.IDLE, SessionState.STOPPED}, SessionState.STARTING, "start")

    def ready(self) -> None:
        self._transition({SessionState.STARTING}, SessionState.ACTIVE, "ready")
        self.started_at = datetime.now(UTC)

    def fail(self) -> None:
        self._transition({SessionState.STARTING}, SessionState.ERRORED, "fail")

    def pause(self) -> None:
        self._transition({SessionState.ACTIVE}, SessionState.PAUSED, "pause")

    def resume(self) -> None:
        self._transition({SessionState.PAUSED}, SessionState.ACTIVE, "resume")

    def stop(self) -> None:
        self._transition({SessionState.ACTIVE, SessionState.PAUSED}, SessionState.STOPPING, "stop")

    def cleanup(self) -> None:
        self._transition({SessionState.STOPPING}, SessionState.STOPPED, "cleanup")
        self.stopped_at = datetime.now(UTC)
        self.transcripts.clear()
        self.translations.clear()

    def unrecoverable(self) -> None:
        self.state = SessionState.ERRORED

    def reset(self) -> None:
        self._transition({SessionState.ERRORED, SessionState.STOPPED}, SessionState.IDLE, "reset")

    def _transition(
        self,
        allowed: set[SessionState],
        next_state: SessionState,
        requested: str,
    ) -> None:
        if self.state not in allowed:
            raise InvalidSessionTransitionError(current=self.state, requested=requested)
        self.state = next_state
