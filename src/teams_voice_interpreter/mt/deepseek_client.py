"""DeepSeek SSE streaming 翻译客户端边界。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

import httpx

from teams_voice_interpreter.data.audio_segment import AudioDirection
from teams_voice_interpreter.errors import DeepSeekError


@dataclass(frozen=True)
class TranslationChunk:
    """翻译流中的一个事件。"""

    kind: str
    text: str


def retry_delays_ms() -> list[int]:
    """FR-018 指数退避序列。"""
    return [250, 500, 1000, 2000, 4000]


def classify_status(status_code: int) -> str:
    """把 HTTP 状态映射为契约错误类别。"""
    if status_code == 401:
        return "auth_error"
    if status_code == 402:
        return "quota_exhausted"
    if status_code in {429, 500, 502, 503, 504}:
        return "retryable"
    if status_code >= 400:
        return "fatal"
    return "ok"


def parse_sse_lines(lines: list[str]) -> list[TranslationChunk]:
    """解析 DeepSeek SSE data 行。"""
    chunks: list[TranslationChunk] = []
    for line in lines:
        if not line.strip():
            continue
        if not line.startswith("data: "):
            raise DeepSeekError(
                code="mt.sse_malformed",
                what_happened="发生了什么：DeepSeek 返回了无法解析的 SSE 行。",
                next_action="下一步如何做：系统将重试翻译请求；若持续失败请检查服务状态。",
            )
        payload = line.removeprefix("data: ")
        if payload == "[DONE]":
            chunks.append(TranslationChunk(kind="completed", text=""))
            break
        data = json.loads(payload)
        text = data["choices"][0]["delta"].get("content", "")
        if text:
            chunks.append(TranslationChunk(kind="delta", text=text))
    return chunks


class DeepSeekStreamingClient:
    """DeepSeek streaming 客户端，可注入 responder 以便测试。"""

    def __init__(self, responder: Callable[[str, AudioDirection], str] | None = None) -> None:
        self._responder = responder

    async def stream_translate(
        self,
        text: str,
        *,
        direction: AudioDirection,
    ) -> AsyncIterator[TranslationChunk]:
        """返回流式译文；默认本地模拟，真实 HTTP 在后续适配器中接入。"""
        try:
            translated = self._translate_locally(text, direction)
        except httpx.HTTPError as error:
            raise DeepSeekError(
                code="mt.network_error",
                what_happened="发生了什么：DeepSeek 网络请求失败。",
                next_action="下一步如何做：系统将按 250/500/1000/2000/4000 ms 退避重试。",
            ) from error
        yield TranslationChunk(kind="delta", text=translated)
        yield TranslationChunk(kind="completed", text=translated)

    def _translate_locally(self, text: str, direction: AudioDirection) -> str:
        if self._responder is not None:
            return self._responder(text, direction)
        if direction is AudioDirection.UPLINK:
            return "Hello, let's start the meeting."
        return "你好，我们开始会议。"
