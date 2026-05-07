#!/usr/bin/env python3
"""测量无人值守 E2E 首段译音耗时（BM-10 / BM-10D）。

本脚本用 macOS `say` 生成固定商务样本 WAV，然后串行复用当前生产模块：

1. WhisperOneShotTranscriber 整段 ASR
2. DeepSeekStreamingClient 流式翻译，但当前生产 TTS 在 MT completed 后启动
3. Piper/Edge-TTS factory 返回的 TTS client，测第一个音频事件到达

因此报告同时给出两个口径：

- post_segment_first_audio_s：语音段闭合后，到首个译音 audio chunk 的处理耗时
- speech_start_first_audio_s：代理口径，从合成输入音频开头算起，额外包含音频段本身时长

示例：

    uv run --extra dev scripts/measure_e2e_first_segment.py \
      --samples-per-direction 3 \
      --proof-json /tmp/e2e-first-segment.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import shutil
import statistics
import subprocess
import time
import wave
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

from teams_voice_interpreter.audio.resample import resample_int16_mono
from teams_voice_interpreter.config import Settings, load_settings
from teams_voice_interpreter.data.audio_segment import AudioDirection
from teams_voice_interpreter.errors import UserFacingError
from teams_voice_interpreter.live_ptt import WhisperOneShotTranscriber
from teams_voice_interpreter.mt.deepseek_client import DeepSeekStreamingClient
from teams_voice_interpreter.tts.factory import build_tts_client

_UPLINK_VOICE = "Tingting"
_DOWNLINK_VOICE = "Samantha"

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
    """读取 WAV，返回 (target_rate_hz mono int16 PCM, 实测时长)。"""
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


class _TranslationClient(Protocol):
    def stream_translate(
        self,
        text: str,
        *,
        direction: AudioDirection,
        context_text: str = "",
    ) -> object: ...


class _TTSClient(Protocol):
    def stream_synthesize(
        self,
        text: str,
        *,
        direction: AudioDirection,
        voice: str | None = None,
    ) -> object: ...


@dataclass(frozen=True)
class MTMeasurement:
    """单段 MT 耗时与完整译文。"""

    first_token_s: float | None
    completed_s: float | None
    translated_text: str
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return (
            self.completed_s is not None
            and not self.error
            and bool(self.translated_text.strip())
        )


@dataclass(frozen=True)
class TTSMeasurement:
    """单段 TTS 首字节与完成耗时。"""

    first_byte_s: float | None
    completed_s: float | None
    audio_format: str
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.first_byte_s is not None and not self.error


@dataclass(frozen=True)
class E2ESample:
    """一条首段译音 E2E replay 样本。"""

    direction: AudioDirection
    source_voice: str
    source_text: str
    target_seconds: float
    actual_audio_duration_s: float
    asr_final_s: float | None
    asr_text: str
    mt_first_token_s: float | None
    mt_completed_s: float | None
    translated_text: str
    tts_first_byte_s: float | None
    tts_completed_s: float | None
    tts_audio_format: str
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return (
            self.error is None
            and self.asr_final_s is not None
            and self.mt_completed_s is not None
            and self.tts_first_byte_s is not None
        )

    @property
    def post_segment_first_audio_s(self) -> float | None:
        """语音段闭合后，到首个译音 audio chunk 的处理耗时。"""
        if (
            self.asr_final_s is None
            or self.mt_completed_s is None
            or self.tts_first_byte_s is None
        ):
            return None
        return self.asr_final_s + self.mt_completed_s + self.tts_first_byte_s

    @property
    def speech_start_first_audio_s(self) -> float | None:
        """从输入音频开头算起的代理耗时，体现整段 ASR 当前边界。"""
        post_segment = self.post_segment_first_audio_s
        if post_segment is None:
            return None
        return self.actual_audio_duration_s + post_segment


@dataclass(frozen=True)
class DirectionSummary:
    """单方向 E2E 首段耗时分布摘要。"""

    direction: AudioDirection
    success_count: int
    failure_count: int
    post_segment_p50_ms: float | None
    post_segment_p95_ms: float | None
    speech_start_p50_ms: float | None
    speech_start_p95_ms: float | None
    avg_post_segment_ms: float | None
    max_post_segment_ms: float | None


def measure_asr(
    samples: np.ndarray,
    *,
    transcriber: WhisperOneShotTranscriber,
) -> tuple[float, str]:
    """对完整段音频跑一次整段 ASR，返回 (final 耗时, 文本)。"""
    started = time.perf_counter()
    final_text = transcriber.transcribe(samples)
    return time.perf_counter() - started, final_text


async def measure_mt(
    client: _TranslationClient,
    text: str,
    *,
    direction: AudioDirection,
) -> MTMeasurement:
    """测量 MT first token 与 completed，并返回完整译文。"""
    started = time.perf_counter()
    first_token_s: float | None = None
    completed_s: float | None = None
    pieces: list[str] = []
    try:
        iterator = client.stream_translate(text, direction=direction)
        async for chunk in iterator:  # type: ignore[attr-defined]
            elapsed = time.perf_counter() - started
            if chunk.kind == "delta":
                if first_token_s is None:
                    first_token_s = elapsed
                pieces.append(chunk.text)
            elif chunk.kind == "completed":
                completed_s = elapsed
                if not pieces and chunk.text:
                    pieces.append(chunk.text)
                break
    except UserFacingError as error:
        return MTMeasurement(
            first_token_s=None,
            completed_s=None,
            translated_text="",
            error=f"{error.what_happened} | {error.next_action}",
        )
    except Exception as error:  # pragma: no cover - 防御网络异常
        return MTMeasurement(
            first_token_s=None,
            completed_s=None,
            translated_text="",
            error=f"{type(error).__name__}: {error}",
        )
    translated_text = "".join(pieces).strip()
    if not translated_text:
        return MTMeasurement(
            first_token_s=first_token_s,
            completed_s=completed_s,
            translated_text="",
            error="DeepSeek 未返回有效译文。",
        )
    return MTMeasurement(
        first_token_s=first_token_s,
        completed_s=completed_s,
        translated_text=translated_text,
    )


async def measure_tts_first_byte(
    client: _TTSClient,
    text: str,
    *,
    direction: AudioDirection,
) -> TTSMeasurement:
    """测量 TTS 首个 audio event 到达时间；消费到 completed 后返回。"""
    started = time.perf_counter()
    first_byte_s: float | None = None
    completed_s: float | None = None
    audio_format = ""
    try:
        iterator = client.stream_synthesize(text, direction=direction)
        async for event in iterator:  # type: ignore[attr-defined]
            elapsed = time.perf_counter() - started
            audio_format = event.audio_format or audio_format
            if event.kind == "first_byte" and first_byte_s is None:
                first_byte_s = elapsed
            elif event.kind == "audio_chunk" and first_byte_s is None:
                first_byte_s = elapsed
            elif event.kind == "completed":
                completed_s = elapsed
                break
    except UserFacingError as error:
        return TTSMeasurement(
            first_byte_s=None,
            completed_s=None,
            audio_format=audio_format,
            error=f"{error.what_happened} | {error.next_action}",
        )
    except Exception as error:  # pragma: no cover - 防御 ONNX / IO 异常
        return TTSMeasurement(
            first_byte_s=None,
            completed_s=None,
            audio_format=audio_format,
            error=f"{type(error).__name__}: {error}",
        )
    if first_byte_s is None:
        return TTSMeasurement(
            first_byte_s=None,
            completed_s=completed_s,
            audio_format=audio_format,
            error="TTS 未返回首个 audio chunk。",
        )
    return TTSMeasurement(
        first_byte_s=first_byte_s,
        completed_s=completed_s,
        audio_format=audio_format,
    )


async def measure_e2e_sample(
    *,
    direction: AudioDirection,
    source_voice: str,
    source_text: str,
    target_seconds: float,
    actual_audio_duration_s: float,
    samples: np.ndarray,
    transcriber: WhisperOneShotTranscriber,
    mt_client: _TranslationClient,
    tts_client: _TTSClient,
) -> E2ESample:
    """串起 ASR → MT completed → TTS first byte，返回单条 E2E 样本。"""
    try:
        asr_final_s, asr_text = measure_asr(samples, transcriber=transcriber)
    except UserFacingError as error:
        return E2ESample(
            direction=direction,
            source_voice=source_voice,
            source_text=source_text,
            target_seconds=target_seconds,
            actual_audio_duration_s=actual_audio_duration_s,
            asr_final_s=None,
            asr_text="",
            mt_first_token_s=None,
            mt_completed_s=None,
            translated_text="",
            tts_first_byte_s=None,
            tts_completed_s=None,
            tts_audio_format="",
            error=f"{error.what_happened} | {error.next_action}",
        )
    except Exception as error:  # pragma: no cover - 防御模型异常
        return E2ESample(
            direction=direction,
            source_voice=source_voice,
            source_text=source_text,
            target_seconds=target_seconds,
            actual_audio_duration_s=actual_audio_duration_s,
            asr_final_s=None,
            asr_text="",
            mt_first_token_s=None,
            mt_completed_s=None,
            translated_text="",
            tts_first_byte_s=None,
            tts_completed_s=None,
            tts_audio_format="",
            error=f"{type(error).__name__}: {error}",
        )

    mt = await measure_mt(mt_client, asr_text, direction=direction)
    if not mt.succeeded:
        return E2ESample(
            direction=direction,
            source_voice=source_voice,
            source_text=source_text,
            target_seconds=target_seconds,
            actual_audio_duration_s=actual_audio_duration_s,
            asr_final_s=asr_final_s,
            asr_text=asr_text,
            mt_first_token_s=mt.first_token_s,
            mt_completed_s=mt.completed_s,
            translated_text=mt.translated_text,
            tts_first_byte_s=None,
            tts_completed_s=None,
            tts_audio_format="",
            error=mt.error,
        )

    tts = await measure_tts_first_byte(tts_client, mt.translated_text, direction=direction)
    return E2ESample(
        direction=direction,
        source_voice=source_voice,
        source_text=source_text,
        target_seconds=target_seconds,
        actual_audio_duration_s=actual_audio_duration_s,
        asr_final_s=asr_final_s,
        asr_text=asr_text,
        mt_first_token_s=mt.first_token_s,
        mt_completed_s=mt.completed_s,
        translated_text=mt.translated_text,
        tts_first_byte_s=tts.first_byte_s,
        tts_completed_s=tts.completed_s,
        tts_audio_format=tts.audio_format,
        error=tts.error,
    )


def summarize(samples: list[E2ESample], *, direction: AudioDirection) -> DirectionSummary:
    """汇总单方向 E2E 耗时分布。"""
    direction_samples = [sample for sample in samples if sample.direction is direction]
    post_segment_ms = [
        value * 1000.0
        for sample in direction_samples
        if sample.succeeded and (value := sample.post_segment_first_audio_s) is not None
    ]
    speech_start_ms = [
        value * 1000.0
        for sample in direction_samples
        if sample.succeeded and (value := sample.speech_start_first_audio_s) is not None
    ]
    if not post_segment_ms:
        return DirectionSummary(
            direction=direction,
            success_count=0,
            failure_count=len(direction_samples),
            post_segment_p50_ms=None,
            post_segment_p95_ms=None,
            speech_start_p50_ms=None,
            speech_start_p95_ms=None,
            avg_post_segment_ms=None,
            max_post_segment_ms=None,
        )
    return DirectionSummary(
        direction=direction,
        success_count=len(post_segment_ms),
        failure_count=len(direction_samples) - len(post_segment_ms),
        post_segment_p50_ms=_percentile(post_segment_ms, 50),
        post_segment_p95_ms=_percentile(post_segment_ms, 95),
        speech_start_p50_ms=_percentile(speech_start_ms, 50),
        speech_start_p95_ms=_percentile(speech_start_ms, 95),
        avg_post_segment_ms=statistics.mean(post_segment_ms),
        max_post_segment_ms=max(post_segment_ms),
    )


def _percentile(values: list[float], rank: int) -> float:
    """nearest-rank percentile：与现有 benchmark 探针保持一致。"""
    sorted_values = sorted(values)
    if not sorted_values:
        msg = "_percentile 不能用于空列表。"
        raise ValueError(msg)
    raw_index = math.ceil(rank / 100.0 * len(sorted_values)) - 1
    index = max(0, min(len(sorted_values) - 1, raw_index))
    return sorted_values[index]


def _format_ms(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f} ms"


def _format_s(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}s"


def render_report(samples: list[E2ESample], summaries: list[DirectionSummary]) -> str:
    """渲染 markdown 报告。"""
    lines: list[str] = ["# E2E 首段译音无人值守 replay", ""]
    lines.append("## 分方向汇总")
    lines.append("")
    lines.append(
        "| 方向 | 成功 | 失败 | 段闭合后 p50 | 段闭合后 p95 "
        "| 从音频开头 p50 | 从音频开头 p95 | avg | max |"
    )
    lines.append(
        "|------|------|------|-------------|-------------"
        "|---------------|---------------|-----|-----|"
    )
    for summary in summaries:
        lines.append(
            f"| {summary.direction.value} | {summary.success_count} | {summary.failure_count} "
            f"| {_format_ms(summary.post_segment_p50_ms)} "
            f"| {_format_ms(summary.post_segment_p95_ms)} "
            f"| {_format_ms(summary.speech_start_p50_ms)} "
            f"| {_format_ms(summary.speech_start_p95_ms)} "
            f"| {_format_ms(summary.avg_post_segment_ms)} "
            f"| {_format_ms(summary.max_post_segment_ms)} |"
        )
    lines.extend(["", "## 样本明细", ""])
    lines.append(
        "| 方向 | 段长 | ASR | MT first | MT done | TTS first "
        "| 段闭合后首音 | 音频开头首音 | ASR 文本 | 译文 | 错误 |"
    )
    lines.append(
        "|------|------|-----|----------|---------|-----------|--------------|--------------|----------|------|------|"
    )
    for sample in samples:
        lines.append(
            f"| {sample.direction.value} "
            f"| {_format_s(sample.actual_audio_duration_s)} "
            f"| {_format_s(sample.asr_final_s)} "
            f"| {_format_s(sample.mt_first_token_s)} "
            f"| {_format_s(sample.mt_completed_s)} "
            f"| {_format_s(sample.tts_first_byte_s)} "
            f"| {_format_s(sample.post_segment_first_audio_s)} "
            f"| {_format_s(sample.speech_start_first_audio_s)} "
            f"| {_escape_cell(sample.asr_text)} "
            f"| {_escape_cell(sample.translated_text)} "
            f"| {_escape_cell(sample.error or '')} |"
        )
    return "\n".join(lines)


def proof_payload(
    samples: list[E2ESample],
    summaries: list[DirectionSummary],
    *,
    settings: Settings,
    sample_rate_hz: int,
) -> dict[str, object]:
    """生成 proof JSON payload。"""
    return {
        "schema_version": 1,
        "generated_by": "scripts/measure_e2e_first_segment.py",
        "definitions": {
            "post_segment_first_audio_s": "speech segment closed -> first translated audio chunk",
            "speech_start_first_audio_s": "input audio start -> first translated audio chunk proxy",
            "current_pipeline_order": "ASR final -> MT completed -> TTS first byte",
        },
        "settings": {
            "model_name": settings.resolved_whisper_model_name(),
            "deepseek_model": settings.deepseek_model,
            "tts_engine": settings.tts_engine,
            "piper_models_dir": str(settings.resolved_piper_models_dir()),
        },
        "sample_rate_hz": sample_rate_hz,
        "summaries": [
            {
                "direction": summary.direction.value,
                "success_count": summary.success_count,
                "failure_count": summary.failure_count,
                "post_segment_p50_ms": summary.post_segment_p50_ms,
                "post_segment_p95_ms": summary.post_segment_p95_ms,
                "speech_start_p50_ms": summary.speech_start_p50_ms,
                "speech_start_p95_ms": summary.speech_start_p95_ms,
                "avg_post_segment_ms": summary.avg_post_segment_ms,
                "max_post_segment_ms": summary.max_post_segment_ms,
            }
            for summary in summaries
        ],
        "samples": [
            {
                "direction": sample.direction.value,
                "source_voice": sample.source_voice,
                "source_text": sample.source_text,
                "target_seconds": sample.target_seconds,
                "actual_audio_duration_s": sample.actual_audio_duration_s,
                "asr_final_s": sample.asr_final_s,
                "asr_text": sample.asr_text,
                "mt_first_token_s": sample.mt_first_token_s,
                "mt_completed_s": sample.mt_completed_s,
                "translated_text": sample.translated_text,
                "tts_first_byte_s": sample.tts_first_byte_s,
                "tts_completed_s": sample.tts_completed_s,
                "tts_audio_format": sample.tts_audio_format,
                "post_segment_first_audio_s": sample.post_segment_first_audio_s,
                "speech_start_first_audio_s": sample.speech_start_first_audio_s,
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


def _escape_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def _planned_samples(
    samples_per_direction: int,
) -> Iterable[tuple[AudioDirection, str, str, str, float]]:
    for target_seconds, voice, text in _UPLINK_SAMPLES[:samples_per_direction]:
        yield AudioDirection.UPLINK, voice, text, "zh", target_seconds
    for target_seconds, voice, text in _DOWNLINK_SAMPLES[:samples_per_direction]:
        yield AudioDirection.DOWNLINK, voice, text, "en", target_seconds


async def run_measurement(
    *,
    settings: Settings,
    samples_per_direction: int,
    sample_rate_hz: int,
    workdir: Path,
) -> list[E2ESample]:
    """按计划串行测量所有样本。"""
    workdir.mkdir(parents=True, exist_ok=True)
    transcribers: dict[str, WhisperOneShotTranscriber] = {}
    mt_client = DeepSeekStreamingClient(
        api_key=settings.resolved_deepseek_api_key(),
        model=settings.deepseek_model,
    )
    tts_client = build_tts_client(settings)
    records: list[E2ESample] = []

    for direction, voice, text, language, target_seconds in _planned_samples(samples_per_direction):
        wav_name = f"{direction.value}_{voice}_{int(target_seconds * 10):04d}.wav"
        wav_path = workdir / wav_name
        synthesize_say_wav(
            voice=voice,
            text=text,
            output_path=wav_path,
            target_rate_hz=sample_rate_hz,
        )
        pcm, actual_duration_s = load_wav_mono_int16(wav_path, target_rate_hz=sample_rate_hz)
        transcriber = transcribers.setdefault(
            language,
            WhisperOneShotTranscriber(
                model_name=settings.resolved_whisper_model_name(),
                language=language,
            ),
        )
        records.append(
            await measure_e2e_sample(
                direction=direction,
                source_voice=voice,
                source_text=text,
                target_seconds=target_seconds,
                actual_audio_duration_s=actual_duration_s,
                samples=pcm,
                transcriber=transcriber,
                mt_client=mt_client,
                tts_client=tts_client,
            )
        )
    return records


def main(argv: list[str] | None = None) -> int:
    """脚本入口：生成 WAV → ASR → MT → TTS → 报告 → 可选 proof JSON。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples-per-direction", type=int, default=3, help="每方向采样数。")
    parser.add_argument("--sample-rate-hz", type=int, default=16000, help="ASR 输入采样率。")
    parser.add_argument(
        "--workdir",
        type=Path,
        default=Path("/tmp/tvi_e2e_first_segment"),
        help="生成 say WAV 的工作目录。",
    )
    parser.add_argument("--config", type=Path, default=None, help="本地 config.toml 路径。")
    parser.add_argument("--proof-json", type=Path, default=None, help="写出测量 proof JSON。")
    args = parser.parse_args(argv)

    max_per_direction = min(len(_UPLINK_SAMPLES), len(_DOWNLINK_SAMPLES))
    if args.samples_per_direction < 1 or args.samples_per_direction > max_per_direction:
        print(
            f"FAIL: --samples-per-direction 必须在 1..{max_per_direction} 之间，"
            f"当前 {args.samples_per_direction}。"
        )
        return 1

    try:
        settings = load_settings(config_path=args.config)
    except UserFacingError as error:
        print(f"FAIL: {error.what_happened} {error.next_action}")
        return 1

    records = asyncio.run(
        run_measurement(
            settings=settings,
            samples_per_direction=args.samples_per_direction,
            sample_rate_hz=args.sample_rate_hz,
            workdir=args.workdir,
        )
    )
    summaries = [
        summarize(records, direction=AudioDirection.UPLINK),
        summarize(records, direction=AudioDirection.DOWNLINK),
    ]
    print(render_report(records, summaries))
    if args.proof_json is not None:
        write_proof_json(
            args.proof_json,
            proof_payload(
                records,
                summaries,
                settings=settings,
                sample_rate_hz=args.sample_rate_hz,
            ),
        )
    return 0 if all(record.succeeded for record in records) else 1


if __name__ == "__main__":
    raise SystemExit(main())
