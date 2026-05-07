#!/usr/bin/env python3
"""测量 Whisper.cpp 整段 ASR 耗时随段长的变化（C+α 评估）。

对应 perf-report.md「Online ASR 实验探针」主线之后的衍生任务。用 macOS
`say` 合成不同目标长度的单句 WAV，对每段跑一次整段 ASR，记录 audio
duration 与 ASR final 耗时；目标是回答以下问题：

- ASR 整段耗时随段长是否线性增长？
- 在生产典型段长 (≈ 3-10 s) 下，ASR 是否吃掉 SC-001 ≤ 800 ms 首段延迟
  预算的大头？
- 上行 (zh) 与下行 (en) 的整段 ASR 耗时是否有显著差异？

示例：

    uv run --extra dev scripts/measure_asr_segment_latency.py \
      --proof-json /tmp/asr-segment-latency.json
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
import wave
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from teams_voice_interpreter.audio.resample import resample_int16_mono
from teams_voice_interpreter.config import normalize_whisper_model_name
from teams_voice_interpreter.data.audio_segment import AudioDirection
from teams_voice_interpreter.live_ptt import WhisperOneShotTranscriber

_UPLINK_VOICE = "Tingting"
_DOWNLINK_VOICE = "Samantha"

# 每档：(target_seconds, voice, text)。实际 say 输出长度可能与 target
# 偏离 ~20%，以实测 audio duration 为准。
_UPLINK_SAMPLES: tuple[tuple[float, str, str], ...] = (
    (3.0, _UPLINK_VOICE, "下次会议三点开始"),
    (6.0, _UPLINK_VOICE, "请把上季度的销售数据汇总后发给市场部"),
    (
        10.0,
        _UPLINK_VOICE,
        "我们计划在第三季度推出云端协同与数据分析两个核心新功能并提前两周开放灰度测试",
    ),
)

_DOWNLINK_SAMPLES: tuple[tuple[float, str, str], ...] = (
    (3.0, _DOWNLINK_VOICE, "Let's start the meeting at three."),
    (
        6.0,
        _DOWNLINK_VOICE,
        "Please send the consolidated sales data from last quarter to marketing.",
    ),
    (
        10.0,
        _DOWNLINK_VOICE,
        (
            "We plan to launch cloud collaboration and analytics in the third quarter "
            "and open a beta two weeks ahead of release."
        ),
    ),
)


@dataclass(frozen=True)
class SegmentMeasurement:
    """单段 ASR 耗时测量记录。"""

    direction: AudioDirection
    voice: str
    text: str
    target_seconds: float
    actual_duration_s: float
    final_asr_s: float
    final_text: str

    @property
    def asr_to_duration_ratio(self) -> float:
        """ASR final 耗时占音频时长的比例。"""
        if self.actual_duration_s <= 0.0:
            return 0.0
        return self.final_asr_s / self.actual_duration_s


def synthesize_say_wav(
    *,
    voice: str,
    text: str,
    output_path: Path,
    target_rate_hz: int = 16000,
) -> None:
    """用 macOS `say` + `afconvert` 合成 16 kHz mono PCM16 WAV。"""
    if shutil.which("say") is None or shutil.which("afconvert") is None:
        msg = "本脚本依赖 macOS `say` 与 `afconvert`，请在 macOS 上运行。"
        raise RuntimeError(msg)
    aiff_path = output_path.with_suffix(".aiff")
    subprocess.run(["say", "-v", voice, text, "-o", str(aiff_path)], check=True)
    subprocess.run(
        [
            "afconvert",
            "-f",
            "WAVE",
            "-d",
            "LEI16",
            "-c",
            "1",
            "-r",
            str(target_rate_hz),
            str(aiff_path),
            str(output_path),
        ],
        check=True,
    )
    aiff_path.unlink(missing_ok=True)


def load_wav_mono_int16(
    path: Path,
    *,
    target_rate_hz: int,
) -> tuple[np.ndarray, float]:
    """读取 WAV，返回 (16 kHz mono int16 PCM, 实测时长)。"""
    with wave.open(str(path), "rb") as wav:
        channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
        source_rate_hz = wav.getframerate()
        frames = wav.readframes(wav.getnframes())
    if sample_width != 2:
        msg = "只支持 16-bit PCM WAV。"
        raise ValueError(msg)
    samples = np.frombuffer(frames, dtype=np.int16)
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1).astype(np.int16)
    duration_s = float(samples.size) / source_rate_hz
    resampled = resample_int16_mono(
        samples,
        source_rate_hz=source_rate_hz,
        target_rate_hz=target_rate_hz,
    )
    return resampled, duration_s


def measure_segment(
    samples: np.ndarray,
    *,
    transcriber: WhisperOneShotTranscriber,
) -> tuple[float, str]:
    """对完整段音频跑一次整段 ASR，返回 (final_asr_s, final_text)。"""
    started = time.perf_counter()
    final_text = transcriber.transcribe(samples)
    return time.perf_counter() - started, final_text


def render_report(measurements: list[SegmentMeasurement]) -> str:
    """渲染 markdown 表与简要结论。"""
    rows = [
        (
            measurement.direction.value,
            measurement.voice,
            f"{measurement.target_seconds:.1f}s",
            f"{measurement.actual_duration_s:.2f}s",
            f"{measurement.final_asr_s:.3f}s",
            f"{measurement.asr_to_duration_ratio:.2%}",
            measurement.text,
        )
        for measurement in measurements
    ]
    table = [
        "| 方向 | 音色 | 目标段长 | 实际段长 | ASR final 耗时 | ASR 占比 (final/duration) | 原文 |",
        "|------|------|---------|----------|----------------|---------------------------|------|",
    ]
    table.extend(
        f"| {direction} | `{voice}` | {target} | {actual} | {asr} | {ratio} | {text} |"
        for direction, voice, target, actual, asr, ratio, text in rows
    )
    return "\n".join(["# ASR 整段耗时 vs 段长", "", *table])


def proof_payload(
    measurements: list[SegmentMeasurement],
    *,
    model_name: str,
    sample_rate_hz: int,
) -> dict[str, object]:
    """生成可读 proof payload。"""
    return {
        "schema_version": 1,
        "generated_by": "scripts/measure_asr_segment_latency.py",
        "model": model_name,
        "sample_rate_hz": sample_rate_hz,
        "measurements": [
            {
                "direction": measurement.direction.value,
                "voice": measurement.voice,
                "target_seconds": measurement.target_seconds,
                "actual_duration_s": measurement.actual_duration_s,
                "final_asr_s": measurement.final_asr_s,
                "asr_to_duration_ratio": measurement.asr_to_duration_ratio,
                "final_text": measurement.final_text,
                "text": measurement.text,
            }
            for measurement in measurements
        ],
    }


def write_proof_json(path: Path, payload: dict[str, object]) -> None:
    """写出测量 proof JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _planned_samples() -> Iterable[tuple[AudioDirection, str, str, str, float]]:
    for target_seconds, voice, text in _UPLINK_SAMPLES:
        yield AudioDirection.UPLINK, voice, text, "zh", target_seconds
    for target_seconds, voice, text in _DOWNLINK_SAMPLES:
        yield AudioDirection.DOWNLINK, voice, text, "en", target_seconds


def main(argv: list[str] | None = None) -> int:
    """脚本入口：合成 → ASR → 报告 → 可选 proof JSON。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="small-q5_1", help="pywhispercpp 模型名。")
    parser.add_argument("--sample-rate-hz", type=int, default=16000, help="目标采样率。")
    parser.add_argument(
        "--workdir",
        type=Path,
        default=Path("/tmp/tvi_asr_segment_measurements"),
        help="生成 say WAV 的工作目录。",
    )
    parser.add_argument(
        "--proof-json",
        type=Path,
        default=None,
        help="写出测量 proof JSON。",
    )
    args = parser.parse_args(argv)

    args.workdir.mkdir(parents=True, exist_ok=True)
    measurements: list[SegmentMeasurement] = []
    transcribers: dict[str, WhisperOneShotTranscriber] = {}
    model_name = normalize_whisper_model_name(args.model)

    for direction, voice, text, language, target_seconds in _planned_samples():
        wav_name = f"{direction.value}_{voice}_{int(target_seconds * 10):04d}.wav"
        wav_path = args.workdir / wav_name
        synthesize_say_wav(
            voice=voice,
            text=text,
            output_path=wav_path,
            target_rate_hz=args.sample_rate_hz,
        )
        samples, actual_duration_s = load_wav_mono_int16(
            wav_path, target_rate_hz=args.sample_rate_hz
        )
        transcriber = transcribers.setdefault(
            language,
            WhisperOneShotTranscriber(model_name=model_name, language=language),
        )
        final_asr_s, final_text = measure_segment(samples, transcriber=transcriber)
        measurements.append(
            SegmentMeasurement(
                direction=direction,
                voice=voice,
                text=text,
                target_seconds=target_seconds,
                actual_duration_s=actual_duration_s,
                final_asr_s=final_asr_s,
                final_text=final_text,
            )
        )

    print(render_report(measurements))
    if args.proof_json is not None:
        write_proof_json(
            args.proof_json,
            proof_payload(
                measurements,
                model_name=model_name,
                sample_rate_hz=args.sample_rate_hz,
            ),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
