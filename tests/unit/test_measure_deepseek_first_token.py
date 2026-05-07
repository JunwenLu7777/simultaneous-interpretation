"""DeepSeek first token 探针脚本测试。"""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from teams_voice_interpreter.data.audio_segment import AudioDirection
from teams_voice_interpreter.errors import DeepSeekError
from teams_voice_interpreter.mt.deepseek_client import TranslationChunk

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "measure_deepseek_first_token.py"
_SPEC = importlib.util.spec_from_file_location("measure_deepseek_first_token", _SCRIPT_PATH)
assert _SPEC is not None
assert _SPEC.loader is not None
measure = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = measure
_SPEC.loader.exec_module(measure)


class _FakeStreamingClient:
    """按预设响应顺序产出 delta / completed 流的本地客户端。"""

    def __init__(self, response_text: str = "fake translation") -> None:
        self._response_text = response_text

    def stream_translate(
        self,
        text: str,
        *,
        direction: AudioDirection,
        context_text: str = "",
    ) -> AsyncIterator[TranslationChunk]:
        return self._iterate(text)

    async def _iterate(self, _text: str) -> AsyncIterator[TranslationChunk]:
        yield TranslationChunk(kind="delta", text=self._response_text)
        yield TranslationChunk(kind="completed", text=self._response_text)


class _RaisingStreamingClient:
    """每次调用都抛 DeepSeekError，模拟网络 / API 故障。"""

    def stream_translate(
        self,
        text: str,
        *,
        direction: AudioDirection,
        context_text: str = "",
    ) -> AsyncIterator[TranslationChunk]:
        return self._iterate(text)

    async def _iterate(self, _text: str) -> AsyncIterator[TranslationChunk]:
        raise DeepSeekError(
            code="mt.network_error",
            what_happened="发生了什么：网络错误。",
            next_action="下一步如何做：请重试。",
        )
        yield  # pragma: no cover - 让函数仍是 async generator


@pytest.mark.asyncio
async def test_measure_first_token_records_first_delta_time() -> None:
    """成功路径必须记录首字节耗时与 completed 耗时。"""
    sample = await measure.measure_first_token(
        _FakeStreamingClient(),
        "hello",
        direction=AudioDirection.UPLINK,
    )

    assert sample.succeeded
    assert sample.first_token_s is not None
    assert sample.completed_s is not None
    assert sample.first_token_s <= sample.completed_s
    assert sample.error is None


@pytest.mark.asyncio
async def test_measure_first_token_captures_two_part_error() -> None:
    """UserFacingError 必须被记录为带「what + next」的 error 字符串。"""
    sample = await measure.measure_first_token(
        _RaisingStreamingClient(),
        "hello",
        direction=AudioDirection.DOWNLINK,
    )

    assert not sample.succeeded
    assert sample.first_token_s is None
    assert sample.completed_s is None
    assert sample.error is not None
    assert "网络错误" in sample.error
    assert "请重试" in sample.error


def test_summarize_returns_p50_p95_avg_max_for_successful_samples() -> None:
    """单方向汇总应当只对成功样本算 p50 / p95 / avg / max。"""
    samples = [
        measure.FirstTokenSample(AudioDirection.UPLINK, "a", 0.10, 0.50),
        measure.FirstTokenSample(AudioDirection.UPLINK, "b", 0.20, 0.60),
        measure.FirstTokenSample(AudioDirection.UPLINK, "c", 0.30, 0.70),
        measure.FirstTokenSample(AudioDirection.UPLINK, "d", 0.40, 0.80),
        measure.FirstTokenSample(
            AudioDirection.UPLINK, "fail", None, None, error="boom"
        ),
        measure.FirstTokenSample(AudioDirection.DOWNLINK, "x", 0.50, 0.90),
    ]

    summary = measure.summarize(samples, direction=AudioDirection.UPLINK)

    assert summary.success_count == 4
    assert summary.failure_count == 1
    assert summary.p50_ms == 200.0
    assert summary.p95_ms == 400.0
    assert summary.avg_ms == 250.0
    assert summary.max_ms == 400.0


def test_summarize_handles_all_failures() -> None:
    """全部失败时 percentile 字段必须置 None，而不是 0。"""
    samples = [
        measure.FirstTokenSample(AudioDirection.UPLINK, "a", None, None, error="boom"),
        measure.FirstTokenSample(AudioDirection.UPLINK, "b", None, None, error="boom"),
    ]

    summary = measure.summarize(samples, direction=AudioDirection.UPLINK)

    assert summary.success_count == 0
    assert summary.failure_count == 2
    assert summary.p50_ms is None
    assert summary.p95_ms is None
    assert summary.avg_ms is None
    assert summary.max_ms is None


def test_render_report_contains_summary_and_sample_tables() -> None:
    """报告必须同时包含分方向汇总表和样本明细表。"""
    samples = [
        measure.FirstTokenSample(AudioDirection.UPLINK, "你好", 0.20, 0.30),
        measure.FirstTokenSample(AudioDirection.DOWNLINK, "Hello.", 0.18, 0.28),
    ]
    summaries = [
        measure.summarize(samples, direction=AudioDirection.UPLINK),
        measure.summarize(samples, direction=AudioDirection.DOWNLINK),
    ]

    report = measure.render_report(samples, summaries)

    assert "# DeepSeek MT first token 首字节延迟" in report
    assert "## 分方向汇总" in report
    assert "## 样本明细" in report
    assert "| uplink |" in report
    assert "| downlink |" in report
    assert "你好" in report
    assert "Hello." in report


def test_proof_payload_serializes_summaries_and_samples() -> None:
    """proof payload 应同时序列化汇总和原始样本。"""
    samples = [
        measure.FirstTokenSample(AudioDirection.UPLINK, "你好", 0.10, 0.20),
        measure.FirstTokenSample(AudioDirection.UPLINK, "fail", None, None, error="boom"),
    ]
    summaries = [measure.summarize(samples, direction=AudioDirection.UPLINK)]

    payload = measure.proof_payload(
        samples,
        summaries,
        model="deepseek-v4-flash",
        base_url="https://api.deepseek.com",
    )

    assert payload["schema_version"] == 1
    assert payload["model"] == "deepseek-v4-flash"
    assert payload["base_url"] == "https://api.deepseek.com"
    summary_list = payload["summaries"]
    sample_list = payload["samples"]
    assert isinstance(summary_list, list) and isinstance(sample_list, list)
    assert summary_list[0]["success_count"] == 1
    assert summary_list[0]["failure_count"] == 1
    assert sample_list[0]["text"] == "你好"
    assert sample_list[1]["error"] == "boom"


def test_write_proof_json_round_trip(tmp_path: Path) -> None:
    """proof JSON 写出再读回应保留完整内容。"""
    proof_path = tmp_path / "deepseek-first-token.json"
    samples = [measure.FirstTokenSample(AudioDirection.DOWNLINK, "Hello.", 0.18, 0.28)]
    payload = measure.proof_payload(
        samples,
        [measure.summarize(samples, direction=AudioDirection.DOWNLINK)],
        model="deepseek-v4-flash",
        base_url="https://api.deepseek.com",
    )

    measure.write_proof_json(proof_path, payload)
    written = json.loads(proof_path.read_text(encoding="utf-8"))

    assert written["samples"][0]["direction"] == "downlink"
    assert written["samples"][0]["text"] == "Hello."
    assert written["samples"][0]["first_token_s"] == 0.18


def test_direction_plan_balances_uplink_and_downlink() -> None:
    """计划必须在两个方向上各采 N 条，且每方向都有不同长度的样本。"""
    plan = measure._direction_plan(samples_per_direction=5)

    uplink_count = sum(
        1 for direction, _text in plan if direction is AudioDirection.UPLINK
    )
    downlink_count = sum(
        1 for direction, _text in plan if direction is AudioDirection.DOWNLINK
    )
    uplink_texts = [text for direction, text in plan if direction is AudioDirection.UPLINK]
    downlink_texts = [text for direction, text in plan if direction is AudioDirection.DOWNLINK]

    assert uplink_count == 5
    assert downlink_count == 5
    assert len(set(uplink_texts)) == 5
    assert len(set(downlink_texts)) == 5
    assert max(len(text) for text in uplink_texts) > min(len(text) for text in uplink_texts)


def test_main_rejects_invalid_samples_count(
    capsys: Any,
) -> None:
    """超出样本池容量必须 fail-closed。"""
    exit_code = measure.main(["--samples-per-direction", "999"])

    assert exit_code == 1
    assert "FAIL" in capsys.readouterr().out


def test_main_rejects_missing_api_key(
    monkeypatch: Any,
    capsys: Any,
) -> None:
    """缺少 API Key 时必须 fail-closed，不发出任何真实 HTTP 调用。"""

    class _FakeSettings:
        deepseek_api_key_env = "DEEPSEEK_API_KEY"

        def resolved_deepseek_api_key(self) -> str:
            return ""

    monkeypatch.setattr(measure, "load_settings", lambda **_kwargs: _FakeSettings())

    exit_code = measure.main(["--samples-per-direction", "1"])

    assert exit_code == 1
    assert "DEEPSEEK_API_KEY" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_run_measurement_iterates_full_plan() -> None:
    """run_measurement 必须按计划顺序记录所有样本。"""
    plan = [
        (AudioDirection.UPLINK, "你好"),
        (AudioDirection.DOWNLINK, "Hello."),
    ]

    samples = await measure.run_measurement(_FakeStreamingClient(), plan)

    assert [sample.direction for sample in samples] == [
        AudioDirection.UPLINK,
        AudioDirection.DOWNLINK,
    ]
    assert [sample.text for sample in samples] == ["你好", "Hello."]
    assert all(sample.succeeded for sample in samples)
