"""E2E 首段译音 replay 探针脚本测试。"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from teams_voice_interpreter.config import Settings
from teams_voice_interpreter.data.audio_segment import AudioDirection
from teams_voice_interpreter.mt.deepseek_client import TranslationChunk
from teams_voice_interpreter.tts.edge_tts_client import TTSEvent

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "measure_e2e_first_segment.py"
_SPEC = importlib.util.spec_from_file_location("measure_e2e_first_segment", _SCRIPT_PATH)
assert _SPEC is not None
assert _SPEC.loader is not None
measure = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = measure
_SPEC.loader.exec_module(measure)


class _FakeMTClient:
    """异步流式 MT fake，模拟 delta + completed。"""

    def stream_translate(
        self,
        text: str,
        *,
        direction: AudioDirection,
        context_text: str = "",
    ) -> Any:
        del text, direction, context_text
        return self._events()

    async def _events(self) -> Any:
        yield TranslationChunk(kind="delta", text="Hello")
        yield TranslationChunk(kind="delta", text=" world")
        yield TranslationChunk(kind="completed", text="")


class _FakeTTSClient:
    """异步 TTS fake，模拟首字节、音频块与 completed。"""

    def stream_synthesize(
        self,
        text: str,
        *,
        direction: AudioDirection,
        voice: str | None = None,
    ) -> Any:
        del text, direction, voice
        return self._events()

    async def _events(self) -> Any:
        yield TTSEvent(kind="first_byte", audio_chunk=b"1", audio_format="pcm_s16le_22050")
        yield TTSEvent(kind="audio_chunk", audio_chunk=b"2", audio_format="pcm_s16le_22050")
        yield TTSEvent(kind="completed", audio_format="pcm_s16le_22050")


class _FakeTranscriber:
    """同步 ASR fake，返回固定文本。"""

    def __init__(self, *, model_name: str, language: str) -> None:
        self.model_name = model_name
        self.language = language

    def transcribe(self, samples: np.ndarray) -> str:
        assert samples.size > 0
        return "下次会议三点开始"


def _sample(
    *,
    direction: AudioDirection = AudioDirection.UPLINK,
    actual_audio_duration_s: float = 2.0,
    asr_final_s: float | None = 0.3,
    mt_completed_s: float | None = 0.6,
    tts_first_byte_s: float | None = 0.1,
    mt_to_first_audio_s: float | None = None,
    error: str | None = None,
) -> Any:
    if mt_to_first_audio_s is None and mt_completed_s is not None and tts_first_byte_s is not None:
        mt_to_first_audio_s = mt_completed_s + tts_first_byte_s
    return measure.E2ESample(
        direction=direction,
        source_voice="Tingting",
        source_text="下次会议三点开始",
        target_seconds=3.0,
        actual_audio_duration_s=actual_audio_duration_s,
        asr_final_s=asr_final_s,
        asr_text="下次会议三点开始",
        mt_first_token_s=0.2,
        mt_completed_s=mt_completed_s,
        translated_text="The next meeting starts at three.",
        tts_first_byte_s=tts_first_byte_s,
        tts_completed_s=0.5,
        tts_audio_format="pcm_s16le_22050",
        mt_to_first_audio_s=mt_to_first_audio_s,
        error=error,
    )


def test_e2e_sample_calculates_processing_and_speech_start_latency() -> None:
    """E2E 样本应区分段闭合后处理耗时与音频开头代理耗时。"""
    record = _sample(actual_audio_duration_s=2.0, asr_final_s=0.3, mt_completed_s=0.6)

    assert record.post_segment_first_audio_s == pytest.approx(1.0)
    assert record.speech_start_first_audio_s == pytest.approx(3.0)


def test_summarize_reports_post_segment_and_speech_start_percentiles() -> None:
    """单方向汇总应只对成功样本计算 p50 / p95。"""
    samples = [
        _sample(actual_audio_duration_s=1.0, asr_final_s=0.1, mt_completed_s=0.2),
        _sample(actual_audio_duration_s=2.0, asr_final_s=0.2, mt_completed_s=0.3),
        _sample(actual_audio_duration_s=3.0, asr_final_s=0.3, mt_completed_s=0.4),
        _sample(error="boom"),
    ]

    summary = measure.summarize(samples, direction=AudioDirection.UPLINK)

    assert summary.success_count == 3
    assert summary.failure_count == 1
    assert summary.post_segment_p50_ms == pytest.approx(600.0)
    assert summary.post_segment_p95_ms == pytest.approx(800.0)
    assert summary.speech_start_p50_ms == 2600.0
    assert summary.speech_start_p95_ms == 3800.0


async def test_measure_mt_collects_delta_text_and_completed_time() -> None:
    """MT 测量应拼接 delta 文本，并记录 completed 耗时。"""
    result = await measure.measure_mt(
        _FakeMTClient(),
        "下次会议三点开始",
        direction=AudioDirection.UPLINK,
    )

    assert result.succeeded
    assert result.translated_text == "Hello world"
    assert result.first_token_s is not None
    assert result.completed_s is not None


async def test_measure_tts_first_byte_consumes_stream_to_completed() -> None:
    """TTS 测量应记录首字节与 completed，并保留音频格式。"""
    result = await measure.measure_tts_first_byte(
        _FakeTTSClient(),
        "Hello world",
        direction=AudioDirection.UPLINK,
    )

    assert result.succeeded
    assert result.first_byte_s is not None
    assert result.completed_s is not None
    assert result.audio_format == "pcm_s16le_22050"


async def test_measure_mt_to_early_tts_starts_tts_before_mt_completed() -> None:
    """E2E replay 应在首个 MT delta 后启动 TTS，而不是等待 completed。"""
    result = await measure.measure_mt_to_early_tts(
        _FakeMTClient(),
        _FakeTTSClient(),
        "下次会议三点开始",
        direction=AudioDirection.UPLINK,
    )

    assert result.succeeded
    assert result.mt_first_token_s is not None
    assert result.mt_completed_s is not None
    assert result.mt_to_first_audio_s is not None
    assert result.tts_first_byte_s is not None
    assert result.translated_text.startswith("Hello")


async def test_measure_e2e_sample_runs_asr_mt_then_tts() -> None:
    """单样本测量应串起 ASR、MT delta early TTS first byte。"""
    record = await measure.measure_e2e_sample(
        direction=AudioDirection.UPLINK,
        source_voice="Tingting",
        source_text="下次会议三点开始",
        target_seconds=3.0,
        actual_audio_duration_s=2.0,
        samples=np.ones(160, dtype=np.int16),
        transcriber=_FakeTranscriber(model_name="small-q5_1", language="zh"),
        mt_client=_FakeMTClient(),
        tts_client=_FakeTTSClient(),
    )

    assert record.succeeded
    assert record.asr_text == "下次会议三点开始"
    assert record.translated_text == "Hello world"
    assert record.tts_audio_format == "pcm_s16le_22050"
    assert record.post_segment_first_audio_s is not None


def test_render_report_includes_both_latency_definitions() -> None:
    """Markdown 报告应直接展示两种 E2E 口径。"""
    samples = [_sample()]
    summaries = [measure.summarize(samples, direction=AudioDirection.UPLINK)]

    report = measure.render_report(samples, summaries)

    assert "# E2E 首段译音无人值守 replay" in report
    assert "段闭合后 p50" in report
    assert "从音频开头 p50" in report
    assert "下次会议三点开始" in report


def test_proof_payload_records_pipeline_order_and_samples() -> None:
    """proof JSON 应包含当前生产顺序定义和样本细节。"""
    samples = [_sample()]
    summaries = [measure.summarize(samples, direction=AudioDirection.UPLINK)]
    payload = measure.proof_payload(
        samples,
        summaries,
        settings=Settings(deepseek_api_key="fake"),
        sample_rate_hz=16000,
    )

    assert payload["schema_version"] == 1
    definitions = payload["definitions"]
    assert definitions["current_pipeline_order"] == "ASR final -> MT delta early TTS first byte"
    assert payload["settings"]["tts_prewarm_mode"] == "none"
    written_samples = payload["samples"]
    assert written_samples[0]["post_segment_first_audio_s"] == pytest.approx(1.0)


async def test_run_measurement_prewarm_tts_when_requested(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    """显式 prewarm replay 口径必须随 MT 测量并发预热对应 voice。"""
    prewarmed: list[AudioDirection] = []

    def fake_synthesize(
        *,
        voice: str,
        text: str,
        output_path: Path,
        target_rate_hz: int,
    ) -> None:
        del voice, text, target_rate_hz
        output_path.write_bytes(b"fake-wav")

    def fake_load(path: Path, *, target_rate_hz: int) -> tuple[np.ndarray, float]:
        del path, target_rate_hz
        return np.ones(160, dtype=np.int16), 2.0

    monkeypatch.setattr(measure, "synthesize_say_wav", fake_synthesize)
    monkeypatch.setattr(measure, "load_wav_mono_int16", fake_load)
    monkeypatch.setattr(measure, "WhisperOneShotTranscriber", _FakeTranscriber)
    monkeypatch.setattr(measure, "DeepSeekStreamingClient", lambda **kwargs: _FakeMTClient())
    monkeypatch.setattr(measure, "build_tts_client", lambda settings: _FakeTTSClient())
    monkeypatch.setattr(
        measure,
        "prewarm_tts_client",
        lambda settings, *, direction: prewarmed.append(direction),
    )

    records = await measure.run_measurement(
        settings=Settings(deepseek_api_key="fake"),
        samples_per_direction=1,
        sample_rate_hz=16000,
        workdir=tmp_path / "wav",
        prewarm_tts=True,
    )

    assert prewarmed == [AudioDirection.UPLINK, AudioDirection.DOWNLINK]
    assert len(records) == 2
    payload = measure.proof_payload(
        records,
        [measure.summarize(records, direction=AudioDirection.UPLINK)],
        settings=Settings(deepseek_api_key="fake"),
        sample_rate_hz=16000,
        prewarm_tts=True,
    )
    assert payload["settings"]["tts_prewarm_mode"] == "mt_concurrent_voice_prewarm"


def test_write_proof_json_round_trip(tmp_path: Path) -> None:
    """proof JSON 应能读回并保留 summary 指标。"""
    proof_path = tmp_path / "e2e.json"
    samples = [_sample()]
    summaries = [measure.summarize(samples, direction=AudioDirection.UPLINK)]
    payload = measure.proof_payload(
        samples,
        summaries,
        settings=Settings(deepseek_api_key="fake"),
        sample_rate_hz=16000,
    )

    measure.write_proof_json(proof_path, payload)

    written = json.loads(proof_path.read_text(encoding="utf-8"))
    assert written["generated_by"] == "scripts/measure_e2e_first_segment.py"
    assert written["summaries"][0]["direction"] == "uplink"


def test_main_writes_proof_json_with_synthetic_clients(
    monkeypatch: Any,
    tmp_path: Path,
    capsys: Any,
) -> None:
    """主入口应生成 6 条样本、打印报告并写出 proof JSON。"""
    proof_path = tmp_path / "e2e.json"
    workdir = tmp_path / "wav"

    def fake_synthesize(
        *,
        voice: str,
        text: str,
        output_path: Path,
        target_rate_hz: int,
    ) -> None:
        del voice, text, target_rate_hz
        output_path.write_bytes(b"fake-wav")

    def fake_load(path: Path, *, target_rate_hz: int) -> tuple[np.ndarray, float]:
        del path, target_rate_hz
        return np.ones(160, dtype=np.int16), 2.0

    class FakeSettings(Settings):
        def resolved_deepseek_api_key(self) -> str:
            return "fake-key"

    monkeypatch.setattr(measure, "synthesize_say_wav", fake_synthesize)
    monkeypatch.setattr(measure, "load_wav_mono_int16", fake_load)
    monkeypatch.setattr(measure, "WhisperOneShotTranscriber", _FakeTranscriber)
    monkeypatch.setattr(measure, "DeepSeekStreamingClient", lambda **kwargs: _FakeMTClient())
    monkeypatch.setattr(measure, "build_tts_client", lambda settings: _FakeTTSClient())
    monkeypatch.setattr(measure, "load_settings", lambda config_path=None: FakeSettings())

    exit_code = measure.main(
        [
            "--workdir",
            str(workdir),
            "--proof-json",
            str(proof_path),
        ]
    )

    output = capsys.readouterr().out
    payload = json.loads(proof_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert "# E2E 首段译音无人值守 replay" in output
    assert len(payload["samples"]) == 6
