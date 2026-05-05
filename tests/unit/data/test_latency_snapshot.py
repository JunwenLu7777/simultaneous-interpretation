"""LatencySnapshot 与 LatencyRecorder 完整统计测试。"""

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from teams_voice_interpreter.data.audio_segment import AudioDirection
from teams_voice_interpreter.data.latency import LatencyStage
from teams_voice_interpreter.perf import LatencyRecorder


def test_latency_recorder_matches_numpy_percentile() -> None:
    """p50 / p95 / avg / max 必须与 numpy.percentile 对齐。"""
    recorder = LatencyRecorder(window_seconds=60)
    now = datetime.now(UTC)
    values = [100.0, 200.0, 300.0, 400.0]
    for value in values:
        recorder.record(
            stage=LatencyStage.E2E_FIRST_SEG,
            direction=AudioDirection.UPLINK,
            duration_ms=value,
            measured_at=now,
        )
    recorder.record(
        stage=LatencyStage.E2E_FIRST_SEG,
        direction=AudioDirection.UPLINK,
        duration_ms=999,
        measured_at=now - timedelta(seconds=120),
    )

    snapshot = recorder.snapshot(now=now)

    assert snapshot.p50[LatencyStage.E2E_FIRST_SEG] == pytest.approx(np.percentile(values, 50))
    assert snapshot.p95[LatencyStage.E2E_FIRST_SEG] == pytest.approx(np.percentile(values, 95))
    assert snapshot.avg[LatencyStage.E2E_FIRST_SEG] == sum(values) / len(values)
    assert snapshot.max[LatencyStage.E2E_FIRST_SEG] == 400
