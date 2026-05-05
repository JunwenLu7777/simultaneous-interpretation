"""短句翻译并写入 Teams 音频路由的可用桥。"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from teams_voice_interpreter.audio.playback import (
    BlackHoleWriter,
    DefaultOutputWriter,
    SoundDeviceAudioSink,
)
from teams_voice_interpreter.audio.routing import AudioDevice, AudioDeviceProbe
from teams_voice_interpreter.config import load_settings
from teams_voice_interpreter.data.audio_segment import AudioDirection
from teams_voice_interpreter.errors import EdgeTTSError, UserFacingError
from teams_voice_interpreter.mt.deepseek_client import DeepSeekStreamingClient, TranslationChunk
from teams_voice_interpreter.tts.audio_decode import decode_mp3_bytes_to_pcm16
from teams_voice_interpreter.tts.edge_tts_client import EdgeTTSClient, TTSEvent

Int16Array = npt.NDArray[np.int16]


@dataclass(frozen=True)
class SayResult:
    """短句发声结果。"""

    source_text: str
    target_text: str
    bytes_written: int
    target_device_name: str
    translation_latency_s: float = 0.0
    tts_latency_s: float = 0.0
    decode_latency_s: float = 0.0
    playback_latency_s: float = 0.0


@dataclass(frozen=True)
class PreparedSayResult:
    """已翻译并合成但尚未写出播放的结果。"""

    source_text: str
    target_text: str
    pcm: Int16Array
    target_device: AudioDevice
    target: str
    translation_latency_s: float
    tts_latency_s: float
    decode_latency_s: float


class LiveSayBridge:
    """把一段文本翻译、合成并写入 BlackHole 或默认输出。"""

    def __init__(self, *, device_probe: AudioDeviceProbe | None = None) -> None:
        self.device_probe = device_probe or AudioDeviceProbe()

    async def say(
        self,
        text: str,
        *,
        direction: AudioDirection,
        target: str,
    ) -> SayResult:
        """执行一次短句翻译发声。"""
        prepared = await self.prepare(text, direction=direction, target=target)
        return self.play_prepared(prepared)

    async def prepare(
        self,
        text: str,
        *,
        direction: AudioDirection,
        target: str,
    ) -> PreparedSayResult:
        """翻译并合成音频，但不阻塞播放。"""
        source_text = text.strip()
        if not source_text:
            raise UserFacingError(
                code="say.empty_text",
                what_happened="发生了什么：没有可发送到 Teams 的文本。",
                next_action="下一步如何做：请提供一段要翻译并播出的短句。",
            )
        target_device = self._target_device(target)
        settings = load_settings(validate_credentials=True)
        translation_started = time.perf_counter()
        chunks = [
            chunk
            async for chunk in DeepSeekStreamingClient(
                api_key=settings.resolved_deepseek_api_key(),
                model=settings.deepseek_model,
            ).stream_translate(source_text, direction=direction)
            if chunk.text
        ]
        translated_at = time.perf_counter()
        target_text = _target_text_from_chunks(chunks)
        if not target_text:
            raise UserFacingError(
                code="say.empty_translation",
                what_happened="发生了什么：DeepSeek 没有返回可播出的译文。",
                next_action="下一步如何做：请稍后重试，或换一句更短的文本。",
            )
        tts_started = time.perf_counter()
        audio_events = await self._synthesize_with_retry(
            target_text=target_text,
            direction=direction,
            rate=settings.tts_rate,
        )
        mp3_bytes = b"".join(event.audio_chunk for event in audio_events)
        tts_completed_at = time.perf_counter()
        pcm = decode_mp3_bytes_to_pcm16(mp3_bytes)
        decoded_at = time.perf_counter()
        return PreparedSayResult(
            source_text=source_text,
            target_text=target_text,
            pcm=pcm,
            target_device=target_device,
            target=target,
            translation_latency_s=translated_at - translation_started,
            tts_latency_s=tts_completed_at - tts_started,
            decode_latency_s=decoded_at - tts_completed_at,
        )

    async def _synthesize_with_retry(
        self,
        *,
        target_text: str,
        direction: AudioDirection,
        rate: str,
        max_retries: int = 1,
    ) -> list[TTSEvent]:
        """调 Edge-TTS；遇到 `tts.no_audio` 短文本/网络抖动时延迟一次重试。

        Edge-TTS 对 ≤ 8 字短句和高频连续请求会偶发返回空音频，重试一次通常可恢复；
        若仍失败则带上译文预览原样抛 `EdgeTTSError`，供调用方按 `_prepare_listen_segment`
        的两段式打印丢弃该段，避免该段在 prepare 阶段直接撕掉整个 worker。
        """
        last_error: EdgeTTSError | None = None
        for attempt in range(max_retries + 1):
            try:
                return [
                    event
                    async for event in EdgeTTSClient(
                        live=True,
                        rate=rate,
                    ).stream_synthesize(target_text, direction=direction)
                    if event.audio_chunk
                ]
            except EdgeTTSError as error:
                last_error = error
                if error.code != "tts.no_audio" or attempt >= max_retries:
                    break
                await asyncio.sleep(0.3 * (attempt + 1))
        assert last_error is not None
        raise EdgeTTSError(
            code=last_error.code,
            what_happened=(
                f"{last_error.what_happened} 译文预览：{_preview_text(target_text)}"
                f"（重试 {max_retries} 次后仍失败）"
            ),
            next_action=last_error.next_action,
        ) from last_error

    def play_prepared(self, prepared: PreparedSayResult) -> SayResult:
        """把已合成 PCM 写入目标设备。"""
        playback_started = time.perf_counter()
        if prepared.target == "blackhole":
            writer = BlackHoleWriter(
                SoundDeviceAudioSink(
                    device_index=prepared.target_device.index,
                    sample_rate_hz=16000,
                )
            )
            writer.write_mono(prepared.pcm)
            bytes_written = writer.sink.bytes_written
        else:
            default_writer = DefaultOutputWriter(
                SoundDeviceAudioSink(
                    device_index=prepared.target_device.index,
                    sample_rate_hz=16000,
                )
            )
            default_writer.write_mono(prepared.pcm)
            bytes_written = default_writer.sink.bytes_written
        played_at = time.perf_counter()
        return SayResult(
            source_text=prepared.source_text,
            target_text=prepared.target_text,
            bytes_written=bytes_written,
            target_device_name=prepared.target_device.name,
            translation_latency_s=prepared.translation_latency_s,
            tts_latency_s=prepared.tts_latency_s,
            decode_latency_s=prepared.decode_latency_s,
            playback_latency_s=played_at - playback_started,
        )

    def _target_device(self, target: str) -> AudioDevice:
        if target == "blackhole":
            settings = load_settings(validate_credentials=False)
            return self.device_probe.find_output_device_by_name(
                settings.uplink_virtual_device_name,
                min_channels=2,
            )
        if target == "default":
            return self.device_probe.get_default_output()
        raise UserFacingError(
            code="say.target_invalid",
            what_happened=f"发生了什么：未知发声目标 `{target}`。",
            next_action="下一步如何做：请使用 `blackhole` 或 `default`。",
        )


def _target_text_from_chunks(chunks: list[TranslationChunk]) -> str:
    """把 DeepSeek delta 分片拼成完整可播出文本。"""
    return "".join(chunk.text for chunk in chunks).strip()


def _preview_text(text: str, *, max_length: int = 80) -> str:
    """生成安全的单行文本预览。"""
    compact = " ".join(text.split())
    if len(compact) <= max_length:
        return compact
    return f"{compact[:max_length]}..."
