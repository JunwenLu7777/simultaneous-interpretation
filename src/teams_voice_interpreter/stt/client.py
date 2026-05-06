"""Whisper 子进程客户端的可测试边界。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from teams_voice_interpreter.audio.capture import AudioFrame
from teams_voice_interpreter.data.audio_segment import AudioDirection
from teams_voice_interpreter.data.transcript import StableTranscriptChunk, TranscriptSegment
from teams_voice_interpreter.errors import WhisperError
from teams_voice_interpreter.stt.whisper_streaming import (
    OnlineASRProcessor,
    TranscribeBuffer,
    WhisperStreamingConfig,
    WhisperStreamingWrapper,
)


@dataclass(frozen=True)
class WhisperStatusEvent:
    """Whisper retry / failure 状态事件。"""

    kind: str
    direction: AudioDirection
    message: str
    emitted_at: datetime


class WhisperClient:
    """管理一路 Whisper streaming 实例。"""

    def __init__(self, config: WhisperStreamingConfig | None = None) -> None:
        self.wrapper = WhisperStreamingWrapper(config)
        self.last_heartbeat_at = datetime.now(UTC)

    def recognize(
        self,
        frames: list[AudioFrame],
        *,
        direction: AudioDirection,
        fixture_text: str | None = None,
    ) -> list[TranscriptSegment]:
        """识别音频帧并返回流式片段。"""
        self.last_heartbeat_at = datetime.now(UTC)
        return self.wrapper.transcribe_frames(
            frames,
            direction=direction,
            fixture_text=fixture_text,
        )

    def start_online(
        self,
        *,
        direction: AudioDirection,
        transcribe_buffer: TranscribeBuffer,
    ) -> OnlineASRProcessor:
        """创建一路可持续 feed 的在线 ASR processor。"""
        self.last_heartbeat_at = datetime.now(UTC)
        return OnlineASRProcessor(
            direction=direction,
            transcribe_buffer=transcribe_buffer,
            config=self.wrapper.config,
        )

    def close_online_segment(
        self,
        processor: OnlineASRProcessor,
        *,
        final_text: str | None = None,
    ) -> list[StableTranscriptChunk]:
        """收口在线 ASR segment，并刷新 heartbeat。"""
        self.last_heartbeat_at = datetime.now(UTC)
        return processor.close_segment(final_text=final_text)

    def handle_stream_interruption(self, *, direction: AudioDirection) -> list[WhisperStatusEvent]:
        """STT 流中断时先推 retry 状态，再推最终失败占位。"""
        now = datetime.now(UTC)
        return [
            WhisperStatusEvent("retrying", direction, "Whisper 流暂时不可用，正在重试。", now),
            WhisperStatusEvent("failed", direction, "Whisper 30 秒内未恢复，该方向已停止。", now),
        ]

    def require_heartbeat(self, *, timeout_seconds: float = 3.0) -> None:
        """检查 heartbeat 是否超时。"""
        elapsed = (datetime.now(UTC) - self.last_heartbeat_at).total_seconds()
        if elapsed > timeout_seconds:
            raise WhisperError(
                code="stt.heartbeat_timeout",
                what_happened="发生了什么：Whisper 子进程 heartbeat 超时。",
                next_action="下一步如何做：系统将尝试重启该方向的 STT 子进程。",
            )
