"""单调计时与滚动延迟统计。"""

from __future__ import annotations

import math
import time
from collections import defaultdict
from datetime import UTC, datetime
from types import TracebackType
from uuid import UUID

from teams_voice_interpreter.data.audio_segment import AudioDirection
from teams_voice_interpreter.data.latency import LatencySample, LatencySnapshot, LatencyStage


class Stopwatch:
    """使用单调时间生成一条延迟样本。"""

    def __init__(
        self,
        *,
        stage: LatencyStage,
        direction: AudioDirection,
        associated_segment_id: UUID | None = None,
    ) -> None:
        self.stage = stage
        self.direction = direction
        self.associated_segment_id = associated_segment_id
        self.sample: LatencySample | None = None
        self._started_at: float | None = None

    def __enter__(self) -> Stopwatch:
        self._started_at = time.monotonic()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._started_at is None:
            return
        duration_ms = (time.monotonic() - self._started_at) * 1000
        self.sample = LatencySample(
            stage=self.stage,
            direction=self.direction,
            duration_ms=duration_ms,
            measured_at=datetime.now(UTC),
            associated_segment_id=self.associated_segment_id,
        )


class LatencyRecorder:
    """保留滚动窗口内样本并输出 p50 / p95 / avg / max。"""

    def __init__(self, *, window_seconds: int = 60) -> None:
        self.window_seconds = window_seconds
        self._samples: list[LatencySample] = []

    def record(
        self,
        *,
        stage: LatencyStage,
        direction: AudioDirection,
        duration_ms: float,
        measured_at: datetime | None = None,
        associated_segment_id: UUID | None = None,
    ) -> LatencySample:
        """写入一条延迟样本。"""
        sample = LatencySample(
            stage=stage,
            direction=direction,
            duration_ms=duration_ms,
            measured_at=measured_at or datetime.now(UTC),
            associated_segment_id=associated_segment_id,
        )
        self._samples.append(sample)
        return sample

    def snapshot(self, *, now: datetime | None = None) -> LatencySnapshot:
        """生成当前滚动窗口统计。"""
        current_time = now or datetime.now(UTC)
        self._prune(current_time)

        grouped: dict[LatencyStage, list[float]] = defaultdict(list)
        for sample in self._samples:
            grouped[sample.stage].append(sample.duration_ms)

        return LatencySnapshot(
            window_seconds=self.window_seconds,
            samples_per_stage=dict(grouped),
            p50={stage: _percentile(values, 50) for stage, values in grouped.items()},
            p95={stage: _percentile(values, 95) for stage, values in grouped.items()},
            avg={stage: sum(values) / len(values) for stage, values in grouped.items()},
            max={stage: max(values) for stage, values in grouped.items()},
        )

    def _prune(self, now: datetime) -> None:
        self._samples = [
            sample
            for sample in self._samples
            if (now - sample.measured_at).total_seconds() <= self.window_seconds
        ]


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * (percentile / 100)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[int(position)]
    lower_value = ordered[lower]
    upper_value = ordered[upper]
    weight = position - lower
    return lower_value + (upper_value - lower_value) * weight
