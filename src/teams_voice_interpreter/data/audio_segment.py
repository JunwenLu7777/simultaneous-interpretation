"""音频方向、流与合成音频片段模型。"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class AudioDirection(StrEnum):
    """音频方向。"""

    UPLINK = "uplink"
    DOWNLINK = "downlink"


class AudioStream(BaseModel):
    """一路音频管道的设备路由描述。"""

    direction: AudioDirection
    source_device_name: str
    source_device_index: int
    sink_device_name: str
    sink_device_index: int
    sample_rate_hz: int = 16000
    channels: Literal[1] = 1
    active: bool = False
    bytes_in: int = 0
    bytes_out: int = 0

    @model_validator(mode="after")
    def validate_route(self) -> AudioStream:
        if self.direction is AudioDirection.UPLINK and "BlackHole" not in self.sink_device_name:
            msg = "uplink sink_device_name must contain BlackHole"
            raise ValueError(msg)
        if self.direction is AudioDirection.DOWNLINK and "BlackHole" not in self.source_device_name:
            msg = "downlink source_device_name must contain BlackHole"
            raise ValueError(msg)
        return self


class SynthesizedAudioSegment(BaseModel):
    """TTS 生成后即写出的临时音频片段。"""

    segment_id: UUID
    direction: AudioDirection
    started_at: datetime
    first_byte_at: datetime | None = None
    completed_at: datetime | None = None
    sample_rate_hz: int = 16000
    channels: Literal[1] = 1
    target_device_name: str
    bytes_written: int = Field(default=0, ge=0)
    chunk_count: int = Field(default=0, ge=0)
    provider: Literal["edge-tts"] = "edge-tts"
    provider_voice: str
