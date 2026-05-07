#!/usr/bin/env python3
"""测量 Piper TTS first byte 延迟（TTS 引擎对比探针）。

对应 BM-6 真实化后启动的 D 路径任务：评估能否换免费 TTS 引擎让端到端
首段延迟回到 SC-001 ≤ 1200 ms 硬阈值内。本脚本与 BM-4 / BM-6 探针对仗，
对一组商务译文样本（上下行各 N 句）调用 `PiperVoice.synthesize`，记录
从迭代开始到首个 `AudioChunk` 到达的 wall-clock 耗时。

依赖：

- `piper-tts`（已在 pyproject.toml `[project.optional-dependencies].dev`）
- 模型文件 `~/.cache/teams-voice-interpreter/piper-models/en_US-amy-medium.onnx`
  与 `zh_CN-huayan-medium.onnx`（含同名 `.json` 配置）；首次运行前请按
  Piper 文档手动从 `https://huggingface.co/rhasspy/piper-voices` 下载。

样本与方向口径（与 BM-6 一致）：

- 上行 TTS：输入英文译文（zh→en 链路出口），音色 `en_US-amy-medium`
- 下行 TTS：输入中文译文（en→zh 链路出口），音色 `zh_CN-huayan-medium`

注意：与 Edge-TTS / DeepSeek 探针不同，Piper 的 `synthesize` 是同步
generator（不是 async），首字节时刻就是第一个 yield AudioChunk 的时刻。

示例：

    uv run --extra dev scripts/measure_piper_first_byte.py \
      --samples-per-direction 15 \
      --proof-json /tmp/piper-first-byte.json
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from dataclasses import dataclass
from pathlib import Path

from piper import PiperVoice

from teams_voice_interpreter.data.audio_segment import AudioDirection

_DEFAULT_MODELS_DIR = Path.home() / ".cache/teams-voice-interpreter/piper-models"

_UPLINK_VOICE = "en_US-amy-medium"
_DOWNLINK_VOICE = "zh_CN-huayan-medium"

# 上行 TTS 输入：英文译文（zh→en 链路出口）—— 与 BM-6 上行样本一致
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

# 下行 TTS 输入：中文译文（en→zh 链路出口）—— 与 BM-6 下行样本一致
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
    """单方向延迟分布摘要。"""

    direction: AudioDirection
    voice: str
    success_count: int
    failure_count: int
    p50_ms: float | None
    p95_ms: float | None
    avg_ms: float | None
    max_ms: float | None


def measure_first_byte(
    voice: PiperVoice,
    text: str,
    *,
    direction: AudioDirection,
    voice_name: str,
) -> FirstByteSample:
    """对单条文本调用一次 synthesize，返回首字节耗时记录。

    必须消费到迭代结束才返回，避免 generator 内 ONNX session 泄露。
    """
    started = time.perf_counter()
    first_byte_s: float | None = None
    chunk_count = 0
    try:
        for _chunk in voice.synthesize(text):
            elapsed = time.perf_counter() - started
            if first_byte_s is None:
                first_byte_s = elapsed
            chunk_count += 1
        completed_s = time.perf_counter() - started
    except Exception as error:  # pragma: no cover - 防御 ONNX / IO 异常
        return FirstByteSample(
            direction=direction,
            voice=voice_name,
            text=text,
            first_byte_s=None,
            completed_s=None,
            error=f"{type(error).__name__}: {error}",
        )
    if chunk_count == 0:
        return FirstByteSample(
            direction=direction,
            voice=voice_name,
            text=text,
            first_byte_s=None,
            completed_s=None,
            error="Piper 未返回任何 audio chunk",
        )
    return FirstByteSample(
        direction=direction,
        voice=voice_name,
        text=text,
        first_byte_s=first_byte_s,
        completed_s=completed_s,
    )


def summarize(samples: list[FirstByteSample], *, direction: AudioDirection) -> DirectionSummary:
    """汇总单方向延迟分布。"""
    direction_samples = [sample for sample in samples if sample.direction is direction]
    voice = direction_samples[0].voice if direction_samples else _voice_for_direction(direction)
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
    """nearest-rank percentile：与其他探针同款语义。"""
    sorted_values = sorted(values)
    if not sorted_values:
        msg = "_percentile 不能用于空列表。"
        raise ValueError(msg)
    raw_index = math.ceil(rank / 100.0 * len(sorted_values)) - 1
    index = max(0, min(len(sorted_values) - 1, raw_index))
    return sorted_values[index]


def _voice_for_direction(direction: AudioDirection) -> str:
    return _UPLINK_VOICE if direction is AudioDirection.UPLINK else _DOWNLINK_VOICE


def _format_ms(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f} ms"


def render_report(
    samples: list[FirstByteSample], summaries: list[DirectionSummary]
) -> str:
    """渲染 markdown 报告。"""
    lines: list[str] = ["# Piper TTS first byte 首字节延迟", ""]
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
    *,
    models_dir: Path,
) -> dict[str, object]:
    """生成 proof JSON payload。"""
    return {
        "schema_version": 1,
        "generated_by": "scripts/measure_piper_first_byte.py",
        "engine": "piper",
        "models_dir": str(models_dir),
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
    """返回 (direction, voice_name, text) 测量计划。"""
    plan: list[tuple[AudioDirection, str, str]] = []
    for text in _UPLINK_SAMPLES[:samples_per_direction]:
        plan.append((AudioDirection.UPLINK, _UPLINK_VOICE, text))
    for text in _DOWNLINK_SAMPLES[:samples_per_direction]:
        plan.append((AudioDirection.DOWNLINK, _DOWNLINK_VOICE, text))
    return plan


def load_voice(models_dir: Path, voice_name: str) -> PiperVoice:
    """加载 Piper voice，若模型缺失则给出明确指引。"""
    onnx_path = models_dir / f"{voice_name}.onnx"
    json_path = models_dir / f"{voice_name}.onnx.json"
    if not onnx_path.exists() or not json_path.exists():
        msg = (
            f"缺少 Piper 模型 {voice_name}（{onnx_path}）。"
            "下一步：从 https://huggingface.co/rhasspy/piper-voices 下载 "
            f"{voice_name}.onnx 和 {voice_name}.onnx.json 到 {models_dir}/。"
        )
        raise FileNotFoundError(msg)
    return PiperVoice.load(str(onnx_path))


def run_measurement(
    voices: dict[str, PiperVoice],
    plan: list[tuple[AudioDirection, str, str]],
) -> list[FirstByteSample]:
    """按计划串行测量并返回所有样本。"""
    samples: list[FirstByteSample] = []
    for direction, voice_name, text in plan:
        voice = voices[voice_name]
        samples.append(
            measure_first_byte(voice, text, direction=direction, voice_name=voice_name)
        )
    return samples


def main(argv: list[str] | None = None) -> int:
    """脚本入口：加载模型 → 串行测量 → 渲染报告 → 可选 proof JSON。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--samples-per-direction",
        type=int,
        default=15,
        help="每方向采样数；最大值受样本池容量限制。",
    )
    parser.add_argument(
        "--models-dir",
        type=Path,
        default=_DEFAULT_MODELS_DIR,
        help="Piper 模型目录。",
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

    try:
        voices = {
            _UPLINK_VOICE: load_voice(args.models_dir, _UPLINK_VOICE),
            _DOWNLINK_VOICE: load_voice(args.models_dir, _DOWNLINK_VOICE),
        }
    except FileNotFoundError as error:
        print(f"FAIL: {error}")
        return 1

    plan = _direction_plan(args.samples_per_direction)
    samples = run_measurement(voices, plan)
    summaries = [
        summarize(samples, direction=AudioDirection.UPLINK),
        summarize(samples, direction=AudioDirection.DOWNLINK),
    ]
    print(render_report(samples, summaries))
    if args.proof_json is not None:
        write_proof_json(
            args.proof_json,
            proof_payload(samples, summaries, models_dir=args.models_dir),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
