"""ASR 整段耗时探针脚本测试。"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

from teams_voice_interpreter.data.audio_segment import AudioDirection

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "measure_asr_segment_latency.py"
_SPEC = importlib.util.spec_from_file_location("measure_asr_segment_latency", _SCRIPT_PATH)
assert _SPEC is not None
assert _SPEC.loader is not None
measure = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = measure
_SPEC.loader.exec_module(measure)


def _measurement(
    *,
    direction: AudioDirection = AudioDirection.UPLINK,
    voice: str = "Tingting",
    text: str = "下次会议三点开始",
    target_seconds: float = 3.0,
    actual_duration_s: float = 2.45,
    final_asr_s: float = 0.18,
    final_text: str = "下次会议三点开始。",
) -> Any:
    return measure.SegmentMeasurement(
        direction=direction,
        voice=voice,
        text=text,
        target_seconds=target_seconds,
        actual_duration_s=actual_duration_s,
        final_asr_s=final_asr_s,
        final_text=final_text,
    )


def test_segment_measurement_ratio_is_asr_over_duration() -> None:
    """ASR 占比应当是 final_asr_s / actual_duration_s。"""
    record = _measurement(actual_duration_s=2.0, final_asr_s=0.4)
    assert record.asr_to_duration_ratio == 0.2


def test_segment_measurement_ratio_handles_zero_duration() -> None:
    """实测时长为 0 时占比应当为 0，避免除零异常。"""
    record = _measurement(actual_duration_s=0.0, final_asr_s=0.5)
    assert record.asr_to_duration_ratio == 0.0


def test_render_report_includes_direction_voice_and_text() -> None:
    """报告应当能直接看出方向、音色、段长比和原文。"""
    report = measure.render_report(
        [
            _measurement(
                direction=AudioDirection.UPLINK,
                voice="Tingting",
                text="下次会议三点开始",
                actual_duration_s=2.40,
                final_asr_s=0.30,
            ),
            _measurement(
                direction=AudioDirection.DOWNLINK,
                voice="Samantha",
                text="Let's start the meeting at three.",
                target_seconds=3.0,
                actual_duration_s=1.95,
                final_asr_s=0.22,
            ),
        ]
    )

    assert "# ASR 整段耗时 vs 段长" in report
    assert "uplink" in report and "downlink" in report
    assert "`Tingting`" in report and "`Samantha`" in report
    assert "下次会议三点开始" in report
    assert "Let's start the meeting at three." in report
    assert "12.50%" in report
    assert "11.28%" in report


def test_proof_payload_records_full_measurement_schema() -> None:
    """proof payload 应包含 schema 版本、模型与每段完整指标。"""
    payload = measure.proof_payload(
        [_measurement(actual_duration_s=2.0, final_asr_s=0.3)],
        model_name="small-q5_1",
        sample_rate_hz=16000,
    )

    assert payload["schema_version"] == 1
    assert payload["model"] == "small-q5_1"
    assert payload["sample_rate_hz"] == 16000
    measurements = payload["measurements"]
    assert isinstance(measurements, list)
    assert len(measurements) == 1
    record = measurements[0]
    assert record["direction"] == "uplink"
    assert record["voice"] == "Tingting"
    assert record["target_seconds"] == 3.0
    assert record["actual_duration_s"] == 2.0
    assert record["final_asr_s"] == 0.3
    assert record["asr_to_duration_ratio"] == 0.15


def test_write_proof_json_round_trip(tmp_path: Path) -> None:
    """proof JSON 应当可以读回并保留方向、音色与原文。"""
    proof_path = tmp_path / "asr-segment-latency.json"
    payload = measure.proof_payload(
        [
            _measurement(
                direction=AudioDirection.DOWNLINK,
                voice="Samantha",
                text="Let's start the meeting at three.",
                target_seconds=3.0,
                actual_duration_s=1.95,
                final_asr_s=0.22,
                final_text="Let's start the meeting at three.",
            )
        ],
        model_name="small-q5_1",
        sample_rate_hz=16000,
    )

    measure.write_proof_json(proof_path, payload)

    written = json.loads(proof_path.read_text(encoding="utf-8"))
    assert written["measurements"][0]["direction"] == "downlink"
    assert written["measurements"][0]["voice"] == "Samantha"
    assert written["measurements"][0]["text"] == "Let's start the meeting at three."


def test_planned_samples_covers_uplink_and_downlink() -> None:
    """计划样本应覆盖上行 zh 与下行 en 各 3 档。"""
    plan = list(measure._planned_samples())
    directions = [item[0] for item in plan]
    languages = [item[3] for item in plan]
    targets = [item[4] for item in plan]

    assert directions.count(AudioDirection.UPLINK) == 3
    assert directions.count(AudioDirection.DOWNLINK) == 3
    assert languages.count("zh") == 3
    assert languages.count("en") == 3
    assert sorted({float(target) for target in targets}) == [3.0, 6.0, 10.0]


def test_main_writes_proof_json_with_synthetic_transcriber(
    monkeypatch: Any,
    tmp_path: Path,
    capsys: Any,
) -> None:
    """走主入口时应当合成 6 段、写出 proof JSON、打印 markdown 报告。"""
    proof_path = tmp_path / "asr-segment-latency.json"
    workdir = tmp_path / "wav"

    def fake_synthesize(
        *,
        voice: str,
        text: str,
        output_path: Path,
        target_rate_hz: int,
    ) -> None:
        del voice, text, target_rate_hz
        output_path.write_bytes(b"fake-wav-bytes")

    def fake_load(
        path: Path,
        *,
        target_rate_hz: int,
    ) -> tuple[np.ndarray, float]:
        del path, target_rate_hz
        return np.ones(160, dtype=np.int16), 2.0

    class FakeTranscriber:
        def __init__(self, *, model_name: str, language: str) -> None:
            self.model_name = model_name
            self.language = language

        def transcribe(self, samples: np.ndarray) -> str:
            assert samples.size == 160
            return "fake transcript"

    monkeypatch.setattr(measure, "synthesize_say_wav", fake_synthesize)
    monkeypatch.setattr(measure, "load_wav_mono_int16", fake_load)
    monkeypatch.setattr(measure, "WhisperOneShotTranscriber", FakeTranscriber)

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
    assert "# ASR 整段耗时 vs 段长" in output
    assert payload["model"] == "small-q5_1"
    assert len(payload["measurements"]) == 6
    assert all(record["final_text"] == "fake transcript" for record in payload["measurements"])
