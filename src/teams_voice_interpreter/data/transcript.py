"""识别片段与翻译片段模型。"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from teams_voice_interpreter.data.audio_segment import AudioDirection


class TranscriptKind(StrEnum):
    """识别片段类型。"""

    PARTIAL = "partial"
    FINAL = "final"


class TranscriptSegment(BaseModel):
    """STT 输出的一段 partial 或 final 文本。"""

    segment_id: UUID
    direction: AudioDirection
    kind: TranscriptKind
    started_at: datetime
    ended_at: datetime | None = None
    text: str
    confidence: float = Field(ge=0.0, le=1.0)
    provider: Literal["whisper.cpp"] = "whisper.cpp"
    provider_model: str = "small-q5_1"

    @model_validator(mode="after")
    def validate_final_segment(self) -> TranscriptSegment:
        if self.kind is TranscriptKind.FINAL and (self.ended_at is None or not self.text.strip()):
            msg = "final transcript requires ended_at and non-empty text"
            raise ValueError(msg)
        return self


class StableTranscriptChunk(BaseModel):
    """LocalAgreement 输出的稳定增量；`text` 是累计全文，`delta_text` 是本次新增。"""

    segment_id: UUID
    direction: AudioDirection
    kind: TranscriptKind
    started_at: datetime
    ended_at: datetime | None = None
    text: str
    delta_text: str = ""
    confidence: float = Field(ge=0.0, le=1.0)
    revision: bool = False
    provider: Literal["whisper.cpp"] = "whisper.cpp"
    provider_model: str = "small-q5_1"

    @model_validator(mode="after")
    def validate_stable_chunk(self) -> StableTranscriptChunk:
        if not self.text.strip():
            msg = "stable transcript chunk requires non-empty cumulative text"
            raise ValueError(msg)
        if self.kind is TranscriptKind.PARTIAL:
            if not self.delta_text.strip():
                msg = "partial stable transcript chunk requires non-empty delta_text"
                raise ValueError(msg)
            if self.revision:
                msg = "partial stable transcript chunk cannot be a revision"
                raise ValueError(msg)
        if self.kind is TranscriptKind.FINAL and self.ended_at is None:
            msg = "final stable transcript chunk requires ended_at"
            raise ValueError(msg)
        if self.revision and self.kind is not TranscriptKind.FINAL:
            msg = "only final stable transcript chunk can be a revision"
            raise ValueError(msg)
        return self


class TranslationSegment(BaseModel):
    """DeepSeek 翻译输出的一段译文。"""

    segment_id: UUID
    source_segment_id: UUID
    direction: AudioDirection
    started_at: datetime
    first_token_at: datetime | None = None
    completed_at: datetime | None = None
    target_text: str
    target_language: Literal["zh", "en"]
    provider: Literal["deepseek"] = "deepseek"
    provider_model: str = "deepseek-chat"
    glossary_hit_count: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_target_language(self) -> TranslationSegment:
        expected = "en" if self.direction is AudioDirection.UPLINK else "zh"
        if self.target_language != expected:
            msg = f"{self.direction.value} target_language must be {expected}"
            raise ValueError(msg)
        return self
