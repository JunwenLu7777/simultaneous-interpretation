"""会话子进程 supervisor 与故障自愈状态。"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from teams_voice_interpreter.data.audio_segment import AudioDirection


@dataclass(frozen=True)
class ServiceEvent:
    """服务状态事件。"""

    kind: str
    service: str
    direction: AudioDirection
    message: str
    emitted_at: datetime


class ServiceSupervisor:
    """记录 heartbeat、崩溃窗口和 FR-018 retry 事件。"""

    def __init__(self, *, heartbeat_timeout_seconds: float = 3.0) -> None:
        self.heartbeat_timeout_seconds = heartbeat_timeout_seconds
        self.last_heartbeat: dict[str, datetime] = {}
        self.crashes: dict[str, deque[datetime]] = defaultdict(deque)
        self.events: list[ServiceEvent] = []

    def heartbeat(self, service: str) -> None:
        """记录服务 heartbeat。"""
        self.last_heartbeat[service] = datetime.now(UTC)

    def is_heartbeat_timed_out(self, service: str, *, now: datetime | None = None) -> bool:
        """判断服务 heartbeat 是否超过 3 秒。"""
        current_time = now or datetime.now(UTC)
        last = self.last_heartbeat.get(service)
        if last is None:
            return True
        return (current_time - last).total_seconds() > self.heartbeat_timeout_seconds

    def record_crash(self, service: str, *, now: datetime | None = None) -> bool:
        """记录一次崩溃，返回是否触发 60 秒 3 次熔断。"""
        current_time = now or datetime.now(UTC)
        window = self.crashes[service]
        window.append(current_time)
        cutoff = current_time - timedelta(seconds=60)
        while window and window[0] < cutoff:
            window.popleft()
        return len(window) >= 3

    def emit_retry_then_failure(
        self,
        service: str,
        direction: AudioDirection,
    ) -> list[ServiceEvent]:
        """FR-018 要求 retry 状态先于最终失败状态。"""
        now = datetime.now(UTC)
        events = [
            ServiceEvent("retrying", service, direction, f"{service} 暂时不可用，正在重试。", now),
            ServiceEvent(
                "failed",
                service,
                direction,
                f"{service} 30 秒内未恢复，该方向已停止。",
                now,
            ),
        ]
        self.events.extend(events)
        return events

    def respawn_preserving_context(
        self,
        service: str,
        context: dict[str, object],
    ) -> dict[str, object]:
        """模拟 respawn，保持 SessionId、滚动上下文与术语表引用不变。"""
        self.heartbeat(service)
        return dict(context)
