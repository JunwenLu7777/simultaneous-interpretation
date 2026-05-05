"""外部服务凭证引用模型。"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator, model_validator


class ServiceKind(StrEnum):
    """外部或本地服务类型。"""

    STT = "stt"
    MT = "mt"
    TTS = "tts"


class ServiceCredential(BaseModel):
    """只保存环境变量名，不保存真实密钥。"""

    id: str
    service: ServiceKind
    provider: str
    endpoint: str | None = None
    key_env_var: str | None = None
    quota_threshold_warning_pct: float = Field(default=0.8, ge=0, le=1)
    healthy: bool = True
    last_check_at: datetime | None = None

    @field_validator("key_env_var")
    @classmethod
    def reject_real_key(cls, value: str | None) -> str | None:
        if value and (value.startswith("sk-") or "secret" in value.lower()):
            msg = "key_env_var must reference an environment variable name, not a real key"
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def validate_endpoint(self) -> ServiceCredential:
        if self.endpoint is not None and not self.endpoint.startswith("https://"):
            msg = "endpoint must use https"
            raise ValueError(msg)
        return self
