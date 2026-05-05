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


def test_drop_pending_playbacks_keeps_only_latest_unstarted_item() -> None:
    """实时播放入队前必须丢弃尚未播放的旧段，避免旧译音继续排队。"""
    playback_queue: cli_app.queue.Queue[cli_app._PendingPlayback | None] = cli_app.queue.Queue()
    old_item = _pending_item(index=1)
    playback_queue.put(old_item)

    dropped = cli_app._drop_pending_playbacks(playback_queue)

    assert dropped == 1
    assert playback_queue.empty()


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


def _pending_item(*, index: int, prepared_at: float = 12.0) -> cli_app._PendingPlayback:
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
    )


class _ExplodingSayBridge:
    def play_prepared(self, prepared: PreparedSayResult) -> SayResult:
        del prepared
        raise AssertionError("stale item must not be played")


class _ExplodingBridge:
    say_bridge = _ExplodingSayBridge()
