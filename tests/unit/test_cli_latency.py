"""CLI 延迟剖面输出测试。"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

import numpy as np

from teams_voice_interpreter.audio.routing import AudioDevice
from teams_voice_interpreter.cli import app as cli_app
from teams_voice_interpreter.data.audio_segment import AudioDirection
from teams_voice_interpreter.data.transcript import StableTranscriptChunk, TranscriptKind
from teams_voice_interpreter.errors import UserFacingError
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


def test_listen_latency_reports_timing_inversion_without_clamping(capsys) -> None:  # type: ignore[no-untyped-def]
    """online-asr 时间戳倒挂时必须显式暴露，不能钳成 0.00s 假低延迟。"""
    prepared = PreparedSayResult(
        source_text="hello",
        target_text="你好",
        target_device=AudioDevice(1, "AirPods Pro", 0, 2),
        target="default",
        translation_latency_s=0.2,
        tts_latency_s=0.0,
        decode_latency_s=0.0,
        pcm=np.array([], dtype=np.int16),
    )
    item = cli_app._PendingPlayback(
        index=1,
        label="上行",
        prepared=prepared,
        started=12.0,
        transcribed_at=10.0,
        prepared_at=10.5,
        queue_depth_at_enqueue=0,
        dropped_pending_before_enqueue=0,
        show_latency=True,
        final_transcribed_at=12.3,
    )
    result = SayResult(
        source_text="hello",
        target_text="你好",
        bytes_written=320,
        target_device_name="AirPods Pro",
        translation_latency_s=0.2,
        playback_latency_s=0.4,
        first_pcm_latency_s=0.05,
        first_playback_write_latency_s=0.1,
    )

    cli_app._print_listen_latency(
        item,
        result=result,
        playback_started=12.4,
        completed_at=12.8,
    )

    output = capsys.readouterr().out
    assert "ASR 0.30s" in output
    assert "源文可用 n/a" in output
    assert "prepare墙钟 0.50s" in output
    assert "排队 1.90s(q=0,drop=0)" in output
    assert "总计 0.80s" in output
    assert "计时异常" in output
    assert "ASR 0.00s" not in output
    assert "总计 0.00s" not in output


def test_drop_pending_playbacks_preserves_old_burst_by_default() -> None:
    """默认不得丢跨 burst pending，避免长句分片被误判新 burst 后丢失。"""
    playback_queue: cli_app.queue.Queue[cli_app._PendingPlayback | None] = cli_app.queue.Queue()
    old_burst = _pending_item(index=1, burst_id=1)
    same_burst = _pending_item(index=2, burst_id=2)
    playback_queue.put(old_burst)
    playback_queue.put(same_burst)

    dropped = cli_app._drop_pending_playbacks(playback_queue, current_burst_id=2)

    assert dropped == 0
    assert [playback_queue.get_nowait().index for _ in range(2)] == [1, 2]
    assert playback_queue.empty()


def test_drop_pending_playbacks_can_drop_old_burst_when_explicitly_enabled() -> None:
    """未来显式低延迟实验可启用旧 burst 清理；默认路径不使用。"""
    playback_queue: cli_app.queue.Queue[cli_app._PendingPlayback | None] = cli_app.queue.Queue()
    old_burst = _pending_item(index=1, burst_id=1)
    same_burst = _pending_item(index=2, burst_id=2)
    playback_queue.put(old_burst)
    playback_queue.put(same_burst)

    dropped = cli_app._drop_pending_playbacks(
        playback_queue,
        current_burst_id=2,
        drop_old_bursts=True,
    )

    assert dropped == 1
    assert playback_queue.get_nowait().index == 2
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


def test_enqueue_prepared_playback_drops_old_bursts_only_when_backlog_over_budget(
    capsys,
) -> None:  # type: ignore[no-untyped-def]
    """积压超过预算时可丢旧 burst，但同一长句 burst 的 pending 仍必须保留。"""
    playback_queue: cli_app.queue.Queue[cli_app._PendingPlayback | None] = cli_app.queue.Queue()
    for index in range(1, cli_app.REALTIME_PLAYBACK_OLD_BURST_DROP_DEPTH):
        playback_queue.put(_pending_item(index=index, burst_id=1))
    same_burst = _pending_item(index=99, burst_id=2)
    playback_queue.put(same_burst)

    cli_app._enqueue_prepared_playback(
        playback_queue,
        index=100,
        label="上行",
        prepared=same_burst.prepared,
        started=1.0,
        transcribed_at=2.0,
        prepared_at=3.0,
        show_latency=True,
        burst_id=2,
        final_transcribed_at=2.0,
    )

    output = capsys.readouterr().out
    assert "已丢弃 7 个跨 burst 旧段" in output
    assert [playback_queue.get_nowait().index for _ in range(2)] == [99, 100]


def test_burst_tracker_groups_forced_split_segments() -> None:
    """VAD 标记为强切延续的相邻段必须共享同一 burst_id。"""
    tracker = cli_app._BurstTracker()

    burst_a = tracker.assign(continues_previous=False)
    burst_b = tracker.assign(continues_previous=True)
    burst_c = tracker.assign(continues_previous=True)

    assert burst_a == burst_b == burst_c


def test_burst_tracker_increments_after_vad_boundary() -> None:
    """VAD 静音/flush 后的新段必须递增 burst_id。"""
    tracker = cli_app._BurstTracker()

    first = tracker.assign(continues_previous=False)
    same = tracker.assign(continues_previous=True)
    new_burst = tracker.assign(continues_previous=False)

    assert first == same
    assert new_burst > same


def test_forced_split_burst_keeps_overlap_dedup_active() -> None:
    """6 秒强切后的连续长句仍应保持同 burst，从而去掉 overlap 前缀。"""
    tracker = cli_app._BurstTracker()
    deduplicator = cli_app._TranscriptOverlapDeduplicator()

    first_burst = tracker.assign(continues_previous=False)
    first = deduplicator.accept("我们今天讨论现金流预测方案", burst_id=first_burst)
    second_burst = tracker.assign(continues_previous=True)
    second = deduplicator.accept("预测方案和预算安排", burst_id=second_burst)

    assert first_burst == second_burst
    assert first == "我们今天讨论现金流预测方案"
    assert second == "和预算安排"


def test_listen_playback_worker_keeps_same_burst_despite_stale_wait(  # type: ignore[no-untyped-def]
    capsys,
) -> None:
    """同 burst 段必须豁免 stale 检查，避免长句切片的后段被误丢。"""
    playback_queue: cli_app.queue.Queue[cli_app._PendingPlayback | None] = cli_app.queue.Queue()
    fresh_first = _pending_item(index=1, prepared_at=99.5, burst_id=7)
    stale_followup = _pending_item(
        index=2,
        prepared_at=96.9,
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


def test_listen_playback_worker_does_not_truncate_by_default(capsys) -> None:  # type: ignore[no-untyped-def]
    """实时路径默认必须完整播放，不能用截断长句换低延迟。"""
    playback_queue: cli_app.queue.Queue[cli_app._PendingPlayback | None] = cli_app.queue.Queue()
    playback_queue.put(_pending_item(index=1, prepared_at=cli_app.time.perf_counter(), burst_id=9))
    playback_queue.put(None)
    bridge = _RecordingStreamingBridge()

    cli_app._listen_playback_worker(playback_queue, bridge)

    output = capsys.readouterr().out
    assert bridge.say_bridge.max_playback_seconds_values == [None]
    assert "截断" not in output


def test_playback_worker_keeps_stale_item_by_default(capsys) -> None:  # type: ignore[no-untyped-def]
    """默认不得因 stale 窗口跳过译音，避免长句切片排队后丢失。"""
    playback_queue: cli_app.queue.Queue[cli_app._PendingPlayback | None] = cli_app.queue.Queue()
    stale_item = _pending_item(index=2, prepared_at=96.9)
    playback_queue.put(stale_item)
    playback_queue.put(None)

    original_perf_counter = cli_app.time.perf_counter
    cli_app.time.perf_counter = lambda: 100.0
    try:
        cli_app._listen_playback_worker(playback_queue, _RecordingBridge())
    finally:
        cli_app.time.perf_counter = original_perf_counter

    output = capsys.readouterr().out
    assert "已丢弃：译音等待播放" not in output


def test_playback_worker_can_skip_stale_item_when_window_enabled(capsys) -> None:  # type: ignore[no-untyped-def]
    """显式启用 stale 窗口时才允许跳过过期译音；默认路径不启用。"""
    playback_queue: cli_app.queue.Queue[cli_app._PendingPlayback | None] = cli_app.queue.Queue()
    playback_queue.put(_pending_item(index=2, prepared_at=96.9))
    playback_queue.put(None)

    original_perf_counter = cli_app.time.perf_counter
    original_stale_window = cli_app.REALTIME_STALE_PLAYBACK_WAIT_SECONDS
    cli_app.time.perf_counter = lambda: 100.0
    cli_app.REALTIME_STALE_PLAYBACK_WAIT_SECONDS = 3.0
    try:
        cli_app._listen_playback_worker(playback_queue, _ExplodingBridge())
    finally:
        cli_app.time.perf_counter = original_perf_counter
        cli_app.REALTIME_STALE_PLAYBACK_WAIT_SECONDS = original_stale_window

    output = capsys.readouterr().out
    assert "已丢弃：译音等待播放" in output


def test_transcript_overlap_deduplicator_removes_forced_split_overlap() -> None:
    """强制切段带来的音频 overlap 不得导致译文重复播。"""
    deduplicator = cli_app._TranscriptOverlapDeduplicator()

    first = deduplicator.accept("我们今天讨论现金流预测方案", burst_id=1)
    second = deduplicator.accept("预测方案和预算安排", burst_id=1)

    assert first == "我们今天讨论现金流预测方案"
    assert second == "和预算安排"


def test_transcript_overlap_deduplicator_resets_between_bursts() -> None:
    """新 burst 不能沿用上一句尾巴去重，避免误删真实重复表达。"""
    deduplicator = cli_app._TranscriptOverlapDeduplicator()

    deduplicator.accept("我们今天讨论现金流预测方案", burst_id=1)
    next_burst = deduplicator.accept("预测方案需要重新评估", burst_id=2)

    assert next_burst == "预测方案需要重新评估"


def test_transcript_overlap_deduplicator_handles_english_word_overlap() -> None:
    """英文 overlap 只按完整词去重，避免切掉半个词。"""
    deduplicator = cli_app._TranscriptOverlapDeduplicator()

    deduplicator.accept("we discuss cash flow forecast", burst_id=1)
    emitted = deduplicator.accept("flow forecast and budget risk", burst_id=1)

    assert emitted == "and budget risk"


def test_transcript_overlap_deduplicator_preserves_partial_word_prefix() -> None:
    """英文 overlap 命中半个词时不得去重，避免误删真实内容。"""
    deduplicator = cli_app._TranscriptOverlapDeduplicator()

    deduplicator.accept("we discuss cash flow forecast", burst_id=1)
    emitted = deduplicator.accept("low forecast and budget risk", burst_id=1)

    assert emitted == "low forecast and budget risk"


def test_transcript_overlap_deduplicator_preserves_entire_repeated_segment() -> None:
    """整段都像 overlap 时宁可重复播放，也不得静默丢掉用户真实重复表达。"""
    deduplicator = cli_app._TranscriptOverlapDeduplicator()

    deduplicator.accept("客户续费风险缓冲", burst_id=1)
    emitted = deduplicator.accept("风险缓冲", burst_id=1)

    assert emitted == "风险缓冲"


def test_prepare_listen_segment_warns_backlog_without_dropping(capsys) -> None:  # type: ignore[no-untyped-def]
    """播放积压时必须保留内容并显式告警，不能静默丢段或阻塞在小队列上。"""
    playback_queue: cli_app.queue.Queue[cli_app._PendingPlayback | None] = cli_app.queue.Queue()
    for index in range(1, cli_app.REALTIME_PLAYBACK_BACKLOG_WARNING_DEPTH + 1):
        playback_queue.put(_pending_item(index=index, burst_id=1))

    cli_app._prepare_listen_segment(
        index=99,
        samples=np.ones(16000, dtype=np.int16),
        playback_queue=playback_queue,
        bridge=_PreparingBridge(),
        direction=cli_app.AudioDirection.DOWNLINK,
        target="default",
        label="下行",
        show_latency=True,
        playback_gate=None,
    )

    output = capsys.readouterr().out
    assert "实时播放积压 3 段" in output
    assert playback_queue.qsize() == 4
    assert [playback_queue.get_nowait().index for _ in range(4)] == [1, 2, 3, 99]


def test_prepare_listen_segment_enqueues_confirmed_stable_chunks(capsys) -> None:  # type: ignore[no-untyped-def]
    """final 确认稳定增量后，应播放翻译单元序列而不是等待整句重新 prepare。"""
    playback_queue: cli_app.queue.Queue[cli_app._PendingPlayback | None] = cli_app.queue.Queue()
    bridge = _StablePreparingBridge(
        [
            _stable_chunk(TranscriptKind.PARTIAL, text="我们今天", delta_text="我们今天"),
            _stable_chunk(
                TranscriptKind.PARTIAL,
                text="我们今天讨论现金流预测方案",
                delta_text="讨论现金流预测方案",
            ),
            _stable_chunk(
                TranscriptKind.FINAL,
                text="我们今天讨论现金流预测方案和预算",
                delta_text="和预算",
            ),
        ]
    )

    cli_app._prepare_listen_segment(
        index=7,
        samples=np.ones(16000, dtype=np.int16),
        playback_queue=playback_queue,
        bridge=bridge,  # type: ignore[arg-type]
        direction=cli_app.AudioDirection.UPLINK,
        target="blackhole",
        label="上行",
        show_latency=True,
        playback_gate=None,
    )

    output = capsys.readouterr().out
    assert "稳定译文" in output
    assert bridge.say_bridge.prepared_sources == ["我们今天讨论现金流预测方案", "和预算"]
    assert bridge.say_bridge.prepare_contexts == ["", "我们今天讨论现金流预测方案"]
    queued_items = [playback_queue.get_nowait() for _ in range(2)]
    assert [item.prepared.source_text for item in queued_items] == [
        "我们今天讨论现金流预测方案",
        "和预算",
    ]
    assert all(item.prepared_at >= item.transcribed_at for item in queued_items)


def test_prepare_listen_segment_gives_later_stable_delta_current_prefix_context(
    capsys,
) -> None:  # type: ignore[no-untyped-def]
    """同一 segment 内后续 stable delta 翻译时必须带上当前句内已确认前缀。"""
    playback_queue: cli_app.queue.Queue[cli_app._PendingPlayback | None] = cli_app.queue.Queue()
    bridge = _StablePreparingBridge(
        [
            _stable_chunk(
                TranscriptKind.PARTIAL,
                text="我们今天讨论现金流预测方案",
                delta_text="我们今天讨论现金流预测方案",
            ),
            _stable_chunk(
                TranscriptKind.PARTIAL,
                text="我们今天讨论现金流预测方案和预算安排以及风险缓冲",
                delta_text="和预算安排以及风险缓冲",
            ),
            _stable_chunk(
                TranscriptKind.FINAL,
                text="我们今天讨论现金流预测方案和预算安排以及风险缓冲",
                delta_text="",
            ),
        ]
    )

    cli_app._prepare_listen_segment(
        index=12,
        samples=np.ones(16000, dtype=np.int16),
        playback_queue=playback_queue,
        bridge=bridge,  # type: ignore[arg-type]
        direction=cli_app.AudioDirection.UPLINK,
        target="blackhole",
        label="上行",
        show_latency=True,
        playback_gate=None,
    )

    output = capsys.readouterr().out
    assert "稳定译文" in output
    assert bridge.say_bridge.prepared_sources == [
        "我们今天讨论现金流预测方案",
        "和预算安排以及风险缓冲",
    ]
    assert bridge.say_bridge.prepare_contexts == ["", "我们今天讨论现金流预测方案"]


def test_prepare_listen_segment_falls_back_on_final_revision(capsys) -> None:  # type: ignore[no-untyped-def]
    """final 改写已提交前缀时，不得把早准备的 partial 放进播放队列。"""
    playback_queue: cli_app.queue.Queue[cli_app._PendingPlayback | None] = cli_app.queue.Queue()
    bridge = _StablePreparingBridge(
        [
            _stable_chunk(
                TranscriptKind.PARTIAL,
                text="我们今天讨论现金流预测方案",
                delta_text="我们今天讨论现金流预测方案",
            ),
            _stable_chunk(
                TranscriptKind.FINAL,
                text="今天我们讨论现金流预测方案",
                delta_text="",
                revision=True,
            ),
        ]
    )

    cli_app._prepare_listen_segment(
        index=8,
        samples=np.ones(16000, dtype=np.int16),
        playback_queue=playback_queue,
        bridge=bridge,  # type: ignore[arg-type]
        direction=cli_app.AudioDirection.UPLINK,
        target="blackhole",
        label="上行",
        show_latency=True,
        playback_gate=None,
    )

    output = capsys.readouterr().out
    assert "稳定译文" not in output
    assert playback_queue.qsize() == 1
    assert playback_queue.get_nowait().prepared.source_text == "今天我们讨论现金流预测方案"


def test_prepare_listen_segment_keeps_confirmed_prefix_when_later_partial_revises(
    capsys,
) -> None:  # type: ignore[no-untyped-def]
    """后续 partial 被 final 修正时，已被 final 确认的前缀仍应保留低延迟收益。"""
    playback_queue: cli_app.queue.Queue[cli_app._PendingPlayback | None] = cli_app.queue.Queue()
    bridge = _StablePreparingBridge(
        [
            _stable_chunk(
                TranscriptKind.PARTIAL,
                text="我们今天讨论现金流预测方案",
                delta_text="我们今天讨论现金流预测方案",
            ),
            _stable_chunk(
                TranscriptKind.PARTIAL,
                text="我們今天討論現金流預測方案和预算安排以及风险缓冲",
                delta_text="和预算安排以及风险缓冲",
            ),
            _stable_chunk(
                TranscriptKind.FINAL,
                text="我们今天讨论现金流预测方案和预算安排以及风险缓冲",
                delta_text="",
                revision=True,
            ),
        ]
    )

    cli_app._prepare_listen_segment(
        index=10,
        samples=np.ones(16000, dtype=np.int16),
        playback_queue=playback_queue,
        bridge=bridge,  # type: ignore[arg-type]
        direction=cli_app.AudioDirection.UPLINK,
        target="blackhole",
        label="上行",
        show_latency=True,
        playback_gate=None,
    )

    output = capsys.readouterr().out
    assert "稳定译文" in output
    queued_items = [playback_queue.get_nowait() for _ in range(2)]
    assert [item.prepared.source_text for item in queued_items] == [
        "我们今天讨论现金流预测方案",
        "和预算安排以及风险缓冲",
    ]


def test_prepare_listen_segment_starts_stable_prepare_before_final_returns() -> None:
    """稳定增量必须在 ASR final 返回前启动 prepare，才能真正降低 final 后等待。"""
    playback_queue: cli_app.queue.Queue[cli_app._PendingPlayback | None] = cli_app.queue.Queue()
    prepare_started = threading.Event()
    bridge = _StablePreparingBridge(
        [
            _stable_chunk(
                TranscriptKind.PARTIAL,
                text="我们今天讨论现金流预测方案",
                delta_text="我们今天讨论现金流预测方案",
            ),
            _stable_chunk(
                TranscriptKind.FINAL,
                text="我们今天讨论现金流预测方案",
                delta_text="",
            ),
        ],
        prepare_started=prepare_started,
        wait_prepare_started_before_final=True,
    )

    cli_app._prepare_listen_segment(
        index=9,
        samples=np.ones(16000, dtype=np.int16),
        playback_queue=playback_queue,
        bridge=bridge,  # type: ignore[arg-type]
        direction=cli_app.AudioDirection.UPLINK,
        target="blackhole",
        label="上行",
        show_latency=True,
        playback_gate=None,
    )

    assert playback_queue.get_nowait().prepared.source_text == "我们今天讨论现金流预测方案"


def test_prepare_listen_segment_cancels_stable_prepare_when_final_asr_fails(
    capsys,
) -> None:  # type: ignore[no-untyped-def]
    """final ASR 失败时，已启动的 stable prepare 任务必须取消。"""
    playback_queue: cli_app.queue.Queue[cli_app._PendingPlayback | None] = cli_app.queue.Queue()
    prepare_started = threading.Event()
    cancelled = threading.Event()
    async_runner = cli_app._AsyncLoopRunner()
    bridge = _FailingStablePreparingBridge(
        prepare_started=prepare_started,
        cancelled=cancelled,
    )
    try:
        cli_app._prepare_listen_segment(
            index=11,
            samples=np.ones(16000, dtype=np.int16),
            playback_queue=playback_queue,
            bridge=bridge,  # type: ignore[arg-type]
            direction=cli_app.AudioDirection.UPLINK,
            target="blackhole",
            label="上行",
            show_latency=True,
            playback_gate=None,
            async_runner=async_runner,
        )

        output = capsys.readouterr().out
        assert "Whisper final 失败" in output
        assert playback_queue.empty()
        assert cancelled.wait(timeout=1.0)
    finally:
        async_runner.close()


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


class _PreparingTranscriber:
    def transcribe(self, samples: np.ndarray) -> str:
        assert samples.size > 0
        return "hello"


class _PreparingSayBridge:
    async def prepare(
        self,
        source_text: str,
        *,
        direction: cli_app.AudioDirection,
        target: str,
        streaming: bool = False,
        context_text: str = "",
    ) -> PreparedSayResult:
        del context_text
        assert source_text == "hello"
        assert streaming
        return PreparedSayResult(
            source_text=source_text,
            target_text="你好",
            target_device=AudioDevice(1, "AirPods Pro", 0, 2),
            target=target,
            translation_latency_s=0.1,
            tts_latency_s=0.1,
            decode_latency_s=0.0,
            pcm=np.array([], dtype=np.int16),
        )


class _PreparingBridge:
    transcriber = _PreparingTranscriber()
    say_bridge = _PreparingSayBridge()


def _stable_chunk(
    kind: TranscriptKind,
    *,
    text: str,
    delta_text: str,
    revision: bool = False,
) -> StableTranscriptChunk:
    return StableTranscriptChunk(
        segment_id=uuid4(),
        direction=AudioDirection.UPLINK,
        kind=kind,
        started_at=datetime.now(UTC),
        ended_at=datetime.now(UTC) if kind is TranscriptKind.FINAL else None,
        text=text,
        delta_text=delta_text,
        confidence=0.9,
        revision=revision,
    )


class _StablePreparingTranscriber:
    def __init__(
        self,
        chunks: list[StableTranscriptChunk],
        *,
        prepare_started: threading.Event | None = None,
        wait_prepare_started_before_final: bool = False,
    ) -> None:
        self._chunks = chunks
        self._prepare_started = prepare_started
        self._wait_prepare_started_before_final = wait_prepare_started_before_final

    def transcribe(
        self,
        samples: np.ndarray,
        *,
        stable_chunk_callback: Callable[[StableTranscriptChunk], None] | None = None,
    ) -> str:
        assert samples.size > 0
        assert stable_chunk_callback is not None
        for index, chunk in enumerate(self._chunks):
            if (
                self._wait_prepare_started_before_final
                and index > 0
                and self._prepare_started is not None
            ):
                assert self._prepare_started.wait(timeout=1.0)
            stable_chunk_callback(chunk)
        return self._chunks[-1].text


class _StablePreparingSayBridge:
    def __init__(self, *, prepare_started: threading.Event | None = None) -> None:
        self.prepared_sources: list[str] = []
        self.prepare_contexts: list[str] = []
        self._prepare_started = prepare_started

    async def prepare(
        self,
        source_text: str,
        *,
        direction: cli_app.AudioDirection,
        target: str,
        streaming: bool = False,
        context_text: str = "",
    ) -> PreparedSayResult:
        assert streaming
        if self._prepare_started is not None:
            self._prepare_started.set()
        self.prepared_sources.append(source_text)
        self.prepare_contexts.append(context_text)
        return PreparedSayResult(
            source_text=source_text,
            target_text=f"译:{source_text}",
            target_device=AudioDevice(1, "BlackHole 2ch", 0, 2),
            target=target,
            translation_latency_s=0.1,
            tts_latency_s=0.1,
            decode_latency_s=0.0,
            pcm=np.array([], dtype=np.int16),
        )


class _StablePreparingBridge:
    def __init__(
        self,
        chunks: list[StableTranscriptChunk],
        *,
        prepare_started: threading.Event | None = None,
        wait_prepare_started_before_final: bool = False,
    ) -> None:
        self.transcriber = _StablePreparingTranscriber(
            chunks,
            prepare_started=prepare_started,
            wait_prepare_started_before_final=wait_prepare_started_before_final,
        )
        self.say_bridge = _StablePreparingSayBridge(prepare_started=prepare_started)


class _FailingStableTranscriber:
    def __init__(self, *, prepare_started: threading.Event) -> None:
        self._prepare_started = prepare_started

    def transcribe(
        self,
        samples: np.ndarray,
        *,
        stable_chunk_callback: Callable[[StableTranscriptChunk], None] | None = None,
    ) -> str:
        assert samples.size > 0
        assert stable_chunk_callback is not None
        stable_chunk_callback(
            _stable_chunk(
                TranscriptKind.PARTIAL,
                text="我们今天讨论现金流预测方案",
                delta_text="我们今天讨论现金流预测方案",
            )
        )
        assert self._prepare_started.wait(timeout=1.0)
        raise UserFacingError(
            code="test.final_failed",
            what_happened="发生了什么：Whisper final 失败。",
            next_action="下一步如何做：请重试。",
        )


class _CancellableStableSayBridge:
    def __init__(
        self,
        *,
        prepare_started: threading.Event,
        cancelled: threading.Event,
    ) -> None:
        self._prepare_started = prepare_started
        self._cancelled = cancelled

    async def prepare(
        self,
        source_text: str,
        *,
        direction: cli_app.AudioDirection,
        target: str,
        streaming: bool = False,
        context_text: str = "",
    ) -> PreparedSayResult:
        del direction, target, context_text
        assert source_text == "我们今天讨论现金流预测方案"
        assert streaming
        self._prepare_started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            self._cancelled.set()
            raise
        raise AssertionError("stable prepare should be cancelled")


class _FailingStablePreparingBridge:
    def __init__(
        self,
        *,
        prepare_started: threading.Event,
        cancelled: threading.Event,
    ) -> None:
        self.transcriber = _FailingStableTranscriber(prepare_started=prepare_started)
        self.say_bridge = _CancellableStableSayBridge(
            prepare_started=prepare_started,
            cancelled=cancelled,
        )


class _RecordingStreamingSayBridge:
    """记录 listen worker 传入的实时播放上限。"""

    def __init__(self) -> None:
        self.max_playback_seconds_values: list[float | None] = []

    async def play_prepared_streaming(
        self,
        prepared: PreparedSayResult,
        *,
        max_playback_seconds: float | None = None,
    ) -> SayResult:
        self.max_playback_seconds_values.append(max_playback_seconds)
        return SayResult(
            source_text=prepared.source_text,
            target_text=prepared.target_text,
            bytes_written=0,
            target_device_name=prepared.target_device.name,
            translation_latency_s=prepared.translation_latency_s,
        )


class _RecordingStreamingBridge:
    def __init__(self) -> None:
        self.say_bridge = _RecordingStreamingSayBridge()
