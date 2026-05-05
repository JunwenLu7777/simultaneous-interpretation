"""DeepSeek streaming 契约测试。"""

import json

import pytest

from teams_voice_interpreter.data.audio_segment import AudioDirection
from teams_voice_interpreter.errors import DeepSeekError
from teams_voice_interpreter.mt.deepseek_client import (
    DeepSeekStreamingClient,
    classify_status,
    parse_sse_lines,
    retry_delays_ms,
)


def test_parse_200_sse_and_done() -> None:
    """200 OK SSE 与 [DONE] 终止符必须可解析。"""
    payload = {"choices": [{"delta": {"content": "hello"}}]}

    chunks = parse_sse_lines([f"data: {json.dumps(payload)}", "data: [DONE]"])

    assert [item.kind for item in chunks] == ["delta", "completed"]
    assert chunks[0].text == "hello"


@pytest.mark.parametrize(
    ("status", "kind"),
    [(401, "auth_error"), (402, "quota_exhausted"), (429, "retryable"), (500, "retryable")],
)
def test_classify_error_status(status: int, kind: str) -> None:
    """401 / 402 / 429 / 5xx 必须进入明确错误类别。"""
    assert classify_status(status) == kind


def test_retry_delays_follow_fr018() -> None:
    """重试退避必须符合 FR-018。"""
    assert retry_delays_ms() == [250, 500, 1000, 2000, 4000]


def test_malformed_sse_line_raises_two_part_error() -> None:
    """SSE 格式异常必须抛用户可见错误。"""
    with pytest.raises(DeepSeekError):
        parse_sse_lines(["event: message"])


@pytest.mark.asyncio
async def test_stream_translate_local_responder() -> None:
    """本地 responder 允许集成测试不依赖外网。"""
    client = DeepSeekStreamingClient(lambda text, direction: f"{direction.value}:{text}")

    chunks = [
        chunk async for chunk in client.stream_translate("你好", direction=AudioDirection.UPLINK)
    ]

    assert chunks[-1].kind == "completed"
    assert chunks[-1].text == "uplink:你好"
