"""延迟样本与滚动统计快照模型。"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field

from teams_voice_interpreter.data.audio_segment import AudioDirection


class LatencyStage(StrEnum):
    """同传链路各阶段延迟。"""

    AUDIO_CAPTURE = "audio_capture"
    STT_PARTIAL = "stt_partial"
    STT_FINAL = "stt_final"
    MT_FIRST_TOKEN = "mt_first_token"
    MT_COMPLETED = "mt_completed"
    TTS_FIRST_BYTE = "tts_first_byte"
    TTS_COMPLETED = "tts_completed"
    AUDIO_ROUTE = "audio_route"
    E2E_FIRST_SEG = "e2e_first_segment"
    E2E_FULL = "e2e_full"


class LatencySample(BaseModel):
    """一条可追溯到方向和阶段的延迟样本。"""

    stage: LatencyStage
    direction: AudioDirection
    duration_ms: float = Field(ge=0)
    measured_at: datetime
    associated_segment_id: UUID | None = None


class LatencySnapshot(BaseModel):
    """状态面板使用的滚动延迟统计。"""

    window_seconds: int = 60
    samples_per_stage: dict[LatencyStage, list[float]] = Field(default_factory=dict)
    p50: dict[LatencyStage, float] = Field(default_factory=dict)
    p95: dict[LatencyStage, float] = Field(default_factory=dict)
    avg: dict[LatencyStage, float] = Field(default_factory=dict)
    max: dict[LatencyStage, float] = Field(default_factory=dict)
