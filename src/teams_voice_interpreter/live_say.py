"""短句翻译并写入 Teams 音频路由的可用桥。"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import cast

import httpx
import numpy as np
import numpy.typing as npt

from teams_voice_interpreter.audio.playback import (
    BlackHoleWriter,
    DefaultOutputWriter,
    SoundDeviceAudioSink,
    StreamingSoundDeviceAudioSink,
)
from teams_voice_interpreter.audio.routing import AudioDevice, AudioDeviceProbe
from teams_voice_interpreter.config import load_settings
from teams_voice_interpreter.data.audio_segment import AudioDirection
from teams_voice_interpreter.errors import DeepSeekError, EdgeTTSError, UserFacingError
from teams_voice_interpreter.mt.deepseek_client import DeepSeekStreamingClient, TranslationChunk
from teams_voice_interpreter.tts.audio_decode import (
    decode_mp3_bytes_to_pcm16,
)
from teams_voice_interpreter.tts.edge_tts_client import EdgeTTSClient, TTSEvent
from teams_voice_interpreter.tts.streaming import start_pcm_stream_with_retry

Int16Array = npt.NDArray[np.int16]
_RETRIABLE_TTS_ERROR_CODES = {
    "tts.no_audio",
    "tts.first_byte_timeout",
    "tts.synthesis_timeout",
}
REALTIME_TTS_FIRST_BYTE_TIMEOUT_S = 3.0
REALTIME_TTS_SYNTHESIS_TIMEOUT_S = 8.0
DEEPSEEK_STREAM_BUDGET_S = 8.0


@dataclass(frozen=True)
class SayResult:
    """短句发声结果。"""

    source_text: str
    target_text: str
    bytes_written: int
    target_device_name: str
    translation_latency_s: float = 0.0
    mt_first_token_latency_s: float = 0.0
    tts_latency_s: float = 0.0
    decode_latency_s: float = 0.0
    playback_latency_s: float = 0.0
    first_pcm_latency_s: float = 0.0
    first_playback_write_latency_s: float | None = None
    playback_truncated: bool = False


@dataclass(frozen=True)
class PreparedSayResult:
    """已翻译并合成但尚未写出播放的结果。"""

    source_text: str
    target_text: str
    target_device: AudioDevice
    target: str
    translation_latency_s: float
    tts_latency_s: float
    decode_latency_s: float
    pcm: Int16Array
    pcm_iterator: AsyncIterator[Int16Array] | None = None
    mt_first_token_latency_s: float = 0.0


@dataclass(frozen=True)
class _StreamingPlaybackStats:
    first_pcm_at: float | None
    playback_truncated: bool


class LiveSayBridge:
    """把一段文本翻译、合成并写入 BlackHole 或默认输出。"""

    def __init__(self, *, device_probe: AudioDeviceProbe | None = None) -> None:
        self.device_probe = device_probe or AudioDeviceProbe()
        self._deepseek_http_client: httpx.AsyncClient | None = None

    def _resolve_deepseek_http_client(self) -> httpx.AsyncClient:
        """Lazy 创建并复用 httpx.AsyncClient，避免每段译音重做 DNS/TLS 握手。"""
        client = getattr(self, "_deepseek_http_client", None)
        if client is None:
            client = httpx.AsyncClient(timeout=30.0)
            self._deepseek_http_client = client
        return client

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
        streaming: bool = False,
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
        chunks: list[TranslationChunk] = []
        mt_first_token_at: float | None = None

        async def _collect_translation() -> None:
            nonlocal mt_first_token_at
            async for chunk in DeepSeekStreamingClient(
                api_key=settings.resolved_deepseek_api_key(),
                model=settings.deepseek_model,
                http_client=self._resolve_deepseek_http_client(),
            ).stream_translate(source_text, direction=direction):
                if not chunk.text:
                    continue
                if mt_first_token_at is None:
                    mt_first_token_at = time.perf_counter()
                chunks.append(chunk)

        try:
            await asyncio.wait_for(
                _collect_translation(), timeout=DEEPSEEK_STREAM_BUDGET_S
            )
        except TimeoutError as error:
            raise DeepSeekError(
                code="mt.stream_budget_exceeded",
                what_happened=(
                    f"发生了什么：DeepSeek 在 {DEEPSEEK_STREAM_BUDGET_S:g} 秒内未完成翻译；"
                    "服务端 / 网络抖动，丢弃该段避免阻塞后续。"
                ),
                next_action=(
                    "下一步如何做：该段已丢弃，请保持通话继续；下一段会自动重试。"
                    "若反复触发，请检查网络或换 DeepSeek 接入点。"
                ),
            ) from error
        translated_at = time.perf_counter()
        mt_first_token_latency_s = (
            mt_first_token_at - translation_started if mt_first_token_at is not None else 0.0
        )
        target_text = _target_text_from_chunks(chunks)
        if not target_text:
            raise UserFacingError(
                code="say.empty_translation",
                what_happened="发生了什么：DeepSeek 没有返回可播出的译文。",
                next_action="下一步如何做：请稍后重试，或换一句更短的文本。",
            )
        if streaming:
            return PreparedSayResult(
                source_text=source_text,
                target_text=target_text,
                target_device=target_device,
                target=target,
                translation_latency_s=translated_at - translation_started,
                mt_first_token_latency_s=mt_first_token_latency_s,
                tts_latency_s=0.0,
                decode_latency_s=0.0,
                pcm=np.array([], dtype=np.int16),
                pcm_iterator=start_pcm_stream_with_retry(
                    target_text=target_text,
                    direction=direction,
                    rate=settings.tts_rate,
                    max_retries=1,
                    first_byte_timeout_s=REALTIME_TTS_FIRST_BYTE_TIMEOUT_S,
                    synthesis_timeout_s=REALTIME_TTS_SYNTHESIS_TIMEOUT_S,
                ),
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
            target_device=target_device,
            target=target,
            translation_latency_s=translated_at - translation_started,
            mt_first_token_latency_s=mt_first_token_latency_s,
            tts_latency_s=tts_completed_at - tts_started,
            decode_latency_s=decoded_at - tts_completed_at,
            pcm=pcm,
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
                if error.code not in _RETRIABLE_TTS_ERROR_CODES or attempt >= max_retries:
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
            mt_first_token_latency_s=prepared.mt_first_token_latency_s,
            tts_latency_s=prepared.tts_latency_s,
            decode_latency_s=prepared.decode_latency_s,
            playback_latency_s=played_at - playback_started,
        )

    async def play_prepared_streaming(
        self,
        prepared: PreparedSayResult,
        *,
        max_playback_seconds: float | None = None,
    ) -> SayResult:
        """把流式 PCM iterator 写入目标设备，保留同步 play_prepared 作为回退契约。"""
        if prepared.pcm_iterator is None:
            return await asyncio.to_thread(self.play_prepared, prepared)
        playback_started = time.perf_counter()
        stats = _StreamingPlaybackStats(first_pcm_at=None, playback_truncated=False)
        sink = StreamingSoundDeviceAudioSink(
            device_index=prepared.target_device.index,
            sample_rate_hz=16000,
        )
        try:
            stats = await _feed_streaming_pcm(
                sink,
                prepared.pcm_iterator,
                max_samples=_max_playback_samples(max_playback_seconds),
            )
        finally:
            if stats.playback_truncated:
                await _close_pcm_iterator(prepared.pcm_iterator)
            await sink.flush_and_close()
        played_at = time.perf_counter()
        return SayResult(
            source_text=prepared.source_text,
            target_text=prepared.target_text,
            bytes_written=sink.bytes_written,
            target_device_name=prepared.target_device.name,
            translation_latency_s=prepared.translation_latency_s,
            mt_first_token_latency_s=prepared.mt_first_token_latency_s,
            tts_latency_s=prepared.tts_latency_s,
            decode_latency_s=prepared.decode_latency_s,
            playback_latency_s=played_at - playback_started,
            first_pcm_latency_s=_first_pcm_latency_s(stats, playback_started=playback_started),
            first_playback_write_latency_s=sink.first_payload_latency_s,
            playback_truncated=stats.playback_truncated,
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


def _max_playback_samples(max_playback_seconds: float | None) -> int | None:
    if max_playback_seconds is None:
        return None
    return max(0, int(max_playback_seconds * 16000))


def _remaining_playback_samples(*, max_samples: int | None, fed_samples: int) -> int | None:
    if max_samples is None:
        return None
    return max(0, max_samples - fed_samples)


async def _feed_streaming_pcm(
    sink: StreamingSoundDeviceAudioSink,
    iterator: AsyncIterator[Int16Array],
    *,
    max_samples: int | None,
) -> _StreamingPlaybackStats:
    first_pcm_at: float | None = None
    fed_samples = 0
    async for pcm in iterator:
        chunk, fed_samples, playback_truncated = _playback_chunk(
            pcm,
            max_samples=max_samples,
            fed_samples=fed_samples,
        )
        if chunk is None:
            return _StreamingPlaybackStats(first_pcm_at, playback_truncated=True)
        if chunk.size == 0:
            continue
        await sink.feed_pcm(chunk)
        first_pcm_at = first_pcm_at or time.perf_counter()
        if playback_truncated:
            return _StreamingPlaybackStats(first_pcm_at, playback_truncated=True)
    return _StreamingPlaybackStats(first_pcm_at, playback_truncated=False)


def _playback_chunk(
    pcm: Int16Array,
    *,
    max_samples: int | None,
    fed_samples: int,
) -> tuple[Int16Array | None, int, bool]:
    if pcm.size == 0:
        return pcm, fed_samples, False
    remaining_samples = _remaining_playback_samples(
        max_samples=max_samples,
        fed_samples=fed_samples,
    )
    if remaining_samples == 0:
        return None, fed_samples, True
    if remaining_samples is None or pcm.size <= remaining_samples:
        return pcm, fed_samples + int(pcm.size), False
    return pcm[:remaining_samples], fed_samples + remaining_samples, True


def _first_pcm_latency_s(
    stats: _StreamingPlaybackStats,
    *,
    playback_started: float,
) -> float:
    if stats.first_pcm_at is None:
        return 0.0
    return stats.first_pcm_at - playback_started


async def _close_pcm_iterator(iterator: AsyncIterator[Int16Array]) -> None:
    close = getattr(iterator, "aclose", None)
    if close is None:
        return
    await cast(Callable[[], Awaitable[None]], close)()
