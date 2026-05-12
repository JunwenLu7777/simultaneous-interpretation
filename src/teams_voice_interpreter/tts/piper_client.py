"""Piper 本地 TTS 流式合成客户端边界。

`PiperVoice.synthesize(text)` 是同步 generator，本类用 `asyncio.to_thread`
把它包装成异步流，以匹配 EdgeTTSClient.stream_synthesize 的 async 签名。

**输出格式**：raw PCM16 mono；sample rate 由 voice 模型决定（项目当前两个
默认音色 `en_US-amy-medium` 与 `zh_CN-huayan-medium` 均为 22050 Hz）。
**与 `EdgeTTSClient` 输出 mp3 chunks 不同**；调用端必须按 raw PCM 处理，
必要时重采样到目标设备 sample rate（生产管线集成时由 audio_writer 负责）。

**并发**：ONNX InferenceSession 非线程安全，本类为每个 voice 维护一个实例池
（默认 3 个），多会议场景下并发 `stream_synthesize` 调用自动分配到不同实例。
池耗尽时调用方排队等待。

模型缺失检查：`validate_voice` 仅检查 `<voice>.onnx` 与 `<voice>.onnx.json`
两个文件存在；不做 SHA256 / 完整性校验（保留给 `cli/wizard.py` 在首次运行
向导中处理）。
"""

from __future__ import annotations

import asyncio
import queue
import re
import threading
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from piper.config import SynthesisConfig

from teams_voice_interpreter.data.audio_segment import AudioDirection
from teams_voice_interpreter.errors import PiperTTSError
from teams_voice_interpreter.tts.edge_tts_client import TTSEvent

DEFAULT_PIPER_VOICES = {
    AudioDirection.UPLINK: "en_US-amy-medium",
    AudioDirection.DOWNLINK: "zh_CN-huayan-medium",
}

PIPER_OUTPUT_SAMPLE_RATE_HZ = 22050
PIPER_AUDIO_FORMAT = f"pcm_s16le_{PIPER_OUTPUT_SAMPLE_RATE_HZ}"

# 每 voice 默认实例池大小。多会议场景每个会议最多占用 2 个 voice（上下行），
# 3 个实例足够覆盖 3-4 个并发会议（正常对话中上下行交替而非同时说话）。
DEFAULT_POOL_SIZE = 3


@dataclass(frozen=True)
class PiperVoiceLoaderProtocol:
    """供测试注入的 voice 加载器：接收 onnx 路径，返回 PiperVoice 实例。"""


class _VoicePool:
    """ONNX 实例池：PiperVoice 非线程安全，用池化支持并发调用。"""

    def __init__(self, factory: Any, size: int) -> None:
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=size)
        for _ in range(size):
            self._queue.put(factory())

    async def acquire(self) -> Any:
        """从池中取一个空闲 voice 实例（阻塞直到有可用）。"""
        return await asyncio.to_thread(self._queue.get)

    def release(self, voice: Any) -> None:
        """归还 voice 实例到池中。"""
        self._queue.put(voice)

    @property
    def size(self) -> int:
        return self._queue.maxsize


class PiperClient:
    """Piper TTS 客户端（同步 generator → 异步流），内置 voice 实例池。

    复用 `TTSEvent` dataclass（与 `EdgeTTSClient` 共享），输出 raw PCM16
    bytes（22050 Hz mono）。
    """

    def __init__(
        self,
        models_dir: Path,
        *,
        pool_size: int = DEFAULT_POOL_SIZE,
        voices: dict[AudioDirection, str] | None = None,
        voice_loader: Any | None = None,
    ) -> None:
        self._models_dir = models_dir
        self._pool_size = pool_size
        self._voices: dict[AudioDirection, str] = voices or dict(DEFAULT_PIPER_VOICES)
        self._voice_loader: Any | None = voice_loader
        self._pools: dict[str, _VoicePool] = {}
        self._pools_lock = threading.Lock()

    def validate_voice(self, voice: str) -> None:
        """校验 voice 模型文件存在。"""
        onnx_path = self._models_dir / f"{voice}.onnx"
        json_path = self._models_dir / f"{voice}.onnx.json"
        if not onnx_path.exists() or not json_path.exists():
            raise PiperTTSError(
                code="tts.voice_invalid",
                what_happened=(
                    f"发生了什么：缺少 Piper voice 模型 {voice}（{onnx_path}）。"
                ),
                next_action=(
                    f"下一步如何做：从 https://huggingface.co/rhasspy/piper-voices 下载 "
                    f"{voice}.onnx 与 {voice}.onnx.json 到 {self._models_dir}/。"
                ),
            )

    def preload_voice(self, *, direction: AudioDirection, voice: str | None = None) -> None:
        """预加载指定方向的所有 pool 实例，消除首次调用冷启动。"""
        selected_voice = voice or self._voices[direction]
        self.validate_voice(selected_voice)
        pool = self._get_or_create_pool(selected_voice)
        # 池初始化时已加载所有实例，此处仅触发创建

    async def stream_synthesize(
        self,
        text: str,
        *,
        direction: AudioDirection,
        voice: str | None = None,
        rate: str | None = None,
    ) -> AsyncIterator[TTSEvent]:
        """合成译文并流式返回 PCM16 音频块。

        从 voice 池中获取一个空闲实例执行合成，完成后归还。
        若所有实例都在使用中则排队等待。

        rate 格式如 ``"+20%"``，转换为 Piper 的 ``length_scale``（值越低越快）。
        """
        sanitized = text.strip()
        if not sanitized:
            raise PiperTTSError(
                code="tts.empty_text",
                what_happened="发生了什么：没有可合成的译文文本。",
                next_action="下一步如何做：请等待下一段有效译文生成。",
            )
        length_scale = _rate_to_length_scale(rate)
        selected_voice = voice or self._voices[direction]
        self.validate_voice(selected_voice)
        pool = self._get_or_create_pool(selected_voice)
        piper_voice = await pool.acquire()
        try:
            async for event in self._synthesize_with_voice(piper_voice, sanitized, length_scale=length_scale):
                yield event
        finally:
            pool.release(piper_voice)

    async def _synthesize_with_voice(
        self, piper_voice: Any, sanitized: str, *, length_scale: float | None = None,
    ) -> AsyncIterator[TTSEvent]:
        first = True
        try:
            iterator = await asyncio.to_thread(_make_iterator, piper_voice, sanitized, length_scale)
            while True:
                chunk = await asyncio.to_thread(_next_chunk, iterator)
                if chunk is _SENTINEL:
                    break
                pcm = bytes(getattr(chunk, "audio_int16_bytes", b""))
                if not pcm:
                    continue
                if first:
                    yield TTSEvent(
                        kind="first_byte",
                        audio_chunk=pcm,
                        audio_format=PIPER_AUDIO_FORMAT,
                    )
                    first = False
                else:
                    yield TTSEvent(
                        kind="audio_chunk",
                        audio_chunk=pcm,
                        audio_format=PIPER_AUDIO_FORMAT,
                    )
        except PiperTTSError:
            raise
        except Exception as error:
            raise PiperTTSError(
                code="tts.piper_synthesize_failed",
                what_happened=f"发生了什么：Piper 合成失败：{type(error).__name__}: {error}。",
                next_action=(
                    "下一步如何做：请检查 ONNX 模型文件是否完整（重新下载 .onnx 与 .onnx.json）"
                    "并确认 onnxruntime 已安装。"
                ),
            ) from error

        if first:
            raise PiperTTSError(
                code="tts.no_audio",
                what_happened="发生了什么：Piper 未返回任何音频数据。",
                next_action=(
                    "下一步如何做：请重试一次；如果持续失败，请把终端里显示的识别文本发给我。"
                ),
            )
        yield TTSEvent(kind="completed", audio_format=PIPER_AUDIO_FORMAT)

    def _get_or_create_pool(self, voice: str) -> _VoicePool:
        with self._pools_lock:
            if voice not in self._pools:
                self._pools[voice] = _VoicePool(
                    factory=lambda v=voice: self._load_voice(v),
                    size=self._pool_size,
                )
        return self._pools[voice]

    def _load_voice(self, voice: str) -> Any:
        onnx_path = self._models_dir / f"{voice}.onnx"
        if self._voice_loader is not None:
            return self._voice_loader(str(onnx_path))
        from piper import PiperVoice as _PiperVoice

        return _PiperVoice.load(str(onnx_path))


_SENTINEL: object = object()


def _make_iterator(voice: Any, text: str, length_scale: float | None = None) -> Any:
    """在 thread 内创建同步 generator iterator；可传入 length_scale 控制语速。"""
    if length_scale is not None:
        syn_config = SynthesisConfig(length_scale=length_scale)
        return iter(voice.synthesize(text, syn_config=syn_config))
    return iter(voice.synthesize(text))


def _rate_to_length_scale(rate: str | None) -> float | None:
    """把 ``"+20%"`` 格式的语速字符串转换为 Piper 的 ``length_scale``。

    length_scale 值越低语速越快：+50% → 0.667, +20% → 0.833。
    """
    if not rate:
        return None
    match = re.match(r"^([+-])(\d+)%$", rate.strip())
    if not match:
        return None
    sign, number = match.group(1), int(match.group(2))
    factor = 1.0 + (number / 100.0) * (1.0 if sign == "+" else -1.0)
    if factor <= 0:
        return None
    return round(1.0 / factor, 4)


def _next_chunk(iterator: Any) -> Any:
    """在 thread 内取下一个 chunk；StopIteration 转换为 _SENTINEL。"""
    try:
        return next(iterator)
    except StopIteration:
        return _SENTINEL
