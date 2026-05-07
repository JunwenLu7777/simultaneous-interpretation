"""短句翻译并写入 Teams 音频路由的可用桥。"""

from __future__ import annotations

import asyncio
import queue
import threading
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
from teams_voice_interpreter.config import Settings, load_settings
from teams_voice_interpreter.data.audio_segment import AudioDirection
from teams_voice_interpreter.errors import (
    DeepSeekError,
    EdgeTTSError,
    PiperTTSError,
    UserFacingError,
)
from teams_voice_interpreter.mt.deepseek_client import DeepSeekStreamingClient, TranslationChunk
from teams_voice_interpreter.tts.audio_decode import (
    decode_mp3_stream_to_pcm16,
    decode_pcm_stream_to_pcm16,
)
from teams_voice_interpreter.tts.edge_tts_client import TTSEvent
from teams_voice_interpreter.tts.factory import build_tts_client
from teams_voice_interpreter.tts.streaming import stream_pcm_chunks_with_retry

Int16Array = npt.NDArray[np.int16]
_RETRIABLE_TTS_ERROR_CODES = {
    "tts.no_audio",
    "tts.first_byte_timeout",
    "tts.synthesis_timeout",
    "tts.piper_synthesize_failed",
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


@dataclass(frozen=True)
class _EarlyTranslationReady:
    """MT delta 已产生首个可播 TTS 片段。"""

    target_text: str
    translation_latency_s: float
    mt_first_token_latency_s: float


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
        context_text: str = "",
    ) -> PreparedSayResult:
        """翻译并合成音频，但不阻塞播放。"""
        source_text = text.strip()
        context_source_text = context_text.strip()
        if not source_text:
            raise UserFacingError(
                code="say.empty_text",
                what_happened="发生了什么：没有可发送到 Teams 的文本。",
                next_action="下一步如何做：请提供一段要翻译并播出的短句。",
            )
        target_device = self._target_device(target)
        settings = load_settings(validate_credentials=True)
        if streaming:
            translation_started = time.perf_counter()
            pcm_iterator = _EarlyTranslationPCMStream(
                source_text=source_text,
                direction=direction,
                context_text=context_source_text,
                settings=settings,
                first_byte_timeout_s=REALTIME_TTS_FIRST_BYTE_TIMEOUT_S,
                synthesis_timeout_s=REALTIME_TTS_SYNTHESIS_TIMEOUT_S,
            )
            try:
                ready = await pcm_iterator.wait_until_ready(
                    timeout_s=DEEPSEEK_STREAM_BUDGET_S,
                )
            except Exception:
                await pcm_iterator.aclose()
                raise
            return PreparedSayResult(
                source_text=source_text,
                target_text=ready.target_text,
                target_device=target_device,
                target=target,
                translation_latency_s=ready.translation_latency_s,
                mt_first_token_latency_s=ready.mt_first_token_latency_s,
                tts_latency_s=0.0,
                decode_latency_s=0.0,
                pcm=np.array([], dtype=np.int16),
                pcm_iterator=pcm_iterator,
            )
        translation_started = time.perf_counter()
        chunks: list[TranslationChunk] = []
        mt_first_token_at: float | None = None

        async def _collect_translation() -> None:
            nonlocal mt_first_token_at
            async for chunk in DeepSeekStreamingClient(
                api_key=settings.resolved_deepseek_api_key(),
                model=settings.deepseek_model,
                http_client=self._resolve_deepseek_http_client(),
            ).stream_translate(
                source_text,
                direction=direction,
                context_text=context_source_text,
            ):
                if not chunk.text:
                    continue
                if mt_first_token_at is None:
                    mt_first_token_at = time.perf_counter()
                chunks.append(chunk)

        try:
            await asyncio.wait_for(_collect_translation(), timeout=DEEPSEEK_STREAM_BUDGET_S)
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
        tts_started = time.perf_counter()
        audio_events = await self._synthesize_with_retry(
            target_text=target_text,
            direction=direction,
            settings=settings,
        )
        tts_completed_at = time.perf_counter()
        pcm = await _decode_tts_events_to_pcm16(audio_events)
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
        settings: Settings,
        max_retries: int = 1,
    ) -> list[TTSEvent]:
        """按配置选择 TTS backend；遇到可恢复错误时延迟一次重试。

        Edge-TTS 对 ≤ 8 字短句和高频连续请求会偶发返回空音频，Piper 也可能遇到
        ONNX runtime / IO 暂时性错误；重试一次通常可恢复。
        若仍失败则带上译文预览原样抛同类 TTS 错误，供调用方按 `_prepare_listen_segment`
        的两段式打印丢弃该段，避免该段在 prepare 阶段直接撕掉整个 worker。
        """
        last_error: EdgeTTSError | PiperTTSError | None = None
        for attempt in range(max_retries + 1):
            try:
                client = build_tts_client(settings)
                return [
                    event
                    async for event in client.stream_synthesize(target_text, direction=direction)
                    if event.audio_chunk
                ]
            except (EdgeTTSError, PiperTTSError) as error:
                last_error = error
                if error.code not in _RETRIABLE_TTS_ERROR_CODES or attempt >= max_retries:
                    break
                await asyncio.sleep(0.3 * (attempt + 1))
        assert last_error is not None
        raise type(last_error)(
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


class _EarlyTranslationPCMStream:
    """后台把 MT delta 尽早转成 TTS PCM，避免等待 MT completed。"""

    def __init__(
        self,
        *,
        source_text: str,
        direction: AudioDirection,
        context_text: str,
        settings: Settings,
        first_byte_timeout_s: float,
        synthesis_timeout_s: float,
        max_queue_chunks: int = 16,
    ) -> None:
        self._closed = threading.Event()
        self._pcm_queue: queue.Queue[Int16Array | BaseException | None] = queue.Queue(
            maxsize=max_queue_chunks
        )
        self._ready_queue: queue.Queue[_EarlyTranslationReady | BaseException] = queue.Queue(
            maxsize=1
        )
        self._thread = threading.Thread(
            target=self._run,
            kwargs={
                "source_text": source_text,
                "direction": direction,
                "context_text": context_text,
                "settings": settings,
                "first_byte_timeout_s": first_byte_timeout_s,
                "synthesis_timeout_s": synthesis_timeout_s,
            },
            daemon=True,
        )
        self._thread.start()

    def __aiter__(self) -> _EarlyTranslationPCMStream:
        return self

    async def __anext__(self) -> Int16Array:
        item = await asyncio.to_thread(self._pcm_queue.get)
        if item is None:
            raise StopAsyncIteration
        if isinstance(item, BaseException):
            raise item
        return item

    async def wait_until_ready(self, *, timeout_s: float) -> _EarlyTranslationReady:
        """等待首个可播译文片段；超时则丢弃该段。"""
        try:
            item = await asyncio.to_thread(self._ready_queue.get, True, timeout_s)
        except queue.Empty as error:
            raise DeepSeekError(
                code="mt.stream_budget_exceeded",
                what_happened=(
                    f"发生了什么：DeepSeek 在 {timeout_s:g} 秒内未产生可播译文；"
                    "服务端 / 网络抖动，丢弃该段避免阻塞后续。"
                ),
                next_action=(
                    "下一步如何做：该段已丢弃，请保持通话继续；下一段会自动重试。"
                    "若反复触发，请检查网络或换 DeepSeek 接入点。"
                ),
            ) from error
        if isinstance(item, BaseException):
            raise item
        return item

    async def aclose(self) -> None:
        """停止后台 producer；调用方提前截断或 prepare 失败时释放队列。"""
        self._closed.set()
        await asyncio.to_thread(self._drain_pcm_queue)
        try:
            self._pcm_queue.put_nowait(None)
        except queue.Full:
            pass
        await asyncio.to_thread(self._thread.join, 0.2)

    def _run(
        self,
        *,
        source_text: str,
        direction: AudioDirection,
        context_text: str,
        settings: Settings,
        first_byte_timeout_s: float,
        synthesis_timeout_s: float,
    ) -> None:
        asyncio.run(
            self._produce(
                source_text=source_text,
                direction=direction,
                context_text=context_text,
                settings=settings,
                first_byte_timeout_s=first_byte_timeout_s,
                synthesis_timeout_s=synthesis_timeout_s,
            )
        )

    async def _produce(
        self,
        *,
        source_text: str,
        direction: AudioDirection,
        context_text: str,
        settings: Settings,
        first_byte_timeout_s: float,
        synthesis_timeout_s: float,
    ) -> None:
        translation_started = time.perf_counter()
        mt_first_token_at: float | None = None
        pending_text = ""
        emitted_first_piece = False
        first_tts_task: asyncio.Task[None] | None = None
        try:
            iterator = DeepSeekStreamingClient(
                api_key=settings.resolved_deepseek_api_key(),
                model=settings.deepseek_model,
            ).stream_translate(
                source_text,
                direction=direction,
                context_text=context_text,
            )
            async for chunk in iterator:
                if chunk.kind == "delta" and chunk.text:
                    if mt_first_token_at is None:
                        mt_first_token_at = time.perf_counter()
                    pending_text = _join_text_delta(pending_text, chunk.text)
                    if not emitted_first_piece and _is_early_tts_text_ready(pending_text):
                        emitted_first_piece = True
                        first_piece = pending_text.strip()
                        pending_text = ""
                        self._put_ready(
                            _EarlyTranslationReady(
                                target_text=first_piece,
                                translation_latency_s=time.perf_counter() - translation_started,
                                mt_first_token_latency_s=(
                                    mt_first_token_at - translation_started
                                ),
                            )
                        )
                        first_tts_task = asyncio.create_task(
                            self._emit_tts_pcm(
                                first_piece,
                                direction=direction,
                                settings=settings,
                                first_byte_timeout_s=first_byte_timeout_s,
                                synthesis_timeout_s=synthesis_timeout_s,
                            )
                        )
                elif chunk.kind == "completed":
                    if chunk.text and not pending_text and not emitted_first_piece:
                        pending_text = _join_text_delta(pending_text, chunk.text)
                    break
            await self._finish_pending_translation(
                pending_text=pending_text,
                emitted_first_piece=emitted_first_piece,
                mt_first_token_at=mt_first_token_at,
                translation_started=translation_started,
                direction=direction,
                settings=settings,
                first_byte_timeout_s=first_byte_timeout_s,
                synthesis_timeout_s=synthesis_timeout_s,
                first_tts_task=first_tts_task,
            )
        except Exception as error:
            self._put_ready_once(error)
            self._put_pcm(error)
        finally:
            self._put_pcm(None)

    async def _finish_pending_translation(
        self,
        *,
        pending_text: str,
        emitted_first_piece: bool,
        mt_first_token_at: float | None,
        translation_started: float,
        direction: AudioDirection,
        settings: Settings,
        first_byte_timeout_s: float,
        synthesis_timeout_s: float,
        first_tts_task: asyncio.Task[None] | None,
    ) -> None:
        text = pending_text.strip()
        if first_tts_task is not None:
            await first_tts_task
        if not text:
            if not emitted_first_piece:
                raise UserFacingError(
                    code="say.empty_translation",
                    what_happened="发生了什么：DeepSeek 没有返回可播出的译文。",
                    next_action="下一步如何做：请稍后重试，或换一句更短的文本。",
                )
            return
        if not emitted_first_piece:
            mt_first_token_latency_s = 0.0
            if mt_first_token_at is not None:
                mt_first_token_latency_s = mt_first_token_at - translation_started
            self._put_ready(
                _EarlyTranslationReady(
                    target_text=text,
                    translation_latency_s=time.perf_counter() - translation_started,
                    mt_first_token_latency_s=mt_first_token_latency_s,
                )
            )
        await self._emit_tts_pcm(
            text,
            direction=direction,
            settings=settings,
            first_byte_timeout_s=first_byte_timeout_s,
            synthesis_timeout_s=synthesis_timeout_s,
        )

    async def _emit_tts_pcm(
        self,
        text: str,
        *,
        direction: AudioDirection,
        settings: Settings,
        first_byte_timeout_s: float,
        synthesis_timeout_s: float,
    ) -> None:
        async for pcm in stream_pcm_chunks_with_retry(
            target_text=text,
            direction=direction,
            rate=settings.tts_rate,
            settings=settings,
            max_retries=1,
            first_byte_timeout_s=first_byte_timeout_s,
            synthesis_timeout_s=synthesis_timeout_s,
        ):
            if self._closed.is_set() or not self._put_pcm(pcm):
                break

    def _put_ready(self, item: _EarlyTranslationReady) -> None:
        self._ready_queue.put(item)

    def _put_ready_once(self, item: BaseException) -> None:
        try:
            self._ready_queue.put_nowait(item)
        except queue.Full:
            pass

    def _put_pcm(self, item: Int16Array | BaseException | None) -> bool:
        while not self._closed.is_set():
            try:
                self._pcm_queue.put(item, timeout=0.05)
                return True
            except queue.Full:
                continue
        return False

    def _drain_pcm_queue(self) -> None:
        while True:
            try:
                self._pcm_queue.get_nowait()
            except queue.Empty:
                return


def _target_text_from_chunks(chunks: list[TranslationChunk]) -> str:
    """把 DeepSeek delta 分片拼成完整可播出文本。"""
    return "".join(chunk.text for chunk in chunks).strip()


def _join_text_delta(existing: str, delta: str) -> str:
    """按 SSE 原样拼接 MT delta；空格由模型 chunk 自身携带。"""
    return existing + delta


def _is_early_tts_text_ready(text: str) -> bool:
    """首个非空 MT delta 即可启动 TTS；剩余 delta 后续顺序补播。"""
    return bool(text.strip())


def _preview_text(text: str, *, max_length: int = 80) -> str:
    """生成安全的单行文本预览。"""
    compact = " ".join(text.split())
    if len(compact) <= max_length:
        return compact
    return f"{compact[:max_length]}..."


async def _decode_tts_events_to_pcm16(events: list[TTSEvent]) -> Int16Array:
    """把任意 backend 的 TTS events 解码成 live_say 的 16 kHz PCM。"""
    audio_events = [event for event in events if event.audio_chunk]
    if not audio_events:
        return np.array([], dtype=np.int16)

    async def audio_chunks() -> AsyncIterator[bytes]:
        for event in audio_events:
            yield event.audio_chunk

    audio_format = audio_events[0].audio_format
    if audio_format == "mp3":
        pcm_chunks = [chunk async for chunk in decode_mp3_stream_to_pcm16(audio_chunks())]
    elif audio_format.startswith("pcm_s16le_"):
        source_rate = int(audio_format.removeprefix("pcm_s16le_"))
        pcm_chunks = [
            chunk
            async for chunk in decode_pcm_stream_to_pcm16(
                audio_chunks(),
                source_sample_rate_hz=source_rate,
            )
        ]
    else:
        raise UserFacingError(
            code="tts.unsupported_audio_format",
            what_happened=f"发生了什么：未知 TTS audio_format `{audio_format}`。",
            next_action=(
                "下一步如何做：检查 TTS backend 是否声明了正确的 audio_format；"
                "当前支持 `mp3` 与 `pcm_s16le_<sample_rate>`。"
            ),
        )

    if not pcm_chunks:
        return np.array([], dtype=np.int16)
    return np.concatenate(pcm_chunks).astype(np.int16)


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
