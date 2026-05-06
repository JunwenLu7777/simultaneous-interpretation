"""online-asr 探针脚本测试。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np

from teams_voice_interpreter.data.audio_segment import AudioDirection
from teams_voice_interpreter.data.transcript import TranscriptKind

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "probe_online_asr.py"
_SPEC = importlib.util.spec_from_file_location("probe_online_asr", _SCRIPT_PATH)
assert _SPEC is not None
assert _SPEC.loader is not None
probe = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = probe
_SPEC.loader.exec_module(probe)


def test_character_error_rate_uses_normalized_character_distance() -> None:
    """CER 按去空白后的字符距离计算。"""
    assert probe.character_error_rate("客户续费", "客户蓄费") == 0.25
    assert probe.character_error_rate("", "") == 0.0
    assert probe.character_error_rate("", "hello") == 1.0


def test_run_online_asr_probe_reports_first_stable_partial_and_cer() -> None:
    """探针应输出 stable partial、final 文本、ASR 调用次数和 CER。"""
    scripted = iter(
        [
            "我们今天讨论现金流预测",
            "我们今天讨论现金流预测方案",
            "我们今天讨论现金流预测方案和预算",
        ]
    )

    def transcribe(samples: np.ndarray) -> str:
        assert samples.size > 0
        return next(scripted)

    result = probe.run_online_asr_probe(
        np.ones(250, dtype=np.int16),
        direction=AudioDirection.UPLINK,
        transcribe=transcribe,
        sample_rate_hz=1000,
        step_ms=100,
        frame_ms=50,
        expected_text="我们今天讨论现金流预测方案和预算",
    )

    partial_chunks = [
        chunk for chunk in result.stable_chunks if chunk.kind is TranscriptKind.PARTIAL
    ]
    assert result.transcribe_calls == 3
    assert result.first_partial_s is not None
    assert result.first_ready_partial_s is not None
    assert result.first_confirmed_ready_partial_s is not None
    assert result.first_partial_s >= 0.20
    assert result.first_ready_partial_s >= 0.20
    assert result.first_confirmed_ready_partial_s >= 0.20
    assert [chunk.delta_text for chunk in partial_chunks] == ["我们今天讨论现金流预测"]
    assert result.final_text == "我们今天讨论现金流预测方案和预算"
    assert result.cer == 0.0


def test_run_online_asr_probe_counts_realtime_audio_arrival_floor() -> None:
    """探针不得把离线快速喂音频耗时误报成真实会议 partial 延迟。"""
    scripted = iter(
        [
            "我们今天讨论现金流预测",
            "我们今天讨论现金流预测方案",
            "我们今天讨论现金流预测方案",
        ]
    )

    result = probe.run_online_asr_probe(
        np.ones(250, dtype=np.int16),
        direction=AudioDirection.UPLINK,
        transcribe=lambda _samples: next(scripted),
        sample_rate_hz=1000,
        step_ms=100,
        frame_ms=50,
        expected_text="我们今天讨论现金流预测方案",
    )

    assert result.duration_s == 0.25
    assert result.first_confirmed_ready_partial_s is not None
    assert result.first_confirmed_ready_partial_s >= 0.20
    assert result.first_confirmed_ready_partial_s < result.duration_s


def test_render_report_includes_threshold_inputs() -> None:
    """报告应能直接看出 partial 数、CER 和 final 文本。"""
    result = probe.run_online_asr_probe(
        np.ones(120, dtype=np.int16),
        direction=AudioDirection.UPLINK,
        transcribe=lambda _samples: "客户续费风险缓冲",
        sample_rate_hz=1000,
        step_ms=60,
        frame_ms=30,
        expected_text="客户续费风险缓冲",
    )

    report = probe.render_report(result)

    assert "# Online ASR 探针" in report
    assert "| CER | 0.000 |" in report
    assert "首个可翻译 stable partial" in report
    assert "首个 final 确认可翻译 stable partial" in report
    assert "客户续费风险缓冲" in report


def test_threshold_failures_fail_closed_when_metrics_missing() -> None:
    """用户给了阈值但缺少对应指标时，探针必须失败而不是沉默通过。"""
    result = probe.OnlineASRProbeResult(
        duration_s=1.0,
        transcribe_calls=1,
        stable_chunks=[],
        stable_observations=[],
        final_text="客户续费风险缓冲",
        first_partial_s=None,
        first_ready_partial_s=None,
        first_confirmed_ready_partial_s=None,
        final_asr_s=0.2,
        cer=None,
    )

    failures = probe.threshold_failures(
        result,
        max_first_partial_s=1.2,
        max_cer=0.1,
    )

    assert failures == [
        "FAIL: 未产生 final 可确认的可翻译 stable partial，无法满足首个 stable partial 阈值",
        "FAIL: 使用 --max-cer 时必须同时提供 --expected-text",
    ]


def test_threshold_failures_reports_threshold_overruns() -> None:
    """超出阈值时应给出可直接定位的失败信息。"""
    result = probe.OnlineASRProbeResult(
        duration_s=1.0,
        transcribe_calls=3,
        stable_chunks=[],
        stable_observations=[],
        final_text="客户蓄费",
        first_partial_s=1.5,
        first_ready_partial_s=1.5,
        first_confirmed_ready_partial_s=1.5,
        final_asr_s=0.2,
        cer=0.25,
    )

    failures = probe.threshold_failures(
        result,
        max_first_partial_s=1.2,
        max_cer=0.1,
    )

    assert failures == [
        "FAIL: 首个 final 确认可翻译 stable partial 1.50s > 1.20s",
        "FAIL: CER 0.250 > 0.100",
    ]
