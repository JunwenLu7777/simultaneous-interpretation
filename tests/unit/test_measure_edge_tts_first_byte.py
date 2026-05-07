"""Edge-TTS first byte 探针脚本测试。"""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from teams_voice_interpreter.data.audio_segment import AudioDirection
from teams_voice_interpreter.errors import EdgeTTSError
from teams_voice_interpreter.tts.edge_tts_client import DEFAULT_VOICES, TTSEvent

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "measure_edge_tts_first_byte.py"
_SPEC = importlib.util.spec_from_file_location("measure_edge_tts_first_byte", _SCRIPT_PATH)
assert _SPEC is not None
assert _SPEC.loader is not None
measure = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = measure
_SPEC.loader.exec_module(measure)


class _FakeStreamingClient:
    """按 first_byte → audio_chunk → completed 顺序产出事件的本地客户端。"""

    def stream_synthesize(
        self,
        text: str,
        *,
        direction: AudioDirection,
        voice: str | None = None,
    ) -> AsyncIterator[TTSEvent]:
        del text, direction, voice
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[TTSEvent]:
        yield TTSEvent(kind="first_byte", audio_chunk=b"pcm0", latency_ms=120)
        yield TTSEvent(kind="audio_chunk", audio_chunk=b"pcm1")
        yield TTSEvent(kind="completed", latency_ms=300)


class _RaisingStreamingClient:
    """每次调用都抛 EdgeTTSError，模拟首字节超时 / 鉴权失败。"""

    def stream_synthesize(
        self,
        text: str,
        *,
        direction: AudioDirection,
        voice: str | None = None,
    ) -> AsyncIterator[TTSEvent]:
        del text, direction, voice
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[TTSEvent]:
        raise EdgeTTSError(
            code="tts.first_byte_timeout",
            what_happened="发生了什么：Edge-TTS 在 8 秒内没有返回首个音频片段。",
            next_action="下一步如何做：请重试一次。",
        )
        yield  # pragma: no cover - 让函数仍是 async generator


@pytest.mark.asyncio
async def test_measure_first_byte_records_first_event_time() -> None:
    """成功路径必须记录首字节耗时与 completed 耗时。"""
    sample = await measure.measure_first_byte(
        _FakeStreamingClient(),
        "Hello.",
        direction=AudioDirection.UPLINK,
        voice="en-US-AriaNeural",
    )

    assert sample.succeeded
    assert sample.first_byte_s is not None
    assert sample.completed_s is not None
    assert sample.first_byte_s <= sample.completed_s
    assert sample.error is None


@pytest.mark.asyncio
async def test_measure_first_byte_captures_two_part_error() -> None:
    """EdgeTTSError 必须被记录为带「what + next」的 error 字符串。"""
    sample = await measure.measure_first_byte(
        _RaisingStreamingClient(),
        "你好",
        direction=AudioDirection.DOWNLINK,
        voice="zh-CN-XiaoxiaoNeural",
    )

    assert not sample.succeeded
    assert sample.first_byte_s is None
    assert sample.completed_s is None
    assert sample.error is not None
    assert "Edge-TTS 在 8 秒内" in sample.error
    assert "请重试" in sample.error


def test_summarize_returns_p50_p95_avg_max_for_successful_samples() -> None:
    """单方向汇总应当只对成功样本算 p50 / p95 / avg / max。"""
    samples = [
        measure.FirstByteSample(AudioDirection.UPLINK, "en-US-AriaNeural", "a", 0.10, 0.50),
        measure.FirstByteSample(AudioDirection.UPLINK, "en-US-AriaNeural", "b", 0.20, 0.60),
        measure.FirstByteSample(AudioDirection.UPLINK, "en-US-AriaNeural", "c", 0.30, 0.70),
        measure.FirstByteSample(AudioDirection.UPLINK, "en-US-AriaNeural", "d", 0.40, 0.80),
        measure.FirstByteSample(
            AudioDirection.UPLINK, "en-US-AriaNeural", "fail", None, None, error="boom"
        ),
        measure.FirstByteSample(
            AudioDirection.DOWNLINK, "zh-CN-XiaoxiaoNeural", "x", 0.50, 0.90
        ),
    ]

    summary = measure.summarize(samples, direction=AudioDirection.UPLINK)

    assert summary.success_count == 4
    assert summary.failure_count == 1
    assert summary.voice == "en-US-AriaNeural"
    assert summary.p50_ms == 200.0
    assert summary.p95_ms == 400.0
    assert summary.avg_ms == 250.0
    assert summary.max_ms == 400.0


def test_summarize_handles_all_failures() -> None:
    """全部失败时 percentile 字段必须置 None，而不是 0。"""
    samples = [
        measure.FirstByteSample(
            AudioDirection.UPLINK, "en-US-AriaNeural", "a", None, None, error="boom"
        ),
        measure.FirstByteSample(
            AudioDirection.UPLINK, "en-US-AriaNeural", "b", None, None, error="boom"
        ),
    ]

    summary = measure.summarize(samples, direction=AudioDirection.UPLINK)

    assert summary.success_count == 0
    assert summary.failure_count == 2
    assert summary.p50_ms is None
    assert summary.p95_ms is None
    assert summary.avg_ms is None
    assert summary.max_ms is None


def test_summarize_falls_back_to_default_voice_when_no_samples() -> None:
    """空样本列表时应用 DEFAULT_VOICES 兜底而不是抛索引错。"""
    summary = measure.summarize([], direction=AudioDirection.UPLINK)

    assert summary.voice == DEFAULT_VOICES[AudioDirection.UPLINK]
    assert summary.success_count == 0
    assert summary.failure_count == 0


def test_render_report_contains_summary_and_sample_tables() -> None:
    """报告必须同时包含分方向汇总表和样本明细表。"""
    samples = [
        measure.FirstByteSample(
            AudioDirection.UPLINK, "en-US-AriaNeural", "Hello.", 0.20, 0.30
        ),
        measure.FirstByteSample(
            AudioDirection.DOWNLINK, "zh-CN-XiaoxiaoNeural", "你好", 0.18, 0.28
        ),
    ]
    summaries = [
        measure.summarize(samples, direction=AudioDirection.UPLINK),
        measure.summarize(samples, direction=AudioDirection.DOWNLINK),
    ]

    report = measure.render_report(samples, summaries)

    assert "# Edge-TTS first byte 首字节延迟" in report
    assert "## 分方向汇总" in report
    assert "## 样本明细" in report
    assert "| uplink |" in report
    assert "| downlink |" in report
    assert "Hello." in report
    assert "你好" in report
    assert "`en-US-AriaNeural`" in report
    assert "`zh-CN-XiaoxiaoNeural`" in report


def test_proof_payload_serializes_summaries_and_samples() -> None:
    """proof payload 应同时序列化汇总和原始样本。"""
    samples = [
        measure.FirstByteSample(
            AudioDirection.UPLINK, "en-US-AriaNeural", "Hello.", 0.20, 0.30
        ),
        measure.FirstByteSample(
            AudioDirection.UPLINK, "en-US-AriaNeural", "fail", None, None, error="boom"
        ),
    ]
    summaries = [measure.summarize(samples, direction=AudioDirection.UPLINK)]

    payload = measure.proof_payload(samples, summaries)

    assert payload["schema_version"] == 1
    summary_list = payload["summaries"]
    sample_list = payload["samples"]
    assert isinstance(summary_list, list) and isinstance(sample_list, list)
    assert summary_list[0]["voice"] == "en-US-AriaNeural"
    assert summary_list[0]["success_count"] == 1
    assert summary_list[0]["failure_count"] == 1
    assert sample_list[0]["text"] == "Hello."
    assert sample_list[1]["error"] == "boom"


def test_write_proof_json_round_trip(tmp_path: Path) -> None:
    """proof JSON 写出再读回应保留方向、音色与原文。"""
    proof_path = tmp_path / "edge-tts-first-byte.json"
    samples = [
        measure.FirstByteSample(
            AudioDirection.DOWNLINK, "zh-CN-XiaoxiaoNeural", "你好", 0.18, 0.28
        )
    ]
    payload = measure.proof_payload(
        samples,
        [measure.summarize(samples, direction=AudioDirection.DOWNLINK)],
    )

    measure.write_proof_json(proof_path, payload)
    written = json.loads(proof_path.read_text(encoding="utf-8"))

    assert written["samples"][0]["direction"] == "downlink"
    assert written["samples"][0]["voice"] == "zh-CN-XiaoxiaoNeural"
    assert written["samples"][0]["text"] == "你好"
    assert written["samples"][0]["first_byte_s"] == 0.18


def test_direction_plan_balances_uplink_and_downlink() -> None:
    """计划必须在两个方向上各采 N 条，且每方向都有不同长度的样本。"""
    plan = measure._direction_plan(samples_per_direction=5)

    uplink_count = sum(
        1 for direction, _voice, _text in plan if direction is AudioDirection.UPLINK
    )
    downlink_count = sum(
        1 for direction, _voice, _text in plan if direction is AudioDirection.DOWNLINK
    )
    uplink_voices = {voice for direction, voice, _text in plan if direction is AudioDirection.UPLINK}
    downlink_voices = {
        voice for direction, voice, _text in plan if direction is AudioDirection.DOWNLINK
    }

    assert uplink_count == 5
    assert downlink_count == 5
    assert uplink_voices == {DEFAULT_VOICES[AudioDirection.UPLINK]}
    assert downlink_voices == {DEFAULT_VOICES[AudioDirection.DOWNLINK]}


def test_main_rejects_invalid_samples_count(
    capsys: Any,
) -> None:
    """超出样本池容量必须 fail-closed。"""
    exit_code = measure.main(["--samples-per-direction", "999"])

    assert exit_code == 1
    assert "FAIL" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_run_measurement_iterates_full_plan() -> None:
    """run_measurement 必须按计划顺序记录所有样本。"""
    plan = [
        (AudioDirection.UPLINK, "en-US-AriaNeural", "Hello."),
        (AudioDirection.DOWNLINK, "zh-CN-XiaoxiaoNeural", "你好"),
    ]

    samples = await measure.run_measurement(_FakeStreamingClient(), plan)

    assert [sample.direction for sample in samples] == [
        AudioDirection.UPLINK,
        AudioDirection.DOWNLINK,
    ]
    assert [sample.text for sample in samples] == ["Hello.", "你好"]
    assert all(sample.succeeded for sample in samples)
