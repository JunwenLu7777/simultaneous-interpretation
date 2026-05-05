"""静态术语表条目模型。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class GlossaryEntry(BaseModel):
    """FR-012 静态术语表中的单条记录。"""

    zh: str = Field(min_length=1, max_length=64)
    en: str = Field(min_length=1, max_length=128)
    note: str | None = Field(default=None, max_length=256)
    source: Literal["user", "v2-builtin"] = "user"
