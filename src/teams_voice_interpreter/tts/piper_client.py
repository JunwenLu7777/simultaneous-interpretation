"""Piper 本地 TTS 流式合成客户端边界。

`PiperVoice.synthesize(text)` 是同步 generator，本类用 `asyncio.to_thread`
把它包装成异步流，以匹配 EdgeTTSClient.stream_synthesize 的 async 签名。

**输出格式**：raw PCM16 mono；sample rate 由 voice 模型决定（项目当前两个
默认音色 `en_US-amy-medium` 与 `zh_CN-huayan-medium` 均为 22050 Hz）。
**与 `EdgeTTSClient` 输出 mp3 chunks 不同**；调用端必须按 raw PCM 处理，
必要时重采样到目标设备 sample rate（生产管线集成时由 audio_writer 负责）。

模型缺失检查：`validate_voice` 仅检查 `<voice>.onnx` 与 `<voice>.onnx.json`
两个文件存在；不做 SHA256 / 完整性校验（保留给 `cli/wizard.py` 在首次运行
向导中处理）。
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from teams_voice_interpreter.data.audio_segment import AudioDirection
from teams_voice_interpreter.errors import PiperTTSError
from teams_voice_interpreter.tts.edge_tts_client import TTSEvent

# 与 `scripts/measure_piper_first_byte.py` 一致的默认音色映射；阶段 3 集成时
# 由 `config.py` 的 `piper_voices` 配置覆盖。
DEFAULT_PIPER_VOICES = {
    AudioDirection.UPLINK: "en_US-amy-medium",
    AudioDirection.DOWNLINK: "zh_CN-huayan-medium",
}

# Piper voice 默认采样率。两个项目默认 voice 当前都是 22050 Hz；如果未来引入
# 16 kHz / 48 kHz voice，应当从 PiperVoice config 读取（保留给阶段 3b）。
PIPER_OUTPUT_SAMPLE_RATE_HZ = 22050
# TTSEvent.audio_format 标识：raw little-endian int16 mono PCM @ 22050 Hz。
# 下游 (tts/audio_decode.decode_pcm_stream_to_pcm16) 按此分支。
PIPER_AUDIO_FORMAT = f"pcm_s16le_{PIPER_OUTPUT_SAMPLE_RATE_HZ}"


@dataclass(frozen=True)
class PiperVoiceLoaderProtocol:
    """供测试注入的 voice 加载器：接收 onnx 路径，返回 PiperVoice 实例。"""


class PiperClient:
    """Piper TTS 客户端（同步 generator → 异步流）。

    复用 `TTSEvent` dataclass（与 `EdgeTTSClient` 共享），输出 raw PCM16
    bytes（22050 Hz mono）。
    """

    def __init__(
        self,
        models_dir: Path,
        *,
        voices: dict[AudioDirection, str] | None = None,
        voice_loader: Any | None = None,
    ) -> None:
        """初始化 Piper 客户端。

        Args:
            models_dir: Piper voice 模型所在目录（含 `<voice>.onnx` 与
                `<voice>.onnx.json`）。
            voices: 方向到 voice 名的映射，覆盖 DEFAULT_PIPER_VOICES。
            voice_loader: 测试注入；签名 `Callable[[str], PiperVoice]`。
                生产环境为 None，运行时延迟 import `piper.PiperVoice`。
        """
        self._models_dir = models_dir
        self._voices: dict[AudioDirection, str] = voices or dict(DEFAULT_PIPER_VOICES)
        self._voice_loader: Any | None = voice_loader
        self._loaded: dict[str, Any] = {}
        self._load_lock = threading.Lock()

    def validate_voice(self, voice: str) -> None:
        """校验 voice 模型文件存在。

        与 `EdgeTTSClient.validate_voice`（音色枚举校验）语义不同 —— Piper 的
        voice 表是文件系统上的 ONNX 模型，需要按文件存在性校验。
        """
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
        """提前加载指定方向的 voice，降低首个 TTS chunk 的 cold start 抖动。"""
        selected_voice = voice or self._voices[direction]
        self.validate_voice(selected_voice)
        self._get_or_load(selected_voice)

    async def stream_synthesize(
        self,
        text: str,
        *,
        direction: AudioDirection,
        voice: str | None = None,
    ) -> AsyncIterator[TTSEvent]:
        """合成译文并流式返回 PCM16 音频块。

        第一个 `kind="first_byte"` event 时刻即首字节延迟（与 EdgeTTSClient
        语义一致，便于 readiness / observability 复用同套指标）。
        """
        sanitized = text.strip()
        if not sanitized:
            raise PiperTTSError(
                code="tts.empty_text",
                what_happened="发生了什么：没有可合成的译文文本。",
                next_action="下一步如何做：请等待下一段有效译文生成。",
            )
        selected_voice = voice or self._voices[direction]
        self.validate_voice(selected_voice)
        piper_voice = self._get_or_load(selected_voice)

        first = True
        try:
            iterator = await asyncio.to_thread(_make_iterator, piper_voice, sanitized)
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
        except Exception as error:  # ONNX runtime / IO / 模型损坏
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

    def _get_or_load(self, voice: str) -> Any:
        with self._load_lock:
            if voice not in self._loaded:
                self._loaded[voice] = self._load_voice(voice)
        return self._loaded[voice]

    def _load_voice(self, voice: str) -> Any:
        onnx_path = self._models_dir / f"{voice}.onnx"
        if self._voice_loader is not None:
            return self._voice_loader(str(onnx_path))
        from piper import PiperVoice as _PiperVoice  # 延迟 import

        return _PiperVoice.load(str(onnx_path))


_SENTINEL: object = object()


def _make_iterator(voice: Any, text: str) -> Any:
    """在 thread 内创建同步 generator iterator。"""
    return iter(voice.synthesize(text))


def _next_chunk(iterator: Any) -> Any:
    """在 thread 内取下一个 chunk；StopIteration 转换为 _SENTINEL。"""
    try:
        return next(iterator)
    except StopIteration:
        return _SENTINEL
