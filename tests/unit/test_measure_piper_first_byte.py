"""Piper TTS first byte 探针脚本测试。"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from teams_voice_interpreter.data.audio_segment import AudioDirection

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "measure_piper_first_byte.py"
_SPEC = importlib.util.spec_from_file_location("measure_piper_first_byte", _SCRIPT_PATH)
assert _SPEC is not None
assert _SPEC.loader is not None
measure = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = measure
_SPEC.loader.exec_module(measure)


class _FakePiperVoice:
    """同步 generator，模拟 PiperVoice.synthesize 输出 AudioChunk。"""

    def __init__(self, *, chunks: int = 3) -> None:
        self._chunks = chunks

    def synthesize(self, text: str) -> Any:
        del text
        for index in range(self._chunks):
            yield {"id": index}


class _RaisingPiperVoice:
    """模拟 ONNX / IO 失败。"""

    def synthesize(self, text: str) -> Any:
        del text
        raise RuntimeError("piper boom")
        yield  # pragma: no cover


class _EmptyPiperVoice:
    """模拟未返回任何 audio chunk。"""

    def synthesize(self, text: str) -> Any:
        del text
        return iter(())


def test_measure_first_byte_records_first_chunk_time() -> None:
    """成功路径必须记录首字节耗时与 completed 耗时。"""
    sample = measure.measure_first_byte(
        _FakePiperVoice(chunks=3),
        "Hello.",
        direction=AudioDirection.UPLINK,
        voice_name="en_US-amy-medium",
    )

    assert sample.succeeded
    assert sample.first_byte_s is not None
    assert sample.completed_s is not None
    assert sample.first_byte_s <= sample.completed_s


def test_measure_first_byte_captures_runtime_error() -> None:
    """RuntimeError 必须被记录为 error 字符串。"""
    sample = measure.measure_first_byte(
        _RaisingPiperVoice(),
        "你好",
        direction=AudioDirection.DOWNLINK,
        voice_name="zh_CN-huayan-medium",
    )

    assert not sample.succeeded
    assert sample.first_byte_s is None
    assert sample.error is not None
    assert "piper boom" in sample.error


def test_measure_first_byte_handles_empty_output() -> None:
    """Piper 没产出 chunk 时必须 fail-closed。"""
    sample = measure.measure_first_byte(
        _EmptyPiperVoice(),
        "Hello.",
        direction=AudioDirection.UPLINK,
        voice_name="en_US-amy-medium",
    )

    assert not sample.succeeded
    assert sample.first_byte_s is None
    assert sample.error is not None
    assert "未返回" in sample.error


def test_summarize_returns_p50_p95_avg_max() -> None:
    """单方向汇总应当只对成功样本算 p50 / p95 / avg / max。"""
    samples = [
        measure.FirstByteSample(AudioDirection.UPLINK, "en_US-amy-medium", "a", 0.10, 0.50),
        measure.FirstByteSample(AudioDirection.UPLINK, "en_US-amy-medium", "b", 0.20, 0.60),
        measure.FirstByteSample(AudioDirection.UPLINK, "en_US-amy-medium", "c", 0.30, 0.70),
        measure.FirstByteSample(AudioDirection.UPLINK, "en_US-amy-medium", "d", 0.40, 0.80),
        measure.FirstByteSample(
            AudioDirection.UPLINK, "en_US-amy-medium", "fail", None, None, error="boom"
        ),
    ]

    summary = measure.summarize(samples, direction=AudioDirection.UPLINK)

    assert summary.success_count == 4
    assert summary.failure_count == 1
    assert summary.voice == "en_US-amy-medium"
    assert summary.p50_ms == 200.0
    assert summary.p95_ms == 400.0
    assert summary.avg_ms == 250.0


def test_summarize_handles_all_failures() -> None:
    """全部失败时 percentile 字段必须置 None。"""
    samples = [
        measure.FirstByteSample(
            AudioDirection.UPLINK, "en_US-amy-medium", "a", None, None, error="boom"
        ),
    ]

    summary = measure.summarize(samples, direction=AudioDirection.UPLINK)

    assert summary.success_count == 0
    assert summary.failure_count == 1
    assert summary.p50_ms is None


def test_render_report_contains_summary_and_sample_tables() -> None:
    """报告同时包含分方向汇总表和样本明细表。"""
    samples = [
        measure.FirstByteSample(
            AudioDirection.UPLINK, "en_US-amy-medium", "Hello.", 0.10, 0.30
        ),
        measure.FirstByteSample(
            AudioDirection.DOWNLINK, "zh_CN-huayan-medium", "你好", 0.11, 0.32
        ),
    ]
    summaries = [
        measure.summarize(samples, direction=AudioDirection.UPLINK),
        measure.summarize(samples, direction=AudioDirection.DOWNLINK),
    ]

    report = measure.render_report(samples, summaries)

    assert "# Piper TTS first byte 首字节延迟" in report
    assert "## 分方向汇总" in report
    assert "## 样本明细" in report
    assert "`en_US-amy-medium`" in report
    assert "`zh_CN-huayan-medium`" in report


def test_proof_payload_records_engine_and_models_dir() -> None:
    """proof payload 必须含 engine='piper' 与模型目录元数据。"""
    samples = [
        measure.FirstByteSample(
            AudioDirection.UPLINK, "en_US-amy-medium", "Hello.", 0.10, 0.30
        )
    ]
    summaries = [measure.summarize(samples, direction=AudioDirection.UPLINK)]

    payload = measure.proof_payload(
        samples,
        summaries,
        models_dir=Path("/tmp/piper-models"),
    )

    assert payload["schema_version"] == 1
    assert payload["engine"] == "piper"
    assert payload["models_dir"] == "/tmp/piper-models"


def test_write_proof_json_round_trip(tmp_path: Path) -> None:
    """proof JSON 写出再读回应保留 voice 与文本。"""
    proof_path = tmp_path / "piper-first-byte.json"
    samples = [
        measure.FirstByteSample(
            AudioDirection.DOWNLINK, "zh_CN-huayan-medium", "你好", 0.11, 0.32
        )
    ]
    payload = measure.proof_payload(
        samples,
        [measure.summarize(samples, direction=AudioDirection.DOWNLINK)],
        models_dir=Path("/tmp/piper-models"),
    )

    measure.write_proof_json(proof_path, payload)
    written = json.loads(proof_path.read_text(encoding="utf-8"))

    assert written["samples"][0]["voice"] == "zh_CN-huayan-medium"
    assert written["samples"][0]["text"] == "你好"


def test_direction_plan_balances_uplink_and_downlink() -> None:
    """计划必须在两个方向上各采 N 条，使用对应的 default voice。"""
    plan = measure._direction_plan(samples_per_direction=5)

    uplink_voices = {voice for direction, voice, _text in plan if direction is AudioDirection.UPLINK}
    downlink_voices = {
        voice for direction, voice, _text in plan if direction is AudioDirection.DOWNLINK
    }

    assert uplink_voices == {"en_US-amy-medium"}
    assert downlink_voices == {"zh_CN-huayan-medium"}
    assert (
        sum(1 for direction, _v, _t in plan if direction is AudioDirection.UPLINK) == 5
    )
    assert (
        sum(1 for direction, _v, _t in plan if direction is AudioDirection.DOWNLINK) == 5
    )


def test_load_voice_raises_with_pointer_when_models_missing(tmp_path: Path) -> None:
    """模型缺失时必须给出明确指引（含模型目录路径）。"""
    with pytest.raises(FileNotFoundError) as excinfo:
        measure.load_voice(tmp_path, "missing-voice")

    assert "missing-voice" in str(excinfo.value)
    assert str(tmp_path) in str(excinfo.value)


def test_main_rejects_invalid_samples_count(
    capsys: Any,
) -> None:
    """超出样本池容量必须 fail-closed。"""
    exit_code = measure.main(["--samples-per-direction", "999"])

    assert exit_code == 1
    assert "FAIL" in capsys.readouterr().out


def test_main_reports_missing_models(
    monkeypatch: Any,
    tmp_path: Path,
    capsys: Any,
) -> None:
    """模型缺失时必须 fail-closed 不进入测量阶段。"""
    exit_code = measure.main(
        ["--samples-per-direction", "1", "--models-dir", str(tmp_path)]
    )

    assert exit_code == 1
    output = capsys.readouterr().out
    assert "缺少 Piper 模型" in output


def test_run_measurement_iterates_full_plan() -> None:
    """run_measurement 必须按计划顺序记录所有样本。"""
    plan = [
        (AudioDirection.UPLINK, "en_US-amy-medium", "Hello."),
        (AudioDirection.DOWNLINK, "zh_CN-huayan-medium", "你好"),
    ]
    voices = {
        "en_US-amy-medium": _FakePiperVoice(),
        "zh_CN-huayan-medium": _FakePiperVoice(),
    }

    samples = measure.run_measurement(voices, plan)

    assert [sample.direction for sample in samples] == [
        AudioDirection.UPLINK,
        AudioDirection.DOWNLINK,
    ]
    assert all(sample.succeeded for sample in samples)
