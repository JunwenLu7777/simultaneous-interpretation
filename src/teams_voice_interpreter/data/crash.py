"""匿名崩溃报告模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, field_validator

from teams_voice_interpreter.data.credential import ServiceKind


class CrashReport(BaseModel):
    """不包含音频、文本、密钥或用户绝对路径的崩溃报告。"""

    occurred_at: datetime
    python_version: str
    os_version: str
    arch: Literal["arm64", "x86_64"]
    dependency_versions: dict[str, str]
    stack_trace: str
    services_health_snapshot: dict[ServiceKind, bool]
    resource_snapshot: dict[Literal["ram_mb", "cpu_pct"], float]
    notes: str | None = None

    @field_validator("stack_trace")
    @classmethod
    def reject_private_stack_trace(cls, value: str) -> str:
        forbidden_tokens = ("/Users/", "sk-", "api_key", "audio bytes", "transcript")
        lowered = value.lower()
        if any(token.lower() in lowered for token in forbidden_tokens):
            msg = "stack_trace contains private or forbidden data"
            raise ValueError(msg)
        return value
