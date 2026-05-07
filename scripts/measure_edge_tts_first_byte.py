#!/usr/bin/env python3
"""测量 Edge-TTS first byte 真实延迟（BM-6 真实化）。

对应 perf-report.md 主表 BM-6 stub 真实化任务。对一组商务译文样本
（上下行各 N 句）调用 `EdgeTTSClient(live=True).stream_synthesize`，
记录从迭代开始到首个 `kind="first_byte"` 事件到达的 wall-clock 耗时，
输出 p50 / p95 / avg / max 分布与每条原始记录。回答"BM-6 真实首字节
延迟在预算 (p50 ≤ 400 ms / p95 ≤ 800 ms) 内吗"，并补齐 SC-001 / SC-002
端到端首段延迟链路的最后一段。

依赖 macOS 网络可访问 `speech.platform.bing.com`；每跑一次会消耗对应
数量的 Edge-TTS 调用（**Edge-TTS 是免费的非官方接口，零按量费用**，
但请尊重 24h 401/403 失败率上限）。

样本与方向口径：

- 上行 TTS：输入英文译文（zh→en 链路出口），输出英文音频，音色
  `en-US-AriaNeural`
- 下行 TTS：输入中文译文（en→zh 链路出口），输出中文音频，音色
  `zh-CN-XiaoxiaoNeural`

示例：

    uv run --extra dev scripts/measure_edge_tts_first_byte.py \
      --samples-per-direction 15 \
      --proof-json /tmp/edge-tts-first-byte.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from teams_voice_interpreter.data.audio_segment import AudioDirection
from teams_voice_interpreter.errors import UserFacingError
from teams_voice_interpreter.tts.edge_tts_client import (
    DEFAULT_VOICES,
    EdgeTTSClient,
    TTSEvent,
)

# 上行 TTS 输入：英文译文（zh→en 链路出口）
_UPLINK_SAMPLES: tuple[str, ...] = (
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

# 下行 TTS 输入：中文译文（en→zh 链路出口）
_DOWNLINK_SAMPLES: tuple[str, ...] = (
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


@dataclass(frozen=True)
class FirstByteSample:
    """单次首字节延迟样本。"""

    direction: AudioDirection
    voice: str
    text: str
    first_byte_s: float | None
    completed_s: float | None
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        """该次调用是否成功收到 first byte。"""
        return self.first_byte_s is not None and self.error is None


@dataclass(frozen=True)
class DirectionSummary:
    """单方向 (uplink / downlink) 首字节延迟分布摘要。"""

    direction: AudioDirection
    voice: str
    success_count: int
    failure_count: int
    p50_ms: float | None
    p95_ms: float | None
    avg_ms: float | None
    max_ms: float | None


class _StreamSynthesizeClient(Protocol):
    def stream_synthesize(
        self,
        text: str,
        *,
        direction: AudioDirection,
        voice: str | None = None,
    ) -> AsyncIterator[TTSEvent]: ...


async def measure_first_byte(
    client: _StreamSynthesizeClient,
    text: str,
    *,
    direction: AudioDirection,
    voice: str,
) -> FirstByteSample:
    """对单条文本调用一次 stream_synthesize，返回首字节耗时记录。

    必须消费到 completed 才返回，避免 communicate.stream() 与 async
    generator 泄露。
    """
    started = time.perf_counter()
    first_byte_s: float | None = None
    completed_s: float | None = None
    try:
        iterator = client.stream_synthesize(text, direction=direction, voice=voice)
        async for event in iterator:
            elapsed = time.perf_counter() - started
            if event.kind == "first_byte" and first_byte_s is None:
                first_byte_s = elapsed
            if event.kind == "completed":
                completed_s = elapsed
                break
    except UserFacingError as error:
        return FirstByteSample(
            direction=direction,
            voice=voice,
            text=text,
            first_byte_s=None,
            completed_s=None,
            error=f"{error.what_happened} | {error.next_action}",
        )
    except Exception as error:  # pragma: no cover - 防御网络异常
        return FirstByteSample(
            direction=direction,
            voice=voice,
            text=text,
            first_byte_s=None,
            completed_s=None,
            error=f"{type(error).__name__}: {error}",
        )
    return FirstByteSample(
        direction=direction,
        voice=voice,
        text=text,
        first_byte_s=first_byte_s,
        completed_s=completed_s,
    )


def summarize(samples: list[FirstByteSample], *, direction: AudioDirection) -> DirectionSummary:
    """汇总单方向延迟分布。"""
    direction_samples = [sample for sample in samples if sample.direction is direction]
    voice = direction_samples[0].voice if direction_samples else DEFAULT_VOICES[direction]
    successful = [
        sample.first_byte_s
        for sample in direction_samples
        if sample.succeeded and sample.first_byte_s is not None
    ]
    if not successful:
        return DirectionSummary(
            direction=direction,
            voice=voice,
            success_count=0,
            failure_count=len(direction_samples),
            p50_ms=None,
            p95_ms=None,
            avg_ms=None,
            max_ms=None,
        )
    successful_ms = [value * 1000.0 for value in successful]
    return DirectionSummary(
        direction=direction,
        voice=voice,
        success_count=len(successful_ms),
        failure_count=len(direction_samples) - len(successful_ms),
        p50_ms=_percentile(successful_ms, 50),
        p95_ms=_percentile(successful_ms, 95),
        avg_ms=statistics.mean(successful_ms),
        max_ms=max(successful_ms),
    )


def _percentile(values: list[float], rank: int) -> float:
    """nearest-rank percentile：取 sorted 后第 `ceil(rank/100 * N) - 1` 位元素。

    与 measure_deepseek_first_token.py 同款语义，避免与 numpy.percentile
    的 linear 模式混用。
    """
    sorted_values = sorted(values)
    if not sorted_values:
        msg = "_percentile 不能用于空列表。"
        raise ValueError(msg)
    raw_index = math.ceil(rank / 100.0 * len(sorted_values)) - 1
    index = max(0, min(len(sorted_values) - 1, raw_index))
    return sorted_values[index]


def _format_ms(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f} ms"


def render_report(
    samples: list[FirstByteSample], summaries: list[DirectionSummary]
) -> str:
    """渲染 markdown 报告：每方向汇总 + 全部样本明细。"""
    lines: list[str] = ["# Edge-TTS first byte 首字节延迟", ""]
    lines.append("## 分方向汇总")
    lines.append("")
    lines.append("| 方向 | 音色 | 成功 | 失败 | p50 | p95 | avg | max |")
    lines.append("|------|------|------|------|-----|-----|-----|-----|")
    for summary in summaries:
        lines.append(
            f"| {summary.direction.value} | `{summary.voice}` "
            f"| {summary.success_count} | {summary.failure_count} "
            f"| {_format_ms(summary.p50_ms)} | {_format_ms(summary.p95_ms)} "
            f"| {_format_ms(summary.avg_ms)} | {_format_ms(summary.max_ms)} |"
        )
    lines.extend(["", "## 样本明细", ""])
    lines.append("| 方向 | 首字节 | completed | 文本 | 错误 |")
    lines.append("|------|--------|-----------|------|------|")
    for sample in samples:
        first = "n/a" if sample.first_byte_s is None else f"{sample.first_byte_s * 1000:.1f} ms"
        completed = (
            "n/a" if sample.completed_s is None else f"{sample.completed_s * 1000:.1f} ms"
        )
        text = sample.text.replace("|", "\\|")
        error = (sample.error or "").replace("|", "\\|")
        lines.append(f"| {sample.direction.value} | {first} | {completed} | {text} | {error} |")
    return "\n".join(lines)


def proof_payload(
    samples: list[FirstByteSample],
    summaries: list[DirectionSummary],
) -> dict[str, object]:
    """生成可读 proof JSON payload。"""
    return {
        "schema_version": 1,
        "generated_by": "scripts/measure_edge_tts_first_byte.py",
        "summaries": [
            {
                "direction": summary.direction.value,
                "voice": summary.voice,
                "success_count": summary.success_count,
                "failure_count": summary.failure_count,
                "p50_ms": summary.p50_ms,
                "p95_ms": summary.p95_ms,
                "avg_ms": summary.avg_ms,
                "max_ms": summary.max_ms,
            }
            for summary in summaries
        ],
        "samples": [
            {
                "direction": sample.direction.value,
                "voice": sample.voice,
                "text": sample.text,
                "first_byte_s": sample.first_byte_s,
                "completed_s": sample.completed_s,
                "error": sample.error,
            }
            for sample in samples
        ],
    }


def write_proof_json(path: Path, payload: dict[str, object]) -> None:
    """写出 proof JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _direction_plan(
    samples_per_direction: int,
) -> list[tuple[AudioDirection, str, str]]:
    """返回 (direction, voice, text) 测量计划，按上行→下行顺序拼接。"""
    plan: list[tuple[AudioDirection, str, str]] = []
    uplink_voice = DEFAULT_VOICES[AudioDirection.UPLINK]
    downlink_voice = DEFAULT_VOICES[AudioDirection.DOWNLINK]
    for text in _UPLINK_SAMPLES[:samples_per_direction]:
        plan.append((AudioDirection.UPLINK, uplink_voice, text))
    for text in _DOWNLINK_SAMPLES[:samples_per_direction]:
        plan.append((AudioDirection.DOWNLINK, downlink_voice, text))
    return plan


async def run_measurement(
    client: _StreamSynthesizeClient,
    plan: list[tuple[AudioDirection, str, str]],
) -> list[FirstByteSample]:
    """按计划串行测量并返回所有样本。"""
    samples: list[FirstByteSample] = []
    for direction, voice, text in plan:
        samples.append(
            await measure_first_byte(client, text, direction=direction, voice=voice)
        )
    return samples


def main(argv: list[str] | None = None) -> int:
    """脚本入口：合成 plan → 串行测量 → 渲染报告 → 可选 proof JSON。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--samples-per-direction",
        type=int,
        default=15,
        help="每方向采样数；最大值受样本池容量限制。",
    )
    parser.add_argument(
        "--first-byte-timeout-s",
        type=float,
        default=8.0,
        help="单次请求等待首字节的超时上限。",
    )
    parser.add_argument(
        "--synthesis-timeout-s",
        type=float,
        default=20.0,
        help="单次请求总合成耗时上限。",
    )
    parser.add_argument(
        "--proof-json",
        type=Path,
        default=None,
        help="写出测量 proof JSON。",
    )
    args = parser.parse_args(argv)

    max_per_direction = min(len(_UPLINK_SAMPLES), len(_DOWNLINK_SAMPLES))
    if args.samples_per_direction < 1 or args.samples_per_direction > max_per_direction:
        print(
            f"FAIL: --samples-per-direction 必须在 1..{max_per_direction} 之间，"
            f"当前 {args.samples_per_direction}。"
        )
        return 1

    client = EdgeTTSClient(
        live=True,
        first_byte_timeout_s=args.first_byte_timeout_s,
        synthesis_timeout_s=args.synthesis_timeout_s,
    )
    plan = _direction_plan(args.samples_per_direction)
    samples = asyncio.run(run_measurement(client, plan))
    summaries = [
        summarize(samples, direction=AudioDirection.UPLINK),
        summarize(samples, direction=AudioDirection.DOWNLINK),
    ]
    print(render_report(samples, summaries))
    if args.proof_json is not None:
        write_proof_json(args.proof_json, proof_payload(samples, summaries))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
