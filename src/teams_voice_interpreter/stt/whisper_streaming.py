"""Whisper.cpp 流式 wrapper 的本地可测试实现。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from teams_voice_interpreter.audio.capture import AudioFrame
from teams_voice_interpreter.data.audio_segment import AudioDirection
from teams_voice_interpreter.data.transcript import TranscriptKind, TranscriptSegment


@dataclass(frozen=True)
class WhisperStreamingConfig:
    """Whisper streaming 基本配置。"""

    model_name: str = "ggml-small-q5_0"
    language: str = "zh"
    step_ms: int = 300
    context_seconds: int = 5
    metal: bool = True
    core_ml: bool = True


class WhisperStreamingWrapper:
    """把音频帧转换为 partial/final 片段的边界封装。"""

    def __init__(self, config: WhisperStreamingConfig | None = None) -> None:
        self.config = config or WhisperStreamingConfig()
        self.loaded = False

    def load_model(self) -> None:
        """加载模型；当前测试实现只记录状态。"""
        self.loaded = True

    def transcribe_frames(
        self,
        frames: list[AudioFrame],
        *,
        direction: AudioDirection,
        fixture_text: str | None = None,
    ) -> list[TranscriptSegment]:
        """返回一个 partial 和一个 final，保持与真实流式顺序一致。"""
        if not self.loaded:
            self.load_model()
        text = fixture_text or (
            "你好，我们开始会议。" if direction is AudioDirection.UPLINK else "hello team"
        )
        now = datetime.now(UTC)
        segment_id = uuid4()
        partial_text = text[: max(1, len(text) // 2)]
        return [
            TranscriptSegment(
                segment_id=segment_id,
                direction=direction,
                kind=TranscriptKind.PARTIAL,
                started_at=now,
                text=partial_text,
                confidence=0.8,
                provider_model=self.config.model_name,
            ),
            TranscriptSegment(
                segment_id=segment_id,
                direction=direction,
                kind=TranscriptKind.FINAL,
                started_at=now,
                ended_at=datetime.now(UTC),
                text=text,
                confidence=0.95 if frames else 0.75,
                provider_model=self.config.model_name,
            ),
        ]


def choose_model_for_budget(*, measured_ram_mb: float, measured_wer_delta: float) -> str:
    """根据资源与准确率预算选择模型档位。"""
    if measured_ram_mb <= 500 or measured_wer_delta < 5:
        return "ggml-tiny"
    return "ggml-small-q5_0"
