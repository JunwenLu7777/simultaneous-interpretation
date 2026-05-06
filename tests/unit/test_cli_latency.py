"""CLI 延迟剖面输出测试。"""

from __future__ import annotations

import numpy as np

from teams_voice_interpreter.audio.routing import AudioDevice
from teams_voice_interpreter.cli import app as cli_app
from teams_voice_interpreter.live_say import PreparedSayResult, SayResult


def test_listen_latency_splits_queue_wait_and_first_write(capsys) -> None:  # type: ignore[no-untyped-def]
    """`--show-latency` 必须拆出 prepare、排队、首 PCM 与首写延迟。"""
    prepared = PreparedSayResult(
        source_text="hello",
        target_text="你好",
        target_device=AudioDevice(1, "AirPods Pro", 0, 2),
        target="default",
        translation_latency_s=1.5,
        tts_latency_s=0.0,
        decode_latency_s=0.0,
        pcm=np.array([], dtype=np.int16),
    )
    item = cli_app._PendingPlayback(
        index=1,
        label="下行",
        prepared=prepared,
        started=8.0,
        transcribed_at=10.0,
        prepared_at=12.0,
        queue_depth_at_enqueue=2,
        dropped_pending_before_enqueue=1,
        show_latency=True,
    )
    result = SayResult(
        source_text="hello",
        target_text="你好",
        bytes_written=320,
        target_device_name="AirPods Pro",
        translation_latency_s=1.5,
        playback_latency_s=2.0,
        first_pcm_latency_s=0.25,
        first_playback_write_latency_s=0.4,
    )

    cli_app._print_listen_latency(
        item,
        result=result,
        playback_started=13.0,
        completed_at=16.0,
    )

    output = capsys.readouterr().out
    assert "prepare墙钟 2.00s" in output
    assert "排队 1.00s(q=2,drop=1)" in output
    assert "首PCM 3.25s" in output
    assert "首写 3.40s" in output
    assert "首字节 3.40s" in output


def test_drop_pending_playbacks_drops_only_old_burst() -> None:
    """新 burst 入队前必须丢跨 burst 旧段；同 burst pending 必须 FIFO 保留。"""
    playback_queue: cli_app.queue.Queue[cli_app._PendingPlayback | None] = cli_app.queue.Queue()
    old_burst = _pending_item(index=1, burst_id=1)
    same_burst = _pending_item(index=2, burst_id=2)
    playback_queue.put(old_burst)
    playback_queue.put(same_burst)

    dropped = cli_app._drop_pending_playbacks(playback_queue, current_burst_id=2)

    assert dropped == 1
    remaining = playback_queue.get_nowait()
    assert remaining.index == 2  # 同 burst (burst_id=2) 段保留
    assert playback_queue.empty()


def test_drop_pending_playbacks_keeps_all_items_in_same_burst() -> None:
    """同 burst 多段（如长句切片）必须全部保留，不丢中间段。"""
    playback_queue: cli_app.queue.Queue[cli_app._PendingPlayback | None] = cli_app.queue.Queue()
    seg1 = _pending_item(index=1, burst_id=5)
    seg2 = _pending_item(index=2, burst_id=5)
    seg3 = _pending_item(index=3, burst_id=5)
    playback_queue.put(seg1)
    playback_queue.put(seg2)
    playback_queue.put(seg3)

    dropped = cli_app._drop_pending_playbacks(playback_queue, current_burst_id=5)

    assert dropped == 0
    assert [playback_queue.get_nowait().index for _ in range(3)] == [1, 2, 3]


def test_burst_tracker_groups_close_segments() -> None:
    """相邻段 transcribed_at 间隔 < gap_threshold 必须共享同一 burst_id。"""
    tracker = cli_app._BurstTracker(gap_threshold_s=1.0)

    burst_a = tracker.assign(transcribed_at=100.0)
    burst_b = tracker.assign(transcribed_at=100.5)
    burst_c = tracker.assign(transcribed_at=101.4)

    assert burst_a == burst_b == burst_c


def test_burst_tracker_increments_after_long_gap() -> None:
    """距上一段 > gap_threshold 必须递增 burst_id 视为新 utterance。"""
    tracker = cli_app._BurstTracker(gap_threshold_s=1.0)

    first = tracker.assign(transcribed_at=100.0)
    same = tracker.assign(transcribed_at=100.5)
    new_burst = tracker.assign(transcribed_at=102.0)

    assert first == same
    assert new_burst > same


def test_listen_playback_worker_keeps_same_burst_despite_stale_wait(  # type: ignore[no-untyped-def]
    capsys,
) -> None:
    """同 burst 段必须豁免 stale 检查，避免长句切片的后段被误丢。"""
    playback_queue: cli_app.queue.Queue[cli_app._PendingPlayback | None] = cli_app.queue.Queue()
    fresh_first = _pending_item(index=1, prepared_at=99.5, burst_id=7)
    stale_followup = _pending_item(
        index=2,
        prepared_at=100.0 - cli_app.REALTIME_STALE_PLAYBACK_WAIT_SECONDS - 0.1,
        burst_id=7,
    )
    playback_queue.put(fresh_first)
    playback_queue.put(stale_followup)
    playback_queue.put(None)

    original_perf_counter = cli_app.time.perf_counter
    cli_app.time.perf_counter = lambda: 100.0
    try:
        cli_app._listen_playback_worker(playback_queue, _RecordingBridge())
    finally:
        cli_app.time.perf_counter = original_perf_counter

    output = capsys.readouterr().out
    assert "已丢弃：译音等待播放" not in output


def test_playback_worker_skips_stale_item(capsys) -> None:  # type: ignore[no-untyped-def]
    """等待播放太久的译音必须跳过，避免实时会话播出过期内容。"""
    playback_queue: cli_app.queue.Queue[cli_app._PendingPlayback | None] = cli_app.queue.Queue()
    stale_item = _pending_item(
        index=2,
        prepared_at=100.0 - cli_app.REALTIME_STALE_PLAYBACK_WAIT_SECONDS - 0.1,
    )
    playback_queue.put(stale_item)
    playback_queue.put(None)

    original_perf_counter = cli_app.time.perf_counter
    cli_app.time.perf_counter = lambda: 100.0
    try:
        cli_app._listen_playback_worker(playback_queue, _ExplodingBridge())
    finally:
        cli_app.time.perf_counter = original_perf_counter

    output = capsys.readouterr().out
    assert "已丢弃：译音等待播放" in output


def _pending_item(
    *, index: int, prepared_at: float = 12.0, burst_id: int = 0
) -> cli_app._PendingPlayback:
    prepared = PreparedSayResult(
        source_text="hello",
        target_text="你好",
        target_device=AudioDevice(1, "AirPods Pro", 0, 2),
        target="default",
        translation_latency_s=1.5,
        tts_latency_s=0.0,
        decode_latency_s=0.0,
        pcm=np.array([], dtype=np.int16),
    )
    return cli_app._PendingPlayback(
        index=index,
        label="下行",
        prepared=prepared,
        started=8.0,
        transcribed_at=10.0,
        prepared_at=prepared_at,
        queue_depth_at_enqueue=0,
        dropped_pending_before_enqueue=0,
        show_latency=True,
        burst_id=burst_id,
    )


class _ExplodingSayBridge:
    def play_prepared(self, prepared: PreparedSayResult) -> SayResult:
        del prepared
        raise AssertionError("stale item must not be played")


class _ExplodingBridge:
    say_bridge = _ExplodingSayBridge()


class _RecordingSayBridge:
    """假 bridge：play_prepared / play_prepared_streaming 直接返回 SayResult，不真合成。"""

    def play_prepared(self, prepared: PreparedSayResult) -> SayResult:
        return SayResult(
            source_text=prepared.source_text,
            target_text=prepared.target_text,
            bytes_written=0,
            target_device_name=prepared.target_device.name,
            translation_latency_s=prepared.translation_latency_s,
        )


class _RecordingBridge:
    say_bridge = _RecordingSayBridge()
