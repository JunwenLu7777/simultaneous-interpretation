#!/usr/bin/env python3
"""测量本地 MLX Qwen2.5-7B 首 token 延迟，与 DeepSeek 对比。

首次运行自动下载 `mlx-community/Qwen2.5-7B-Instruct-4bit`（约 4.5 GB）。

示例：
    uv run --extra dev scripts/measure_local_mt_first_token.py --samples-per-direction 5
"""

from __future__ import annotations

import argparse
import math
import statistics
import time
from dataclasses import dataclass

from mlx_lm import load, stream_generate
from teams_voice_interpreter.data.audio_segment import AudioDirection
from teams_voice_interpreter.mt.prompt import build_system_prompt

_UPLINK_SAMPLES = (
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

_DOWNLINK_SAMPLES = (
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


@dataclass(frozen=True)
class FirstTokenSample:
    direction: AudioDirection
    text: str
    first_token_s: float | None
    total_s: float | None
    translation: str = ""
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.first_token_s is not None and self.error is None


def measure_one(model, tokenizer, text: str, *, direction: AudioDirection, max_tokens: int = 128) -> FirstTokenSample:
    messages = [
        {"role": "system", "content": build_system_prompt(direction, [])},
        {"role": "user", "content": text},
    ]
    prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    try:
        started = time.perf_counter()
        first_token_at: float | None = None
        collected: list[str] = []
        for response in stream_generate(model, tokenizer, prompt, max_tokens=max_tokens):
            elapsed = time.perf_counter() - started
            if first_token_at is None:
                first_token_at = elapsed
            collected.append(response.text)
        total = time.perf_counter() - started
        return FirstTokenSample(
            direction=direction,
            text=text,
            first_token_s=first_token_at,
            total_s=total,
            translation="".join(collected).strip(),
        )
    except Exception as error:
        return FirstTokenSample(
            direction=direction,
            text=text,
            first_token_s=None,
            total_s=None,
            error=f"{type(error).__name__}: {error}",
        )


def summarize(samples: list[FirstTokenSample], *, direction: AudioDirection) -> dict:
    direction_samples = [s for s in samples if s.direction is direction and s.succeeded]
    if not direction_samples:
        return {"direction": direction.value, "count": 0}
    values = [s.first_token_s * 1000 for s in direction_samples if s.first_token_s is not None]
    if not values:
        return {"direction": direction.value, "count": 0}
    return {
        "direction": direction.value,
        "count": len(values),
        "p50_ms": _percentile(values, 50),
        "p95_ms": _percentile(values, 95),
        "avg_ms": statistics.mean(values),
        "max_ms": max(values),
        "min_ms": min(values),
    }


def _percentile(values: list[float], rank: int) -> float:
    sorted_values = sorted(values)
    raw_index = math.ceil(rank / 100.0 * len(sorted_values)) - 1
    index = max(0, min(len(sorted_values) - 1, raw_index))
    return sorted_values[index]


def _format_ms(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f} ms"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples-per-direction", type=int, default=5)
    parser.add_argument("--model", default="mlx-community/Qwen2.5-7B-Instruct-4bit")
    parser.add_argument("--max-tokens", type=int, default=128)
    parser.add_argument("--show-translations", action="store_true", default=True)
    args = parser.parse_args(argv)

    max_samples = min(len(_UPLINK_SAMPLES), len(_DOWNLINK_SAMPLES))
    n = min(args.samples_per_direction, max_samples)

    print(f"加载模型 {args.model} ...")
    t0 = time.perf_counter()
    model, tokenizer = load(args.model)
    print(f"加载完成，耗时 {time.perf_counter() - t0:.1f}s")

    # warmup: 首次推理包含 Metal kernel JIT 编译
    print("预热中（首次推理包含 Metal 编译）...")
    warmup_text = _UPLINK_SAMPLES[0]
    measure_one(model, tokenizer, warmup_text, direction=AudioDirection.UPLINK, max_tokens=5)
    print("预热完成\n")

    samples: list[FirstTokenSample] = []

    print("--- uplink (中→英) ---")
    for text in _UPLINK_SAMPLES[:n]:
        sample = measure_one(model, tokenizer, text, direction=AudioDirection.UPLINK, max_tokens=args.max_tokens)
        samples.append(sample)
        ft = sample.first_token_s
        ft_str = f"{ft * 1000:.0f} ms" if ft is not None else "FAIL"
        print(f"  [{ft_str}] {text}")
        if args.show_translations and sample.translation:
            print(f"        → {sample.translation}")

    print("\n--- downlink (英→中) ---")
    for text in _DOWNLINK_SAMPLES[:n]:
        sample = measure_one(model, tokenizer, text, direction=AudioDirection.DOWNLINK, max_tokens=args.max_tokens)
        samples.append(sample)
        ft = sample.first_token_s
        ft_str = f"{ft * 1000:.0f} ms" if ft is not None else "FAIL"
        print(f"  [{ft_str}] {text}")
        if args.show_translations and sample.translation:
            print(f"         → {sample.translation}")

    uplink_summary = summarize(samples, direction=AudioDirection.UPLINK)
    downlink_summary = summarize(samples, direction=AudioDirection.DOWNLINK)

    print("\n## 本地 Qwen2.5-7B (MLX 4-bit) 汇总")
    print("| 方向 | 样本 | p50 | p95 | avg | min | max |")
    print("|------|------|-----|-----|-----|-----|-----|")
    for s in [uplink_summary, downlink_summary]:
        if s["count"] == 0:
            print(f"| {s['direction']} | 0 | - | - | - | - | - |")
        else:
            print(
                f"| {s['direction']} | {s['count']} "
                f"| {_format_ms(s.get('p50_ms'))} "
                f"| {_format_ms(s.get('p95_ms'))} "
                f"| {_format_ms(s.get('avg_ms'))} "
                f"| {_format_ms(s.get('min_ms'))} "
                f"| {_format_ms(s.get('max_ms'))} |"
            )

    errors = [s for s in samples if s.error]
    if errors:
        print(f"\n{len(errors)} 条错误:")
        for s in errors:
            print(f"  {s.direction.value}: {s.text[:40]}... → {s.error}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
