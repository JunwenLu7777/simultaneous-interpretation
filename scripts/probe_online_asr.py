#!/usr/bin/env python3
"""用本地 WAV 探测 online-asr 的稳定 partial 延迟与 final 准确率。

示例：

    uv run --extra dev scripts/probe_online_asr.py /tmp/long_zh.wav \
      --expected-text "我们今天讨论现金流预测方案和下季度预算安排" \
      --max-first-partial-s 1.2 \
      --max-cer 0.12
"""

from __future__ import annotations

import argparse
import json
import time
import wave
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from teams_voice_interpreter.audio.resample import resample_int16_mono
from teams_voice_interpreter.config import normalize_whisper_model_name
from teams_voice_interpreter.data.audio_segment import AudioDirection
from teams_voice_interpreter.data.transcript import StableTranscriptChunk, TranscriptKind
from teams_voice_interpreter.live_ptt import WhisperOneShotTranscriber
from teams_voice_interpreter.stt.whisper_streaming import (
    OnlineASRProcessor,
    WhisperStreamingConfig,
)

_TRANSLATION_UNIT_BOUNDARY_CHARS = set(" \t\n\r,.!?;:，。！？；：、")
_MIN_CJK_TRANSLATION_UNIT_CHARS = 10
_MIN_WORD_TRANSLATION_UNIT_WORDS = 5


@dataclass(frozen=True)
class StableChunkObservation:
    """一次 stable partial 输出及其探针时刻。"""

    chunk: StableTranscriptChunk
    emitted_at_s: float
    translation_ready: bool = False


@dataclass(frozen=True)
class OnlineASRProbeResult:
    """online-asr 探针结果。"""

    duration_s: float
    transcribe_calls: int
    stable_chunks: list[StableTranscriptChunk]
    stable_observations: list[StableChunkObservation]
    final_text: str
    first_partial_s: float | None
    first_ready_partial_s: float | None
    first_confirmed_ready_partial_s: float | None
    final_asr_s: float
    cer: float | None


def load_wav_mono_int16(path: Path, *, target_rate_hz: int) -> np.ndarray:
    """读取 WAV，并转换为 16 kHz mono int16 PCM。"""
    with wave.open(str(path), "rb") as wav:
        channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
        source_rate_hz = wav.getframerate()
        frames = wav.readframes(wav.getnframes())
    if sample_width != 2:
        raise ValueError("只支持 16-bit PCM WAV。")
    samples = np.frombuffer(frames, dtype=np.int16)
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1).astype(np.int16)
    return resample_int16_mono(
        samples,
        source_rate_hz=source_rate_hz,
        target_rate_hz=target_rate_hz,
    )


def run_online_asr_probe(
    samples: np.ndarray,
    *,
    direction: AudioDirection,
    transcribe: Callable[[np.ndarray], str],
    sample_rate_hz: int = 16000,
    step_ms: int = 300,
    frame_ms: int = 30,
    expected_text: str | None = None,
) -> OnlineASRProbeResult:
    """喂入整段 PCM，周期性重跑 ASR，返回稳定 partial 与 final 指标。

    探针离线读取 WAV，但 partial 时间按实时音频到达下界计算：一帧音频只有在
    真实时间走到该帧末尾后才可被处理，再叠加同步 ASR 重跑耗时。这样不会把
    “离线快速喂完整段音频”的耗时误报成会议里的首段延迟。
    """
    transcribe_calls = 0

    def counted_transcribe(buffer: np.ndarray) -> str:
        nonlocal transcribe_calls
        transcribe_calls += 1
        return transcribe(buffer)

    processor = OnlineASRProcessor(
        direction=direction,
        transcribe_buffer=counted_transcribe,
        config=WhisperStreamingConfig(
            sample_rate_hz=sample_rate_hz,
            step_ms=step_ms,
        ),
    )
    stable_chunks: list[StableTranscriptChunk] = []
    stable_observations: list[StableChunkObservation] = []
    first_partial_s: float | None = None
    first_ready_partial_s: float | None = None
    pending_source = ""
    live_clock_s = 0.0
    audio_cursor_s = 0.0
    duration_s = float(np.asarray(samples).size) / sample_rate_hz
    for frame in _iter_frames(samples, sample_rate_hz=sample_rate_hz, frame_ms=frame_ms):
        audio_cursor_s += float(np.asarray(frame).size) / sample_rate_hz
        live_clock_s = max(live_clock_s, audio_cursor_s)
        process_started = time.perf_counter()
        chunks = processor.insert_audio_chunk(frame)
        live_clock_s += time.perf_counter() - process_started
        if chunks and first_partial_s is None:
            first_partial_s = live_clock_s
        for chunk in chunks:
            emitted_at_s = live_clock_s
            translation_ready = False
            if chunk.kind is not TranscriptKind.PARTIAL:
                continue
            pending_source = _join_transcript_parts(pending_source, chunk.delta_text)
            if _is_translation_unit_ready(pending_source):
                if first_ready_partial_s is None:
                    first_ready_partial_s = live_clock_s
                translation_ready = True
                pending_source = ""
            stable_observations.append(
                StableChunkObservation(
                    chunk=chunk,
                    emitted_at_s=emitted_at_s,
                    translation_ready=translation_ready,
                )
            )
        stable_chunks.extend(chunks)
    live_clock_s = max(live_clock_s, duration_s)
    final_started = time.perf_counter()
    final_text = counted_transcribe(np.asarray(samples, dtype=np.int16).reshape(-1))
    stable_chunks.extend(processor.close_segment(final_text=final_text))
    final_asr_s = time.perf_counter() - final_started
    first_confirmed_ready_partial_s = _first_confirmed_ready_partial_s(
        stable_observations,
        final_text=final_text,
    )
    reference = _normalize_for_cer(expected_text) if expected_text else None
    hypothesis = _normalize_for_cer(final_text)
    return OnlineASRProbeResult(
        duration_s=duration_s,
        transcribe_calls=transcribe_calls,
        stable_chunks=stable_chunks,
        stable_observations=stable_observations,
        final_text=final_text,
        first_partial_s=first_partial_s,
        first_ready_partial_s=first_ready_partial_s,
        first_confirmed_ready_partial_s=first_confirmed_ready_partial_s,
        final_asr_s=final_asr_s,
        cer=character_error_rate(reference, hypothesis) if reference is not None else None,
    )


def character_error_rate(reference: str, hypothesis: str) -> float:
    """按字符级 Levenshtein 距离计算 CER。"""
    if not reference:
        return 0.0 if not hypothesis else 1.0
    return _levenshtein(reference, hypothesis) / len(reference)


def render_report(result: OnlineASRProbeResult) -> str:
    """渲染 markdown 探针报告。"""
    partial_chunks = [
        chunk for chunk in result.stable_chunks if chunk.kind is TranscriptKind.PARTIAL
    ]
    first_partial = "n/a" if result.first_partial_s is None else f"{result.first_partial_s:.2f}s"
    first_ready = (
        "n/a" if result.first_ready_partial_s is None else f"{result.first_ready_partial_s:.2f}s"
    )
    first_confirmed_ready = (
        "n/a"
        if result.first_confirmed_ready_partial_s is None
        else f"{result.first_confirmed_ready_partial_s:.2f}s"
    )
    cer = "未提供 expected-text" if result.cer is None else f"{result.cer:.3f}"
    rows = [
        ("音频时长", f"{result.duration_s:.2f}s"),
        ("ASR 重跑次数", str(result.transcribe_calls)),
        ("稳定 partial 数", str(len(partial_chunks))),
        ("首个稳定 partial", first_partial),
        ("首个可翻译 stable partial", first_ready),
        ("首个 final 确认可翻译 stable partial", first_confirmed_ready),
        ("final ASR 耗时", f"{result.final_asr_s:.2f}s"),
        ("CER", cer),
    ]
    table = ["| 指标 | 值 |", "|------|----|"]
    table.extend(f"| {name} | {value} |" for name, value in rows)
    stable_lines = [
        f"- {index}. {chunk.delta_text}"
        for index, chunk in enumerate(partial_chunks, start=1)
        if chunk.delta_text.strip()
    ]
    if not stable_lines:
        stable_lines = ["- 未产生稳定 partial"]
    return "\n".join(
        [
            "# Online ASR 探针",
            "",
            *table,
            "",
            "## 稳定增量",
            *stable_lines,
            "",
            "## Final 文本",
            result.final_text,
        ]
    )


def threshold_failures(
    result: OnlineASRProbeResult,
    *,
    max_first_partial_s: float | None,
    max_cer: float | None,
) -> list[str]:
    """根据用户给定阈值返回 fail-closed 错误列表。"""
    failures: list[str] = []
    if max_first_partial_s is not None:
        if result.first_confirmed_ready_partial_s is None:
            failures.append(
                "FAIL: 未产生 final 可确认的可翻译 stable partial，无法满足首个 stable partial 阈值"
            )
        elif result.first_confirmed_ready_partial_s > max_first_partial_s:
            failures.append(
                "FAIL: 首个 final 确认可翻译 stable partial "
                f"{result.first_confirmed_ready_partial_s:.2f}s "
                f"> {max_first_partial_s:.2f}s"
            )
    if max_cer is not None:
        if result.cer is None:
            failures.append("FAIL: 使用 --max-cer 时必须同时提供 --expected-text")
        elif result.cer > max_cer:
            failures.append(f"FAIL: CER {result.cer:.3f} > {max_cer:.3f}")
    return failures


def proof_payload(
    result: OnlineASRProbeResult,
    *,
    max_first_partial_s: float | None,
    max_cer: float | None,
) -> dict[str, object]:
    """生成可供 doctor 读取的低延迟 proof JSON payload。"""
    failures = threshold_failures(
        result,
        max_first_partial_s=max_first_partial_s,
        max_cer=max_cer,
    )
    if max_first_partial_s is None:
        failures.append("FAIL: 生成低延迟 proof 必须提供 --max-first-partial-s")
    if max_cer is None:
        failures.append("FAIL: 生成低延迟 proof 必须提供 --max-cer")
    return {
        "schema_version": 1,
        "generated_by": "scripts/probe_online_asr.py",
        "passed": not failures,
        "failures": failures,
        "thresholds": {
            "max_first_partial_s": max_first_partial_s,
            "max_cer": max_cer,
        },
        "metrics": {
            "duration_s": result.duration_s,
            "transcribe_calls": result.transcribe_calls,
            "first_partial_s": result.first_partial_s,
            "first_ready_partial_s": result.first_ready_partial_s,
            "first_confirmed_ready_partial_s": result.first_confirmed_ready_partial_s,
            "final_asr_s": result.final_asr_s,
            "cer": result.cer,
        },
        "final_text": result.final_text,
    }


def write_proof_json(
    path: Path,
    result: OnlineASRProbeResult,
    *,
    max_first_partial_s: float | None,
    max_cer: float | None,
) -> None:
    """写出低延迟验收 proof JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            proof_payload(
                result,
                max_first_partial_s=max_first_partial_s,
                max_cer=max_cer,
            ),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _first_confirmed_ready_partial_s(
    observations: list[StableChunkObservation],
    *,
    final_text: str,
) -> float | None:
    for observation in observations:
        if observation.translation_ready and final_text.startswith(observation.chunk.text):
            return observation.emitted_at_s
    return None


def _iter_frames(
    samples: np.ndarray,
    *,
    sample_rate_hz: int,
    frame_ms: int,
) -> Iterable[np.ndarray]:
    frame_samples = max(1, int(sample_rate_hz * frame_ms / 1000))
    flat = np.asarray(samples, dtype=np.int16).reshape(-1)
    for offset in range(0, flat.size, frame_samples):
        yield flat[offset : offset + frame_samples]


def _join_transcript_parts(left: str, right: str) -> str:
    left_text = left.strip()
    right_text = right.strip()
    if not left_text:
        return right_text
    if not right_text:
        return left_text
    if _contains_cjk(left_text) or _contains_cjk(right_text):
        return f"{left_text}{right_text}"
    return f"{left_text} {right_text}"


def _is_translation_unit_ready(text: str) -> bool:
    source_text = text.strip()
    if not source_text:
        return False
    if source_text[-1] in _TRANSLATION_UNIT_BOUNDARY_CHARS:
        return True
    if _contains_cjk(source_text):
        return len(source_text) >= _MIN_CJK_TRANSLATION_UNIT_CHARS
    return len(source_text.split()) >= _MIN_WORD_TRANSLATION_UNIT_WORDS


def _contains_cjk(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def _normalize_for_cer(text: str) -> str:
    return "".join(text.lower().split())


def _levenshtein(left: str, right: str) -> int:
    previous = list(range(len(right) + 1))
    for left_index, left_char in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_char in enumerate(right, start=1):
            cost = 0 if left_char == right_char else 1
            current.append(
                min(
                    previous[right_index] + 1,
                    current[right_index - 1] + 1,
                    previous[right_index - 1] + cost,
                )
            )
        previous = current
    return previous[-1]


def _direction(value: str) -> AudioDirection:
    return AudioDirection.UPLINK if value == "uplink" else AudioDirection.DOWNLINK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wav_path", type=Path, help="待探测的 WAV 文件路径。")
    parser.add_argument("--model", default="small-q5_1", help="pywhispercpp 模型名。")
    parser.add_argument("--language", default="zh", help="Whisper 语言，例如 zh / en。")
    parser.add_argument(
        "--direction",
        choices=["uplink", "downlink"],
        default="uplink",
        help="识别方向，仅用于标记 stable chunk。",
    )
    parser.add_argument("--sample-rate-hz", type=int, default=16000, help="目标采样率。")
    parser.add_argument("--step-ms", type=int, default=300, help="online ASR 重跑步长。")
    parser.add_argument("--frame-ms", type=int, default=30, help="喂入 online ASR 的帧长。")
    parser.add_argument("--expected-text", default=None, help="用于计算 CER 的参考文本。")
    parser.add_argument(
        "--max-first-partial-s",
        type=float,
        default=None,
        help="首个 final 可确认、可翻译 stable partial 上限。",
    )
    parser.add_argument("--max-cer", type=float, default=None, help="CER 上限。")
    parser.add_argument(
        "--proof-json",
        type=Path,
        default=None,
        help="写出可供 `tvi doctor --low-latency-proof` 读取的低延迟验收 JSON。",
    )
    args = parser.parse_args(argv)

    samples = load_wav_mono_int16(args.wav_path, target_rate_hz=args.sample_rate_hz)
    transcriber = WhisperOneShotTranscriber(
        model_name=normalize_whisper_model_name(args.model),
        language=args.language,
    )
    result = run_online_asr_probe(
        samples,
        direction=_direction(args.direction),
        transcribe=transcriber.transcribe,
        sample_rate_hz=args.sample_rate_hz,
        step_ms=args.step_ms,
        frame_ms=args.frame_ms,
        expected_text=args.expected_text,
    )
    print(render_report(result))
    failures = threshold_failures(
        result,
        max_first_partial_s=args.max_first_partial_s,
        max_cer=args.max_cer,
    )
    if args.proof_json is not None:
        proof = proof_payload(
            result,
            max_first_partial_s=args.max_first_partial_s,
            max_cer=args.max_cer,
        )
        write_proof_json(
            args.proof_json,
            result,
            max_first_partial_s=args.max_first_partial_s,
            max_cer=args.max_cer,
        )
        proof_failures = proof.get("failures", [])
        if isinstance(proof_failures, list):
            failures.extend(failure for failure in proof_failures if isinstance(failure, str))
        failures = list(dict.fromkeys(failures))
    if failures:
        print()
        print("\n".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
