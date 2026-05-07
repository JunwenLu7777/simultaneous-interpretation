"""Push-to-talk 语音入口。"""

from __future__ import annotations

import queue
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal, Protocol
from uuid import UUID, uuid4

import numpy as np
import sounddevice as sd
from pywhispercpp.model import Model

from teams_voice_interpreter.audio.capture import BlackHoleReader
from teams_voice_interpreter.audio.resample import resample_int16_mono as _resample_int16_mono
from teams_voice_interpreter.audio.routing import AudioDevice, AudioDeviceProbe
from teams_voice_interpreter.config import load_settings
from teams_voice_interpreter.data.audio_segment import AudioDirection
from teams_voice_interpreter.data.transcript import StableTranscriptChunk, TranscriptKind
from teams_voice_interpreter.errors import UserFacingError
from teams_voice_interpreter.live_say import LiveSayBridge, SayResult
from teams_voice_interpreter.stt.vad import VadBackendProtocol, VadSegmenter, WebRtcBackend
from teams_voice_interpreter.stt.whisper_streaming import LocalAgreementCommitter

BLANK_TRANSCRIPT_MARKERS = {"[BLANK_AUDIO]", "[NO_SPEECH]", "[NO SPEECH]"}
HALLUCINATION_MARKERS = frozenset(
    {
        "[BLANK_AUDIO]",
        "[NO_SPEECH]",
        "[NO SPEECH]",
        "[MUSIC]",
        "[NOISE]",
        "[INAUDIBLE]",
        "[SILENCE]",
        "(MUSIC)",
        "(NOISE)",
        "(SILENCE)",
        "(BEEP)",
        "*PHONE RINGS*",
        "*PHONE RINGING*",
        "*RINGING*",
        "*BEEP*",
        "*MUSIC*",
        "*NOISE*",
        "IN CHINESE",
        "IN ENGLISH",
        "TRANSLATION",
        "TRANSLATION:",
        "音声",
        "声音声",
        "音聲",
        "聲音聲",
    }
)
HALLUCINATION_PREFIX_PATTERNS: frozenset[str] = frozenset(
    {
        "字幕",
        "字幕组",
        "字幕組",
        "謝謝觀看",
        "谢谢观看",
        "感謝觀看",
        "感谢观看",
        "请订阅",
        "請訂閱",
        "请不吝点赞",
        "請不吝點讚",
        "请大家点赞",
        "請大家點讚",
        "请大家订阅",
        "請大家訂閱",
        "请看视频",
        "請看視頻",
        "如果您喜欢",
        "如果你喜欢",
        "记得点赞",
        "记得订阅",
        "打赏支持",
        "感谢您的观看",
        "感謝您的觀看",
        "明镜与点点",
        "明鏡與點點",
        # 路径 b 扩充：sachaarbonel/whisper-hallucinations 数据集与真测高频幻觉
        "欢迎订阅",
        "歡迎訂閱",
        "欢迎收看",
        "歡迎收看",
        "欢迎收听",
        "歡迎收聽",
        "订阅我们",
        "訂閱我們",
        "订阅频道",
        "訂閱頻道",
        "关注我们",
        "關注我們",
        "点击关注",
        "點擊關注",
        "点击订阅",
        "點擊訂閱",
        "一键三连",
        "一鍵三連",
        "三连支持",
        "三連支持",
        "本期视频",
        "本期節目",
        "本视频",
        "本視頻",
        "以上是本期",
        "Subtitles by",
        "Thanks for watching",
        "Please subscribe",
        "Like and subscribe",
        "Don't forget to subscribe",
        "Don't forget to hit",
        "See you next",
        "See you in the next",
        "Hit the bell",
        "Smash that like",
        "If you enjoyed this",
        "If you liked this",
    }
)
InputSource = Literal["default_input", "blackhole"]
SpeechCloseReason = Literal["silence", "max_length", "flush"]


class WhisperSegmentLike(Protocol):
    """Whisper 识别片段的最小协议。"""

    text: object


@dataclass
class _StableChunkStreamState:
    """一次 pywhispercpp callback 流的 LocalAgreement 状态。"""

    direction: AudioDirection
    provider_model: str
    segment_id: UUID = field(default_factory=uuid4)
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    committer: LocalAgreementCommitter = field(default_factory=LocalAgreementCommitter)


@dataclass(frozen=True)
class SpeechSegment:
    """VAD 输出的语音段及其与上一段的连续关系。"""

    samples: np.ndarray
    continues_previous: bool
    closed_by: SpeechCloseReason


@dataclass
class MicrophoneRecorder:
    """用 sounddevice 从默认麦克风录制一小段 PCM。"""

    sample_rate_hz: int = 16000
    device_probe: AudioDeviceProbe | None = None

    def record(self, *, seconds: float) -> np.ndarray:
        """阻塞录制指定秒数，并返回 mono int16 PCM。"""
        if seconds <= 0:
            raise UserFacingError(
                code="ptt.seconds_invalid",
                what_happened="发生了什么：录音时长必须大于 0 秒。",
                next_action="下一步如何做：请使用 `--seconds 3` 这类正数。",
            )
        probe = self.device_probe or AudioDeviceProbe()
        device = probe.get_default_input()
        input_sample_rate_hz = _device_default_sample_rate_hz(
            device_index=device.index,
            fallback_sample_rate_hz=self.sample_rate_hz,
        )
        frames = int(seconds * input_sample_rate_hz)
        recording = sd.rec(
            frames,
            samplerate=input_sample_rate_hz,
            channels=1,
            dtype="int16",
            device=device.index,
        )
        sd.wait()
        samples = np.asarray(recording, dtype=np.int16).reshape(-1)
        return _resample_int16_mono(
            samples,
            source_rate_hz=input_sample_rate_hz,
            target_rate_hz=self.sample_rate_hz,
        )


@dataclass
class StreamingAudioRecorder:
    """后台持续采集输入设备，并按固定时间片吐出 16 kHz mono PCM。"""

    sample_rate_hz: int = 16000
    device_probe: AudioDeviceProbe | None = None
    input_source: InputSource = "default_input"
    input_device_name: str = ""

    def chunks(
        self,
        *,
        chunk_seconds: float,
        max_chunks: int | None = None,
    ) -> Iterable[np.ndarray]:
        """连续采集音频块；调用方处理上一块时，输入流仍在后台继续填充队列。"""
        if chunk_seconds <= 0:
            raise UserFacingError(
                code="listen.chunk_seconds_invalid",
                what_happened="发生了什么：连续监听的分片时长必须大于 0 秒。",
                next_action="下一步如何做：请使用 `--chunk-seconds 6` 这类正数。",
            )
        probe = self.device_probe or AudioDeviceProbe()
        device = self._input_device(probe)
        input_sample_rate_hz = _device_default_sample_rate_hz(
            device_index=device.index,
            fallback_sample_rate_hz=self.sample_rate_hz,
        )
        channels = self._input_channels(device)
        chunk_frames = int(chunk_seconds * input_sample_rate_hz)
        audio_queue: queue.Queue[np.ndarray] = queue.Queue()

        def callback(
            indata: np.ndarray,
            frames: int,
            time: object,
            status: object,
        ) -> None:
            del frames, time, status
            audio_queue.put(self._to_mono(indata).copy())

        emitted = 0
        buffer = np.array([], dtype=np.int16)
        with sd.InputStream(
            samplerate=input_sample_rate_hz,
            channels=channels,
            dtype="int16",
            device=device.index,
            callback=callback,
        ):
            while max_chunks is None or emitted < max_chunks:
                while buffer.size < chunk_frames:
                    buffer = np.concatenate([buffer, audio_queue.get()])
                chunk = buffer[:chunk_frames]
                buffer = buffer[chunk_frames:]
                emitted += 1
                yield _resample_int16_mono(
                    chunk,
                    source_rate_hz=input_sample_rate_hz,
                    target_rate_hz=self.sample_rate_hz,
                )

    def segments(
        self,
        *,
        max_segment_seconds: float,
        end_silence_ms: int = 700,
        min_speech_ms: int = 600,
        overlap_seconds: float = 1.0,
        frame_ms: int = 30,
        rms_threshold: float = 160.0,
        max_segments: int | None = None,
        vad_backend: VadBackendProtocol | None = None,
    ) -> Iterable[np.ndarray]:
        """按 VAD + 尾部静音 + 最大窗口输出稳定语音段。"""
        for segment in self.speech_segments(
            max_segment_seconds=max_segment_seconds,
            end_silence_ms=end_silence_ms,
            min_speech_ms=min_speech_ms,
            overlap_seconds=overlap_seconds,
            frame_ms=frame_ms,
            rms_threshold=rms_threshold,
            max_segments=max_segments,
            vad_backend=vad_backend,
        ):
            yield segment.samples

    def speech_segments(
        self,
        *,
        max_segment_seconds: float,
        end_silence_ms: int = 700,
        min_speech_ms: int = 600,
        overlap_seconds: float = 1.0,
        frame_ms: int = 30,
        rms_threshold: float = 160.0,
        max_segments: int | None = None,
        vad_backend: VadBackendProtocol | None = None,
    ) -> Iterable[SpeechSegment]:
        """输出带连续关系的语音段，供下游按真实切段原因保留长句 burst。

        vad_backend 决定每帧的 sample 数（webrtc 30 ms / silero 32 ms）；外部 frame_ms
        参数仅在 vad_backend 为 None 且需要回退到旧行为时使用。
        """
        del frame_ms  # 现统一由 vad_backend.frame_samples 决定，旧参数保留接口兼容
        if max_segment_seconds <= 0:
            raise UserFacingError(
                code="listen.max_segment_seconds_invalid",
                what_happened="发生了什么：连续监听的最大分段时长必须大于 0 秒。",
                next_action="下一步如何做：请使用 `--chunk-seconds 8` 这类正数。",
            )
        if end_silence_ms <= 0 or min_speech_ms <= 0:
            raise UserFacingError(
                code="listen.vad_timing_invalid",
                what_happened="发生了什么：VAD 静音和最短人声阈值必须大于 0。",
                next_action="下一步如何做：请使用正数，例如 `--end-silence-ms 700`。",
            )
        if vad_backend is None:
            vad_backend = WebRtcBackend()
        actual_frame_ms = round(vad_backend.frame_samples * 1000 / vad_backend.sample_rate_hz)
        segmenter = _StableSpeechSegmenter(
            overlap_frames=max(0, int(overlap_seconds * 1000 / actual_frame_ms)),
            end_silence_frames=max(1, end_silence_ms // actual_frame_ms),
            min_speech_frames=max(1, min_speech_ms // actual_frame_ms),
            max_segment_frames=max(1, int(max_segment_seconds * 1000 / actual_frame_ms)),
        )
        vad = VadSegmenter(backend=vad_backend, sample_rate_hz=self.sample_rate_hz)
        emitted = 0
        previous_closed_by_max_length = False
        boundary_silence_frames = 0

        for frame in self.chunks(chunk_seconds=actual_frame_ms / 1000):
            is_speech = _is_speech_frame(frame, vad=vad, rms_threshold=rms_threshold)
            segment = segmenter.accept(
                frame,
                is_speech=is_speech,
            )
            if segment is None:
                previous_closed_by_max_length, boundary_silence_frames = (
                    _update_forced_split_continuation_on_open_frame(
                        segmenter=segmenter,
                        previous_closed_by_max_length=previous_closed_by_max_length,
                        boundary_silence_frames=boundary_silence_frames,
                        is_speech=is_speech,
                    )
                )
                continue
            emitted += 1
            close_reason = segmenter.last_close_reason or "silence"
            yield SpeechSegment(
                samples=segment,
                continues_previous=previous_closed_by_max_length,
                closed_by=close_reason,
            )
            previous_closed_by_max_length = close_reason == "max_length"
            boundary_silence_frames = 0
            if max_segments is not None and emitted >= max_segments:
                return
        segment = segmenter.flush()
        if segment is not None:
            yield SpeechSegment(
                samples=segment,
                continues_previous=previous_closed_by_max_length,
                closed_by=segmenter.last_close_reason or "flush",
            )

    def _input_device(self, probe: AudioDeviceProbe) -> AudioDevice:
        if self.input_source == "blackhole":
            if self.input_device_name:
                return probe.find_input_device_by_name(self.input_device_name, min_channels=2)
            return probe.find_blackhole_2ch()
        if self.input_device_name:
            return probe.find_input_device_by_name(self.input_device_name)
        return probe.get_default_input()

    def _input_channels(self, device: AudioDevice) -> int:
        if self.input_source == "blackhole":
            return 2
        return max(1, min(device.max_input_channels, 1))

    def _to_mono(self, indata: np.ndarray) -> np.ndarray:
        samples = np.asarray(indata, dtype=np.int16)
        if self.input_source == "blackhole":
            return BlackHoleReader(sample_rate_hz=self.sample_rate_hz).downmix_stereo(samples)
        return samples.reshape(-1)


class StreamingMicrophoneRecorder(StreamingAudioRecorder):
    """后台持续采集默认或显式指定的真实麦克风。"""

    def __init__(
        self,
        *,
        sample_rate_hz: int = 16000,
        device_probe: AudioDeviceProbe | None = None,
        device_name: str = "",
    ) -> None:
        super().__init__(
            sample_rate_hz=sample_rate_hz,
            device_probe=device_probe,
            input_source="default_input",
            input_device_name=device_name,
        )


class StreamingBlackHoleRecorder(StreamingAudioRecorder):
    """后台持续采集 BlackHole 2ch 输入，用于 Teams 下行音频。"""

    def __init__(
        self,
        *,
        sample_rate_hz: int = 16000,
        device_probe: AudioDeviceProbe | None = None,
        device_name: str = "",
    ) -> None:
        super().__init__(
            sample_rate_hz=sample_rate_hz,
            device_probe=device_probe,
            input_source="blackhole",
            input_device_name=device_name,
        )


@dataclass
class _StableSpeechSegmenter:
    """把 VAD 帧合并成可送 ASR 的稳定语音段。"""

    overlap_frames: int
    end_silence_frames: int
    min_speech_frames: int
    max_segment_frames: int
    pre_roll: deque[np.ndarray] = field(init=False)
    segment_frames: list[np.ndarray] = field(default_factory=list)
    speech_frames: int = 0
    silent_frames: int = 0
    last_close_reason: SpeechCloseReason | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        self.pre_roll = deque(maxlen=self.overlap_frames)

    def accept(self, frame: np.ndarray, *, is_speech: bool) -> np.ndarray | None:
        """接收一帧，必要时返回一个稳定语音段。"""
        if not self.segment_frames:
            self._start_or_buffer(frame, is_speech=is_speech)
            return None
        self.segment_frames.append(frame)
        self._count_frame(is_speech=is_speech)
        if self._should_discard_as_noise():
            self._discard_segment()
            return None
        close_reason = self._close_reason()
        if close_reason is None:
            return None
        return self._close_segment(reason=close_reason)

    def flush(self) -> np.ndarray | None:
        """输入流结束时收口已满足最短人声要求的尾段，避免尾巴静默丢失。"""
        if not self.segment_frames:
            return None
        if self.speech_frames < self.min_speech_frames:
            self._discard_segment()
            return None
        return self._close_segment(reason="flush")

    def _start_or_buffer(self, frame: np.ndarray, *, is_speech: bool) -> None:
        if not is_speech:
            self.pre_roll.append(frame)
            return
        self.segment_frames = list(self.pre_roll)
        self.segment_frames.append(frame)
        self.speech_frames = 1
        self.silent_frames = 0

    def _count_frame(self, *, is_speech: bool) -> None:
        if is_speech:
            self.speech_frames += 1
            self.silent_frames = 0
            return
        self.silent_frames += 1

    def _should_discard_as_noise(self) -> bool:
        return (
            self.speech_frames < self.min_speech_frames
            and self.silent_frames >= self.end_silence_frames
        )

    def _close_reason(self) -> SpeechCloseReason | None:
        has_enough_speech = self.speech_frames >= self.min_speech_frames
        has_tail_silence = self.silent_frames >= self.end_silence_frames
        reached_max_length = len(self.segment_frames) >= self.max_segment_frames
        if not has_enough_speech:
            return None
        if has_tail_silence:
            return "silence"
        if reached_max_length:
            return "max_length"
        return None

    def _discard_segment(self) -> None:
        self.pre_roll.extend(self._carry_frames(self.segment_frames))
        self.last_close_reason = None
        self._reset()

    def _close_segment(self, *, reason: SpeechCloseReason) -> np.ndarray:
        stable_frames = self._stable_frames()
        segment = _concat_frames(stable_frames)
        self.pre_roll = deque(self._carry_frames(stable_frames), maxlen=self.overlap_frames)
        self.last_close_reason = reason
        self._reset()
        return segment

    def _stable_frames(self) -> list[np.ndarray]:
        if self.silent_frames >= self.end_silence_frames:
            return self.segment_frames[: -self.silent_frames]
        return self.segment_frames

    def _carry_frames(self, frames: list[np.ndarray]) -> list[np.ndarray]:
        if not self.overlap_frames:
            return []
        return frames[-self.overlap_frames :]

    def _reset(self) -> None:
        self.segment_frames = []
        self.speech_frames = 0
        self.silent_frames = 0


def _update_forced_split_continuation_on_open_frame(
    *,
    segmenter: _StableSpeechSegmenter,
    previous_closed_by_max_length: bool,
    boundary_silence_frames: int,
    is_speech: bool,
) -> tuple[bool, int]:
    if not previous_closed_by_max_length:
        return False, 0
    if segmenter.segment_frames or is_speech:
        return True, 0
    boundary_silence_frames += 1
    if boundary_silence_frames >= segmenter.end_silence_frames:
        return False, 0
    return True, boundary_silence_frames


class WhisperOneShotTranscriber:
    """用 pywhispercpp 做一次短语音识别。"""

    def __init__(
        self,
        *,
        model_name: str,
        language: str = "zh",
        initial_prompt: str = "",
    ) -> None:
        self.model_name = model_name
        self.language = language
        self.initial_prompt = initial_prompt
        self._model = Model(self.model_name)

    def transcribe(
        self,
        samples: np.ndarray,
        *,
        stable_chunk_callback: Callable[[StableTranscriptChunk], None] | None = None,
    ) -> str:
        """识别一段 16 kHz mono PCM。"""
        if samples.size == 0:
            raise UserFacingError(
                code="ptt.empty_audio",
                what_happened="发生了什么：没有录到可识别的音频。",
                next_action="下一步如何做：请确认麦克风权限和输入音量后重试。",
            )
        audio = samples.astype(np.float32) / 32768.0
        params: dict[str, object] = {
            "language": self.language,
            "no_context": True,
            "initial_prompt": self.initial_prompt,
            "print_progress": False,
        }
        stream_state = _StableChunkStreamState(
            direction=AudioDirection.UPLINK if self.language == "zh" else AudioDirection.DOWNLINK,
            provider_model=self.model_name,
        )
        observed_segments: list[WhisperSegmentLike] = []
        if stable_chunk_callback is not None:

            def on_new_segment(segment: WhisperSegmentLike) -> None:
                observed_segments.append(segment)
                _emit_partial_stable_chunk(
                    observed_segments,
                    stream_state=stream_state,
                    callback=stable_chunk_callback,
                )

            params["new_segment_callback"] = on_new_segment
        segments = self._model.transcribe(audio, **params)
        text = _transcript_text_from_segments(segments)
        if not text:
            raise UserFacingError(
                code="ptt.empty_transcript",
                what_happened="发生了什么：Whisper 没有识别到文本。",
                next_action="下一步如何做：请靠近麦克风重试，或增加 `--seconds` 时长。",
            )
        if stable_chunk_callback is not None:
            _emit_final_stable_chunk(
                text,
                stream_state=stream_state,
                callback=stable_chunk_callback,
            )
        return text


def _emit_partial_stable_chunk(
    segments: Iterable[WhisperSegmentLike],
    *,
    stream_state: _StableChunkStreamState,
    callback: Callable[[StableTranscriptChunk], None],
) -> None:
    try:
        cumulative_text = _transcript_text_from_segments(segments)
    except UserFacingError:
        return
    delta_text = stream_state.committer.accept_partial(cumulative_text)
    if not delta_text:
        return
    callback(
        StableTranscriptChunk(
            segment_id=stream_state.segment_id,
            direction=stream_state.direction,
            kind=TranscriptKind.PARTIAL,
            started_at=stream_state.started_at,
            text=stream_state.committer.committed_text,
            delta_text=delta_text,
            confidence=0.82,
            provider_model=stream_state.provider_model,
        )
    )


def _emit_final_stable_chunk(
    text: str,
    *,
    stream_state: _StableChunkStreamState,
    callback: Callable[[StableTranscriptChunk], None],
) -> None:
    final = stream_state.committer.accept_final(text)
    if not final.text:
        return
    callback(
        StableTranscriptChunk(
            segment_id=stream_state.segment_id,
            direction=stream_state.direction,
            kind=TranscriptKind.FINAL,
            started_at=stream_state.started_at,
            ended_at=datetime.now(UTC),
            text=final.text,
            delta_text=final.delta_text,
            confidence=0.95,
            revision=final.revision,
            provider_model=stream_state.provider_model,
        )
    )


class LivePushToTalkBridge:
    """录音 -> Whisper -> DeepSeek/Edge-TTS -> BlackHole 的一次性桥。"""

    def __init__(
        self,
        *,
        source_language: str = "zh",
        recorder: MicrophoneRecorder | None = None,
        transcriber: WhisperOneShotTranscriber | None = None,
        say_bridge: LiveSayBridge | None = None,
    ) -> None:
        settings = load_settings(validate_credentials=False)
        self.recorder = recorder or MicrophoneRecorder()
        self.transcriber = transcriber or WhisperOneShotTranscriber(
            model_name=settings.resolved_whisper_model_name(),
            language=source_language,
            initial_prompt=settings.asr_initial_prompt,
        )
        self.say_bridge = say_bridge or LiveSayBridge()

    def close(self) -> None:
        """释放下游翻译/TTS 桥持有的后台资源。"""
        close = getattr(self.say_bridge, "close", None)
        if callable(close):
            close()

    async def run(
        self,
        *,
        seconds: float,
        direction: AudioDirection,
        target: str,
    ) -> SayResult:
        """执行一次 push-to-talk。"""
        samples = self.recorder.record(seconds=seconds)
        text = self.transcriber.transcribe(samples)
        return await self.say_bridge.say(text, direction=direction, target=target)


def _transcript_text_from_segments(segments: Iterable[WhisperSegmentLike]) -> str:
    """把 Whisper 片段转成可翻译文本，并阻断空音频/音效幻觉占位。"""
    text = "".join(str(segment.text).strip() for segment in segments).strip()
    if not text:
        return text
    if text.upper() in BLANK_TRANSCRIPT_MARKERS:
        raise UserFacingError(
            code="ptt.blank_audio",
            what_happened="发生了什么：麦克风录到的是空音频。",
            next_action="下一步如何做：请在提示开始录音后立刻说话，或把 `--seconds` 增加到 5。",
        )
    if _looks_like_hallucination(text):
        raise UserFacingError(
            code="ptt.hallucinated_transcript",
            what_happened=(
                f"发生了什么：识别到的内容 `{text}` 长度过短或像 Whisper 在静音/噪声段产生的幻觉，"
                "已丢弃以避免胡乱翻译。"
            ),
            next_action=(
                "下一步如何做：请说一句更完整的话再试；若反复出现，"
                "可调高 `--speech-rms-threshold` 减少噪声触发。"
            ),
        )
    return text


def _looks_like_hallucination(text: str) -> bool:
    """识别 Whisper 在静音/噪声段上的常见幻觉占位。

    覆盖五类：音效标签、纯标点、过短输出、量化解码 N-gram 重复、训练集片头/片尾。
    """
    stripped = text.strip()
    if not stripped:
        return False
    if stripped in HALLUCINATION_MARKERS or stripped.upper() in HALLUCINATION_MARKERS:
        return True
    bare = stripped.strip("()[]<>*\"' ").strip()
    if not bare:
        return True
    if bare in HALLUCINATION_MARKERS or bare.upper() in HALLUCINATION_MARKERS:
        return True
    if len(bare) <= 1:
        return True
    has_letter = any(c.isalpha() for c in bare)
    has_chinese = any("一" <= c <= "鿿" for c in bare)
    if not has_letter and not has_chinese:
        return True
    if _starts_with_known_hallucination_prefix(bare):
        return True
    if _has_runaway_repeats(bare):
        return True
    return False


def _starts_with_known_hallucination_prefix(text: str) -> bool:
    """匹配 Whisper 训练集里高频出现的字幕 / 片尾 / 订阅提示前缀。"""
    lowered = text.lower()
    for prefix in HALLUCINATION_PREFIX_PATTERNS:
        if text.startswith(prefix) or lowered.startswith(prefix.lower()):
            return True
    return False


def _has_runaway_repeats(text: str) -> bool:
    """检测 Whisper.cpp 在量化模型上常见的解码重复模式。

    两条规则任一命中即视为幻觉：
    1. 长度 ≥ 2 的子串在文本中**连续重复 ≥ 3 次**（例：直接直接直接、性能。性能。性能。）。
    2. 中文字符 ≥ 4 个时，唯一中文字符占比 < 50%（例：性格的性格的性格 / 嗯嗯嗯嗯）。
    """
    stripped = text.strip()
    if not stripped:
        return False

    for unit_length in range(2, len(stripped) // 3 + 1):
        repeat_window = unit_length * 3
        for start in range(len(stripped) - repeat_window + 1):
            unit = stripped[start : start + unit_length]
            if not unit.strip():
                continue
            if stripped[start : start + repeat_window] == unit * 3:
                return True

    chinese_chars = [c for c in stripped if "一" <= c <= "鿿"]
    if len(chinese_chars) >= 5:
        unique_ratio = len(set(chinese_chars)) / len(chinese_chars)
        if unique_ratio < 0.5:
            return True

    return False


def _device_default_sample_rate_hz(
    *,
    device_index: int,
    fallback_sample_rate_hz: int,
) -> int:
    """读取输入设备原生采样率，失败时回落到 Whisper 目标采样率。"""
    device_info = sd.query_devices(device_index)
    try:
        sample_rate_hz = int(float(device_info["default_samplerate"]))
    except (KeyError, TypeError, ValueError):
        return fallback_sample_rate_hz
    if sample_rate_hz <= 0:
        return fallback_sample_rate_hz
    return sample_rate_hz


def _is_speech_frame(
    frame: np.ndarray,
    *,
    vad: VadSegmenter,
    rms_threshold: float,
) -> bool:
    """RMS 与 WebRTC VAD 双门：必须同时通过才算人声。

    取消了"高能量自动放行"的旁路——Teams 提示音、键盘敲击、风扇噪声等高能量但非人声
    的输入会被 VAD 拒绝，从而避免 Whisper 在这些段上吐出 `*phone rings*` / `恶意!`
    这类幻觉占位再被 DeepSeek 编造成完整句。
    """
    samples = np.asarray(frame, dtype=np.int16).reshape(-1)
    rms = _frame_rms(samples)
    if rms < rms_threshold / 2:
        return False
    return vad.accept(samples).is_speech


def _frame_rms(samples: np.ndarray) -> float:
    frame = np.asarray(samples, dtype=np.int16).reshape(-1)
    if frame.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))


def _concat_frames(frames: Iterable[np.ndarray]) -> np.ndarray:
    items = [np.asarray(frame, dtype=np.int16).reshape(-1) for frame in frames]
    if not items:
        return np.array([], dtype=np.int16)
    return np.concatenate(items).astype(np.int16)
