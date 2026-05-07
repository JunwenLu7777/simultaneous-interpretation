"""Edge-TTS 流式合成客户端边界。"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol, cast

import edge_tts
from edge_tts.exceptions import NoAudioReceived

from teams_voice_interpreter.data.audio_segment import AudioDirection
from teams_voice_interpreter.errors import EdgeTTSError

DEFAULT_VOICES = {
    AudioDirection.UPLINK: "en-US-AriaNeural",
    AudioDirection.DOWNLINK: "zh-CN-XiaoxiaoNeural",
}
FIRST_BYTE_TIMEOUT_S = 8.0
SYNTHESIS_TIMEOUT_S = 15.0


@dataclass(frozen=True)
class TTSEvent:
    """TTS 流式事件。

    `audio_format` 让下游解码路径分流：
    - `"mp3"`：Edge-TTS 默认；下游用 `tts.audio_decode.decode_mp3_stream_to_pcm16`
      （PyAV 增量解码 + 重采到 16 kHz mono）。
    - `"pcm_s16le_22050"`：Piper 默认（22050 Hz mono int16 raw）；下游用
      `tts.audio_decode.decode_pcm_stream_to_pcm16` 重采到 16 kHz mono。
    新增 backend 时按 `<codec>_s16le_<rate>` 命名扩展。
    """

    kind: str
    audio_chunk: bytes = b""
    latency_ms: int = 0
    audio_format: str = "mp3"


class CommunicateLike(Protocol):
    """edge_tts.Communicate 的最小协议。"""

    def __init__(self, text: str, voice: str, *, rate: str = "+0%") -> None:
        """创建一次合成请求。"""

    def stream(self) -> AsyncIterator[dict[str, Any]]:
        """返回 Edge-TTS 流式事件。"""


class EdgeTTSClient:
    """Edge-TTS 客户端，测试环境返回确定性音频块。"""

    def __init__(
        self,
        voices: set[str] | None = None,
        *,
        live: bool = False,
        rate: str = "+0%",
        communicate_factory: type[CommunicateLike] | None = None,
        first_byte_timeout_s: float = FIRST_BYTE_TIMEOUT_S,
        synthesis_timeout_s: float = SYNTHESIS_TIMEOUT_S,
    ) -> None:
        self.voices = voices or {"en-US-AriaNeural", "zh-CN-XiaoxiaoNeural"}
        self.token_refresh_count = 0
        self.live = live
        self.rate = rate
        self.communicate_factory = communicate_factory or edge_tts.Communicate
        self.first_byte_timeout_s = first_byte_timeout_s
        self.synthesis_timeout_s = synthesis_timeout_s

    def validate_voice(self, voice: str) -> None:
        """校验音色是否可用。"""
        if voice not in self.voices:
            raise EdgeTTSError(
                code="tts.voice_invalid",
                what_happened="发生了什么：配置的 Edge-TTS 音色 ID 不可用。",
                next_action="下一步如何做：请在 config.toml 中切换为可用音色。",
            )

    async def stream_synthesize(
        self,
        text: str,
        *,
        direction: AudioDirection,
        voice: str | None = None,
    ) -> AsyncIterator[TTSEvent]:
        """合成译文并流式返回音频块。"""
        selected_voice = voice or DEFAULT_VOICES[direction]
        self.validate_voice(selected_voice)
        sanitized = sanitize_text(text)
        if not sanitized:
            raise EdgeTTSError(
                code="tts.empty_text",
                what_happened="发生了什么：没有可合成的译文文本。",
                next_action="下一步如何做：请等待下一段有效译文生成。",
            )
        if self.live:
            async for event in self._stream_live(sanitized, selected_voice):
                yield event
            return
        yield TTSEvent(kind="first_byte", audio_chunk=b"pcm0", latency_ms=100)
        yield TTSEvent(kind="audio_chunk", audio_chunk=sanitized.encode("utf-8")[:64])
        yield TTSEvent(kind="completed", latency_ms=300)

    async def _stream_live(self, text: str, voice: str) -> AsyncIterator[TTSEvent]:
        first_audio = True
        communicate = self.communicate_factory(text, voice, rate=self.rate)
        stream = cast(AsyncIterator[dict[str, Any]], communicate.stream().__aiter__())
        loop = asyncio.get_running_loop()
        started = loop.time()
        first_audio_deadline = started + self.first_byte_timeout_s
        synthesis_deadline = started + self.synthesis_timeout_s
        try:
            while True:
                chunk = await self._next_stream_chunk(
                    stream,
                    first_audio=first_audio,
                    first_audio_deadline=first_audio_deadline,
                    synthesis_deadline=synthesis_deadline,
                )
                if chunk is None:
                    break
                if chunk.get("type") != "audio":
                    continue
                audio = bytes(chunk.get("data", b""))
                if not audio:
                    continue
                if first_audio:
                    yield TTSEvent(kind="first_byte", audio_chunk=audio)
                    first_audio = False
                else:
                    yield TTSEvent(kind="audio_chunk", audio_chunk=audio)
        except NoAudioReceived as error:
            raise EdgeTTSError(
                code="tts.no_audio",
                what_happened="发生了什么：Edge-TTS 未返回音频数据。",
                next_action=(
                    "下一步如何做：请重试一次；如果持续失败，请把终端里显示的识别文本发给我。"
                ),
            ) from error
        if first_audio:
            raise _no_audio_error()
        yield TTSEvent(kind="completed")

    async def _next_stream_chunk(
        self,
        stream: AsyncIterator[dict[str, Any]],
        *,
        first_audio: bool,
        first_audio_deadline: float,
        synthesis_deadline: float,
    ) -> dict[str, Any] | None:
        timeout_s = _next_timeout_s(
            first_audio=first_audio,
            first_audio_deadline=first_audio_deadline,
            synthesis_deadline=synthesis_deadline,
        )
        try:
            return await asyncio.wait_for(anext(stream), timeout=timeout_s)
        except StopAsyncIteration:
            return None
        except TimeoutError as error:
            raise _timeout_error(
                first_audio=first_audio,
                first_audio_deadline=first_audio_deadline,
                synthesis_deadline=synthesis_deadline,
                first_byte_timeout_s=self.first_byte_timeout_s,
                synthesis_timeout_s=self.synthesis_timeout_s,
            ) from error

    def refresh_token_once(self) -> None:
        """模拟 401/403 后刷新 token。"""
        self.token_refresh_count += 1

    def handle_401_403_failures(self, failures: int) -> str:
        """连续鉴权失败后的降级动作。"""
        if failures >= 3:
            return "edge_tts_degraded"
        self.refresh_token_once()
        return "retrying"


def sanitize_text(text: str) -> str:
    """去除简单 SSML / 标签注入。"""
    return re.sub(r"<[^>]+>", "", text).strip()


def _next_timeout_s(
    *,
    first_audio: bool,
    first_audio_deadline: float,
    synthesis_deadline: float,
) -> float:
    loop = asyncio.get_running_loop()
    deadline = min(first_audio_deadline, synthesis_deadline) if first_audio else synthesis_deadline
    return max(0.0, deadline - loop.time())


def _timeout_error(
    *,
    first_audio: bool,
    first_audio_deadline: float,
    synthesis_deadline: float,
    first_byte_timeout_s: float,
    synthesis_timeout_s: float,
) -> EdgeTTSError:
    if first_audio and first_audio_deadline <= synthesis_deadline:
        return EdgeTTSError(
            code="tts.first_byte_timeout",
            what_happened=(
                f"发生了什么：Edge-TTS 在 {first_byte_timeout_s:g} 秒内没有返回首个音频片段。"
            ),
            next_action="下一步如何做：该段已丢弃，请保持通话继续，下一段会自动重试。",
        )
    return EdgeTTSError(
        code="tts.synthesis_timeout",
        what_happened=f"发生了什么：Edge-TTS 合成超过 {synthesis_timeout_s:g} 秒仍未完成。",
        next_action="下一步如何做：该段已丢弃，请保持通话继续，下一段会自动重试。",
    )


def _no_audio_error() -> EdgeTTSError:
    return EdgeTTSError(
        code="tts.no_audio",
        what_happened="发生了什么：Edge-TTS 未返回音频数据。",
        next_action="下一步如何做：请检查网络是否能访问 speech.platform.bing.com 后重试。",
    )
