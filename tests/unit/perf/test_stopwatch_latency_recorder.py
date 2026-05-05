"""延迟计时器与滚动统计测试。"""

from datetime import UTC, datetime, timedelta

from teams_voice_interpreter.data.audio_segment import AudioDirection
from teams_voice_interpreter.data.latency import LatencyStage
from teams_voice_interpreter.perf import LatencyRecorder, Stopwatch


def test_stopwatch_records_non_negative_duration() -> None:
    """Stopwatch 必须使用单调时间并记录非负延迟。"""
    with Stopwatch(stage=LatencyStage.AUDIO_CAPTURE, direction=AudioDirection.UPLINK) as stopwatch:
        pass

    assert stopwatch.sample is not None
    assert stopwatch.sample.duration_ms >= 0


def test_latency_recorder_empty_snapshot() -> None:
    """空窗口应返回空统计而不是异常。"""
    recorder = LatencyRecorder(window_seconds=60)

    snapshot = recorder.snapshot()

    assert snapshot.p50 == {}
    assert snapshot.p95 == {}


def test_latency_recorder_rolls_window_and_computes_stats() -> None:
    """滚动窗口只保留最近样本并计算 p50/p95/avg/max。"""
    recorder = LatencyRecorder(window_seconds=60)
    old_time = datetime.now(UTC) - timedelta(seconds=120)
    new_time = datetime.now(UTC)

    recorder.record(
        stage=LatencyStage.MT_FIRST_TOKEN,
        direction=AudioDirection.UPLINK,
        duration_ms=999,
        measured_at=old_time,
    )
    recorder.record(
        stage=LatencyStage.MT_FIRST_TOKEN,
        direction=AudioDirection.UPLINK,
        duration_ms=100,
        measured_at=new_time,
    )
    recorder.record(
        stage=LatencyStage.MT_FIRST_TOKEN,
        direction=AudioDirection.UPLINK,
        duration_ms=200,
        measured_at=new_time,
    )

    snapshot = recorder.snapshot(now=new_time)

    assert snapshot.p50[LatencyStage.MT_FIRST_TOKEN] == 150
    assert snapshot.p95[LatencyStage.MT_FIRST_TOKEN] == 195
    assert snapshot.avg[LatencyStage.MT_FIRST_TOKEN] == 150
    assert snapshot.max[LatencyStage.MT_FIRST_TOKEN] == 200
