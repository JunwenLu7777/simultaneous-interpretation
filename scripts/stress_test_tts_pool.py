#!/usr/bin/env python3
"""对 PiperClient voice 池做长时间会议压力测试。

模拟 2-4 个并发会议，每个会议持续 10-30 分钟，每句话间隔 0.5-5s
（指数分布），跟踪首字节延迟、池等待时间、失败率随时间的漂移。

真实模式（默认）需要 Piper 模型文件；若无模型可用，加 ``--fake`` 走
内部 fake voice（适合 CI / 快速验证池逻辑）。

示例：
    # 快速冒烟（fake voice，30s 会议）
    uv run --extra dev scripts/stress_test_tts_pool.py --fake --duration 30

    # 真实模型长时间压测（需预下载 Piper 模型）
    uv run --extra dev scripts/stress_test_tts_pool.py --duration 600 --meetings 3
"""

from __future__ import annotations

import argparse
import asyncio
import gc
import math
import os
import random
import statistics
import sys
import time
import types
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from teams_voice_interpreter.data.audio_segment import AudioDirection
from teams_voice_interpreter.tts.piper_client import DEFAULT_PIPER_VOICES, PiperClient

# ── 会议样本：模拟真实同传场景中 TTS 输入的典型译文 ──────────────

_UPLINK_TEXTS = (
    "Hello.",
    "Let's start the meeting at three.",
    "Please hold on.",
    "I agree with the proposal.",
    "Please send the consolidated sales data from last quarter to marketing.",
    "Customer feedback shows the renewal rate dropped three percentage points.",
    "We decided to push the release date to the third quarter.",
    "We need to ship the cloud collaboration feature before year end.",
    "Quarterly revenue grew twelve percent year over year.",
    "Today's product review will discuss user growth, retention, and paid conversion.",
    "Each team lead should prepare the latest data ahead of time.",
    "Next Wednesday at ten in the main conference room for the monthly review.",
    "Let's bump this bug priority to P0.",
    "The customer support team is short-staffed during peak hours.",
    "We plan to launch cloud collaboration and analytics in the third quarter.",
)

_DOWNLINK_TEXTS = (
    "你好",
    "下次会议三点开始",
    "请稍等一下",
    "我同意这个方案",
    "请把上季度的销售数据汇总后发给市场部",
    "客户反馈续费率下降三个百分点",
    "我们决定把发布日期推迟到第三季度",
    "需要在年底前把云端协同功能上线",
    "本季度营收同比增长百分之十二",
    "今天的产品评审会议将依次讨论用户增长、留存率、付费转化",
    "请各位团队负责人提前准备好最新数据",
    "下周三上午十点在主会议室开月度回顾",
    "建议把这个 bug 优先级调到 P0",
    "客户支持团队反馈高峰期人手不足",
    "我们计划在第三季度推出云端协同与数据分析两个核心新功能",
)


# ── Fake voice（无 ONNX 模型时的替代，模拟真实合成耗时） ─────────

class _FakePiperVoice:
    """模拟 PiperVoice：产出 PCM bytes + 模拟 10-40ms 合成耗时。"""

    def synthesize(self, text: str) -> Iterator[Any]:
        # 模拟真实 Piper 的合成耗时（按文本长度缩放）
        base_ms = 8 + len(text.encode("utf-8")) * 0.15
        jitter = random.gauss(0, base_ms * 0.2)
        delay = max(2, base_ms + jitter) / 1000.0
        time.sleep(delay)
        # chunk 数模拟真实 Piper 行为：短句 1-2 chunks，长句 2-4 chunks
        num_chunks = max(1, min(4, len(text) // 20))
        for _ in range(num_chunks):
            yield types.SimpleNamespace(audio_int16_bytes=b"\x00\x01" * 512)


# ── 数据模型 ────────────────────────────────────────────────────────

@dataclass
class TTSProbe:
    """单次 TTS 调用记录。"""

    elapsed_s: float  # 从脚本启动到调用发起的墙钟秒
    direction: str
    text_preview: str
    acquire_ms: float  # pool.acquire() 等待时间
    first_byte_ms: float  # 从 acquire 到 first_byte 的时间
    total_ms: float  # 从 acquire 到 completed 的总时间
    success: bool = True
    error: str | None = None


@dataclass
class TimeWindowReport:
    """单时间窗口的统计。"""

    window_start_s: float
    window_end_s: float
    count: int
    failures: int
    acquire_p50_ms: float
    acquire_p95_ms: float
    acquire_max_ms: float
    first_byte_p50_ms: float
    first_byte_p95_ms: float
    first_byte_max_ms: float
    total_p50_ms: float
    total_p95_ms: float


# ── 核心测量 ────────────────────────────────────────────────────────

async def _measure_one(
    client: PiperClient,
    text: str,
    direction: AudioDirection,
    t0: float,
) -> TTSProbe:
    """对单条文本做一次 stream_synthesize 并记录耗时。"""
    acquire_start = time.perf_counter()
    try:
        stream = client.stream_synthesize(text, direction=direction)
    except Exception as exc:
        return TTSProbe(
            elapsed_s=time.perf_counter() - t0,
            direction=direction.value,
            text_preview=text[:30],
            acquire_ms=(time.perf_counter() - acquire_start) * 1000,
            first_byte_ms=-1,
            total_ms=-1,
            success=False,
            error=f"{type(exc).__name__}: {exc}",
        )

    acquired_at = time.perf_counter()
    acquire_ms = (acquired_at - acquire_start) * 1000

    first_byte_ms: float | None = None
    try:
        async for event in stream:
            elapsed = (time.perf_counter() - acquired_at) * 1000
            if first_byte_ms is None:
                first_byte_ms = elapsed
        total_ms = (time.perf_counter() - acquired_at) * 1000
    except Exception as exc:
        return TTSProbe(
            elapsed_s=acquired_at - t0,
            direction=direction.value,
            text_preview=text[:30],
            acquire_ms=acquire_ms,
            first_byte_ms=first_byte_ms or -1,
            total_ms=-1,
            success=False,
            error=f"{type(exc).__name__}: {exc}",
        )

    return TTSProbe(
        elapsed_s=acquired_at - t0,
        direction=direction.value,
        text_preview=text[:30],
        acquire_ms=acquire_ms,
        first_byte_ms=first_byte_ms or 0,
        total_ms=total_ms,
    )


async def _simulate_meeting(
    client: PiperClient,
    meeting_id: int,
    duration_s: float,
    t0: float,
    direction: AudioDirection,
    probes: list[TTSProbe],
    *,
    mean_interval: float = 2.5,
    burst_prob: float = 0.0,
) -> None:
    """模拟一场会议：按指数间隔发 TTS 请求直到 duration_s 结束。

    burst_prob > 0 时，每次发话后有该概率触发 2-4 句连续抢话（模拟多人
    同时发言场景，真正考验池排队能力）。
    """
    rng = random.Random(meeting_id * 1000 + hash(direction.value))
    texts = _UPLINK_TEXTS if direction is AudioDirection.UPLINK else _DOWNLINK_TEXTS

    async def _say(text: str) -> None:
        probe = await _measure_one(client, text, direction, t0)
        probes.append(probe)

    while time.perf_counter() - t0 < duration_s:
        text = rng.choice(texts)
        await _say(text)
        # 突发模式：模拟多人同时抢话
        if rng.random() < burst_prob:
            burst_count = rng.randint(2, 4)
            burst_tasks = [
                asyncio.create_task(_say(rng.choice(texts)))
                for _ in range(burst_count)
            ]
            await asyncio.gather(*burst_tasks)
        gap = rng.expovariate(1.0 / mean_interval)
        await asyncio.sleep(max(0.1, gap))


# ── 统计 ────────────────────────────────────────────────────────────

def _build_window_reports(
    probes: list[TTSProbe], window_s: float, total_duration_s: float
) -> list[TimeWindowReport]:
    """将探针按时间窗口汇总。"""
    windows: list[TimeWindowReport] = []
    window_start = 0.0
    while window_start < total_duration_s:
        window_end = window_start + window_s
        bucket = [p for p in probes if window_start <= p.elapsed_s < window_end]
        if not bucket:
            window_start += window_s
            continue

        successes = [p for p in bucket if p.success]
        failures = len(bucket) - len(successes)

        def _p(values: list[float], rank: float) -> float:
            if not values:
                return 0.0
            ordered = sorted(values)
            pos = (len(ordered) - 1) * (rank / 100)
            lo = math.floor(pos)
            hi = math.ceil(pos)
            if lo == hi:
                return ordered[int(pos)]
            return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)

        acquire_values = [p.acquire_ms for p in successes if p.acquire_ms >= 0]
        fb_values = [p.first_byte_ms for p in successes if p.first_byte_ms >= 0]
        total_values = [p.total_ms for p in successes if p.total_ms >= 0]

        windows.append(
            TimeWindowReport(
                window_start_s=window_start,
                window_end_s=window_end,
                count=len(bucket),
                failures=failures,
                acquire_p50_ms=_p(acquire_values, 50),
                acquire_p95_ms=_p(acquire_values, 95),
                acquire_max_ms=max(acquire_values) if acquire_values else 0,
                first_byte_p50_ms=_p(fb_values, 50),
                first_byte_p95_ms=_p(fb_values, 95),
                first_byte_max_ms=max(fb_values) if fb_values else 0,
                total_p50_ms=_p(total_values, 50),
                total_p95_ms=_p(total_values, 95),
            )
        )
        window_start += window_s
    return windows


def _overall_stats(probes: list[TTSProbe]) -> dict[str, Any]:
    successes = [p for p in probes if p.success]
    failures = [p for p in probes if not p.success]
    fb = sorted([p.first_byte_ms for p in successes if p.first_byte_ms >= 0])
    acq = sorted([p.acquire_ms for p in successes if p.acquire_ms >= 0])
    total = sorted([p.total_ms for p in successes if p.total_ms >= 0])

    def _p(values: list[float], rank: float) -> float:
        if not values:
            return 0.0
        pos = int(math.ceil(rank / 100.0 * len(values))) - 1
        return values[max(0, min(len(values) - 1, pos))]

    return {
        "total_calls": len(probes),
        "successes": len(successes),
        "failures": len(failures),
        "failure_rate_pct": len(failures) / max(1, len(probes)) * 100,
        "first_byte_p50_ms": _p(fb, 50),
        "first_byte_p95_ms": _p(fb, 95),
        "first_byte_p99_ms": _p(fb, 99),
        "first_byte_max_ms": fb[-1] if fb else 0,
        "first_byte_mean_ms": statistics.mean(fb) if fb else 0,
        "acquire_p50_ms": _p(acq, 50),
        "acquire_p95_ms": _p(acq, 95),
        "acquire_p99_ms": _p(acq, 99),
        "acquire_max_ms": acq[-1] if acq else 0,
        "total_p50_ms": _p(total, 50),
        "total_p95_ms": _p(total, 95),
    }


# ── 报告 ────────────────────────────────────────────────────────────

def _render(stats: dict[str, Any], windows: list[TimeWindowReport]) -> str:
    lines: list[str] = []
    lines.append("# TTS 池长时间会议压力测试")
    lines.append("")
    lines.append("## 总体统计")
    lines.append("")
    lines.append(
        f"| 指标 | 值 |\n"
        f"|------|----|\n"
        f"| 总调用 | {stats['total_calls']} |\n"
        f"| 成功 | {stats['successes']} |\n"
        f"| 失败 | {stats['failures']} ({stats['failure_rate_pct']:.1f}%) |\n"
        f"| 首字节 p50 | {stats['first_byte_p50_ms']:.1f} ms |\n"
        f"| 首字节 p95 | {stats['first_byte_p95_ms']:.1f} ms |\n"
        f"| 首字节 p99 | {stats['first_byte_p99_ms']:.1f} ms |\n"
        f"| 首字节 max | {stats['first_byte_max_ms']:.1f} ms |\n"
        f"| 首字节 mean | {stats['first_byte_mean_ms']:.1f} ms |\n"
        f"| 池等待 p50 | {stats['acquire_p50_ms']:.1f} ms |\n"
        f"| 池等待 p95 | {stats['acquire_p95_ms']:.1f} ms |\n"
        f"| 池等待 p99 | {stats['acquire_p99_ms']:.1f} ms |\n"
        f"| 池等待 max | {stats['acquire_max_ms']:.1f} ms |\n"
        f"| 合成总 p50 | {stats['total_p50_ms']:.1f} ms |\n"
        f"| 合成总 p95 | {stats['total_p95_ms']:.1f} ms |"
    )

    if not windows:
        return "\n".join(lines)

    lines.extend(["", "## 时间窗口统计（检测延迟漂移）", ""])
    lines.append(
        "| 窗口(s) | 调用 | 失败 | fb p50 | fb p95 | fb max | acq p50 | acq p95 | acq max | total p50 | total p95 |"
    )
    lines.append(
        "|---------|------|------|--------|--------|--------|---------|---------|---------|-----------|-----------|"
    )
    for w in windows:
        lines.append(
            f"| {w.window_start_s:.0f}-{w.window_end_s:.0f} "
            f"| {w.count} | {w.failures} "
            f"| {w.first_byte_p50_ms:.1f} | {w.first_byte_p95_ms:.1f} | {w.first_byte_max_ms:.1f} "
            f"| {w.acquire_p50_ms:.1f} | {w.acquire_p95_ms:.1f} | {w.acquire_max_ms:.1f} "
            f"| {w.total_p50_ms:.1f} | {w.total_p95_ms:.1f} |"
        )

    # 延迟漂移检测
    fb_p50_list = [w.first_byte_p50_ms for w in windows if w.first_byte_p50_ms > 0]
    if len(fb_p50_list) >= 2:
        first_half = statistics.mean(fb_p50_list[: len(fb_p50_list) // 2])
        second_half = statistics.mean(fb_p50_list[len(fb_p50_list) // 2 :])
        drift_pct = (second_half - first_half) / max(0.01, first_half) * 100
        drift_status = "正常" if abs(drift_pct) < 20 else ("显著退化" if drift_pct > 0 else "显著改善")
        lines.extend([
            "",
            "## 延迟漂移分析",
            "",
            f"- 前半段 fb p50 均值: {first_half:.1f} ms",
            f"- 后半段 fb p50 均值: {second_half:.1f} ms",
            f"- 漂移: {drift_pct:+.1f}% → **{drift_status}**",
        ])

    # 池等待分析（关注 pool 耗尽信号）
    acq_p95_list = [w.acquire_p95_ms for w in windows if w.acquire_max_ms > 0]
    if acq_p95_list:
        high_wait = [w for w in windows if w.acquire_p95_ms > 50]
        if high_wait:
            lines.extend([
                "",
                "## 池等待异常",
                "",
                f"存在 {len(high_wait)} 个时间窗口 pool.acquire() p95 > 50ms，"
                f"说明池大小不足或单个合成耗时过长：",
            ])
            for w in high_wait[:5]:
                lines.append(
                    f"- 窗口 {w.window_start_s:.0f}-{w.window_end_s:.0f}s: "
                    f"acq p95={w.acquire_p95_ms:.1f}ms, "
                    f"acq max={w.acquire_max_ms:.1f}ms"
                )

    if stats["failures"] > 0:
        lines.extend([
            "",
            "## 失败明细（前 5 条）",
        ])

    return "\n".join(lines)


# ── 入口 ────────────────────────────────────────────────────────────

def _build_client(
    models_dir: Path,
    pool_size: int,
    use_fake: bool,
    slow_fake: bool = False,
) -> PiperClient:
    if use_fake:
        # fake 模式：创建占位模型文件 + voice_loader
        models_dir.mkdir(parents=True, exist_ok=True)
        for voice_name in set(DEFAULT_PIPER_VOICES.values()):
            (models_dir / f"{voice_name}.onnx").write_bytes(b"fake")
            (models_dir / f"{voice_name}.onnx.json").write_text("{}", encoding="utf-8")

        if slow_fake:

            def _slow_loader(_path: str) -> _SlowFakePiperVoice:
                return _SlowFakePiperVoice()

            return PiperClient(
                models_dir=models_dir,
                pool_size=pool_size,
                voice_loader=_slow_loader,
            )

        def _fake_loader(_path: str) -> _FakePiperVoice:
            return _FakePiperVoice()

        return PiperClient(
            models_dir=models_dir,
            pool_size=pool_size,
            voice_loader=_fake_loader,
        )
    return PiperClient(models_dir=models_dir, pool_size=pool_size)


class _SlowFakePiperVoice:
    """模拟真实 Piper 延迟：首字节 30-80ms，用于测试池排队行为。"""

    def synthesize(self, text: str) -> Iterator[Any]:
        # 模拟真实 Piper: prefill ~40ms + per-token ~2ms
        base_ms = 30 + len(text.encode("utf-8")) * 1.5
        jitter = random.gauss(0, base_ms * 0.2)
        delay = max(10, base_ms + jitter) / 1000.0
        time.sleep(delay)
        num_chunks = max(1, min(5, len(text) // 15))
        for _ in range(num_chunks):
            yield types.SimpleNamespace(audio_int16_bytes=b"\x00\x01" * 1024)


async def _run(
    *,
    models_dir: Path,
    pool_size: int,
    meetings: int,
    duration: float,
    window_s: float,
    use_fake: bool,
    slow_fake: bool = False,
    mean_interval: float = 2.5,
    burst_prob: float = 0.0,
) -> int:
    client = _build_client(models_dir, pool_size, use_fake, slow_fake=slow_fake)

    # 预热：触发建池
    for d in (AudioDirection.UPLINK, AudioDirection.DOWNLINK):
        client.preload_voice(direction=d)

    probes: list[TTSProbe] = []
    t0 = time.perf_counter()

    # 每个会议一半 uplink 一半 downlink
    tasks: list[asyncio.Task[None]] = []
    for m in range(meetings):
        tasks.append(
            asyncio.create_task(
                _simulate_meeting(
                    client, m, duration, t0, AudioDirection.UPLINK, probes,
                    mean_interval=mean_interval, burst_prob=burst_prob,
                )
            )
        )
        tasks.append(
            asyncio.create_task(
                _simulate_meeting(
                    client, m + 100, duration, t0, AudioDirection.DOWNLINK, probes,
                    mean_interval=mean_interval, burst_prob=burst_prob,
                )
            )
        )

    extras = []
    if burst_prob > 0:
        extras.append(f"突发概率 {burst_prob:.0%}")
    if slow_fake:
        extras.append("慢速 fake")
    if mean_interval != 2.5:
        extras.append(f"平均间隔 {mean_interval:.1f}s")
    extra_str = f"（{', '.join(extras)}）" if extras else ""
    print(
        f"启动 {meetings} 个会议 × 2 方向 = {len(tasks)} 个并发流，"
        f"pool_size={pool_size}，持续 {duration}s {extra_str}..."
    )
    started_msg = time.perf_counter()
    await asyncio.gather(*tasks)
    actual_duration = time.perf_counter() - t0
    print(f"完成，实际耗时 {actual_duration:.1f}s\n")

    # 内存快照（近似）
    gc.collect()
    try:
        import resource

        rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        print(f"进程 RSS 峰值: {rss_mb:.1f} MB")
    except ImportError:
        pass

    stats = _overall_stats(probes)
    windows = _build_window_reports(probes, window_s, actual_duration)
    print(_render(stats, windows))

    if stats["failures"] > 0:
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=float, default=60, help="测试持续秒数（默认 60）")
    parser.add_argument("--meetings", type=int, default=2, help="并发会议数（默认 2）")
    parser.add_argument("--pool-size", type=int, default=3, help="每 voice 池大小（默认 3）")
    parser.add_argument("--window", type=float, default=15, help="统计窗口秒数（默认 15）")
    parser.add_argument("--fake", action="store_true", help="使用 fake voice（无需 ONNX 模型）")
    parser.add_argument("--slow-fake", action="store_true", help="慢速 fake voice（模拟真实 ~40ms 延迟）")
    parser.add_argument("--burst", type=float, default=0.0, help="突发抢话概率 0.0-1.0（模拟多人同时发言）")
    parser.add_argument("--interval", type=float, default=2.5, help="句子平均间隔秒数（默认 2.5）")
    parser.add_argument(
        "--models-dir",
        type=Path,
        default=Path.home() / ".cache/teams-voice-interpreter/piper-models",
        help="Piper 模型目录",
    )
    args = parser.parse_args(argv)

    return asyncio.run(
        _run(
            models_dir=args.models_dir,
            pool_size=args.pool_size,
            meetings=args.meetings,
            duration=args.duration,
            window_s=args.window,
            use_fake=args.fake,
            slow_fake=args.slow_fake,
            mean_interval=args.interval,
            burst_prob=args.burst,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
