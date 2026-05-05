"""DeepSeek SSE streaming 翻译客户端边界。"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

import httpx

from teams_voice_interpreter.data.audio_segment import AudioDirection
from teams_voice_interpreter.errors import DeepSeekError
from teams_voice_interpreter.mt.prompt import build_system_prompt


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
        choices = data.get("choices", [])
        if not choices:
            continue
        text = choices[0]["delta"].get("content", "")
        if text:
            chunks.append(TranslationChunk(kind="delta", text=text))
    return chunks


class DeepSeekStreamingClient:
    """DeepSeek streaming 客户端，可注入 responder 以便测试。"""

    def __init__(
        self,
        responder: Callable[[str, AudioDirection], str] | None = None,
        *,
        api_key: str | None = None,
        model: str = "deepseek-v4-flash",
        base_url: str = "https://api.deepseek.com",
        http_client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._responder = responder
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._http_client = http_client
        self._timeout_seconds = timeout_seconds

    async def stream_translate(
        self,
        text: str,
        *,
        direction: AudioDirection,
    ) -> AsyncIterator[TranslationChunk]:
        """返回流式译文；注入 responder 时走本地测试分支，否则走 DeepSeek HTTP。"""
        try:
            if self._responder is not None:
                translated = self._translate_locally(text, direction)
                yield TranslationChunk(kind="delta", text=translated)
                yield TranslationChunk(kind="completed", text=translated)
                return
            async for chunk in self._translate_http(text, direction):
                yield chunk
        except httpx.HTTPError as error:
            raise DeepSeekError(
                code="mt.network_error",
                what_happened="发生了什么：DeepSeek 网络请求失败。",
                next_action="下一步如何做：系统将按 250/500/1000/2000/4000 ms 退避重试。",
            ) from error

    def _translate_locally(self, text: str, direction: AudioDirection) -> str:
        if self._responder is not None:
            return self._responder(text, direction)
        if direction is AudioDirection.UPLINK:
            return "Hello, let's start the meeting."
        return "你好，我们开始会议。"

    async def _translate_http(
        self,
        text: str,
        direction: AudioDirection,
    ) -> AsyncIterator[TranslationChunk]:
        api_key = self._api_key or os.getenv("DEEPSEEK_API_KEY", "")
        if not api_key:
            raise DeepSeekError(
                code="mt.deepseek_key_missing",
                what_happened="发生了什么：缺少 DeepSeek API Key 环境变量。",
                next_action="下一步如何做：请设置 DEEPSEEK_API_KEY 后重试。",
            )
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": build_system_prompt(direction, [])},
                {"role": "user", "content": text},
            ],
            "stream": True,
            "temperature": 0.2,
            "thinking": {"type": "disabled"},
        }
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        client = self._http_client or httpx.AsyncClient(timeout=self._timeout_seconds)
        close_client = self._http_client is None
        try:
            async with client.stream(
                "POST",
                f"{self._base_url}/chat/completions",
                headers=headers,
                json=payload,
            ) as response:
                if response.status_code >= 400:
                    error_kind = classify_status(response.status_code)
                    raise DeepSeekError(
                        code=f"mt.{error_kind}",
                        what_happened=f"发生了什么：DeepSeek 返回 HTTP {response.status_code}。",
                        next_action="下一步如何做：请检查 API Key、账户额度和网络后重试。",
                    )
                async for line in response.aiter_lines():
                    for chunk in parse_sse_lines([line]):
                        yield chunk
        finally:
            if close_client:
                await client.aclose()
