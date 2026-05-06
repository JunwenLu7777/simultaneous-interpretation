#!/usr/bin/env python3
"""把 `tvi duplex --show-latency` 输出按阶段拆成 p50 / p95 / 最大值。

用法：

    tvi duplex --show-latency --chunks 30 2>&1 | tee /tmp/latency.log
    uv run --extra dev scripts/analyze_latency.py /tmp/latency.log

支持仅分析某个方向（默认两路一起）：

    uv run --extra dev scripts/analyze_latency.py /tmp/latency.log --label 上行
    uv run --extra dev scripts/analyze_latency.py /tmp/latency.log --label 下行
"""

from __future__ import annotations

import argparse
import re
import statistics
import sys
from collections.abc import Iterable
from pathlib import Path

STAGE_PATTERNS: dict[str, re.Pattern[str]] = {
    "ASR": re.compile(r"ASR\s+([0-9.]+)s"),
    "MT首T": re.compile(r"MT首T\s+([0-9.]+)s"),
    "MT总": re.compile(r"MT总\s+([0-9.]+)s"),
    "prepare墙钟": re.compile(r"prepare墙钟\s+([0-9.]+)s"),
    "TTS": re.compile(r"TTS\s+([0-9.]+)s"),
    "解码": re.compile(r"解码\s+([0-9.]+)s"),
    "排队": re.compile(r"排队\s+([0-9.]+)s"),
    "首PCM": re.compile(r"首PCM\s+([0-9.]+)s"),
    "首写": re.compile(r"首写\s+([0-9.]+)s"),
    "首字节": re.compile(r"首字节\s+([0-9.]+)s"),
    "播放": re.compile(r"播放\s+([0-9.]+)s"),
    "总计": re.compile(r"总计\s+([0-9.]+)s"),
}
LABEL_PATTERN = re.compile(r"^\[(?:(?P<label>上行|下行)\s+)?\d+\]")
TRUNCATED_PATTERN = re.compile(r"截断")


def parse(lines: Iterable[str], *, label: str | None) -> dict[str, list[float]]:
    """从日志行中提取每个阶段的耗时序列。"""
    samples: dict[str, list[float]] = {stage: [] for stage in STAGE_PATTERNS}
    samples["截断段数"] = []
    for line in lines:
        if "耗时：" not in line:
            continue
        match = LABEL_PATTERN.search(line)
        if label is not None:
            line_label = match.group("label") if match else None
            if line_label != label:
                continue
        for stage, pattern in STAGE_PATTERNS.items():
            hit = pattern.search(line)
            if hit:
                samples[stage].append(float(hit.group(1)))
        if TRUNCATED_PATTERN.search(line):
            samples["截断段数"].append(1.0)
    return samples


def quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    lower = int(pos)
    upper = min(lower + 1, len(ordered) - 1)
    weight = pos - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def report(samples: dict[str, list[float]]) -> str:
    """渲染 markdown 表格摘要。"""
    rows: list[tuple[str, int, float, float, float, float]] = []
    for stage in [*STAGE_PATTERNS.keys(), "截断段数"]:
        values = samples[stage]
        if stage == "截断段数":
            count = len(values)
            rows.append((stage, count, 0.0, 0.0, 0.0, 0.0))
            continue
        if not values:
            continue
        rows.append(
            (
                stage,
                len(values),
                statistics.mean(values),
                quantile(values, 0.5),
                quantile(values, 0.95),
                max(values),
            )
        )

    header = "| 阶段 | n | 均值 s | p50 s | p95 s | max s |"
    sep = "|------|---|--------|-------|-------|-------|"
    body = []
    for stage, n, mean, p50, p95, mx in rows:
        if stage == "截断段数":
            body.append(f"| {stage} | {n} | - | - | - | - |")
        else:
            body.append(f"| {stage} | {n} | {mean:.2f} | {p50:.2f} | {p95:.2f} | {mx:.2f} |")
    return "\n".join([header, sep, *body])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log_path", type=Path, help="`tvi duplex --show-latency` 日志路径。")
    parser.add_argument(
        "--label",
        choices=["上行", "下行"],
        default=None,
        help="仅统计某一方向；不传则两路合并。",
    )
    args = parser.parse_args(argv)

    if not args.log_path.exists():
        print(f"日志不存在：{args.log_path}", file=sys.stderr)
        return 1

    with args.log_path.open("r", encoding="utf-8") as fp:
        samples = parse(fp, label=args.label)

    total = len(samples["首字节"])
    if total == 0:
        print(
            "未匹配到任何 `耗时：...` 行。请确认运行时带了 `--show-latency`，且方向过滤是否正确。",
            file=sys.stderr,
        )
        return 1

    print(f"# Latency 分布（{args.label or '两路合并'}，共 {total} 段）\n")
    print(report(samples))
    print()
    print("注：首字节 = 排队 + 首PCM；TTS / 解码 在流式模式下被合入 首PCM，因此通常为 0。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
