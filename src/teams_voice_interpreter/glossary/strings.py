"""共享用户可见文案加载与合规校验。"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

DEFAULT_LOCALE_PATH = Path(__file__).with_name("i18n") / "zh-CN.toml"

REQUIRED_STRING_KEYS = (
    "error.blackhole_missing",
    "error.aggregate_missing",
    "error.deepseek_key_missing",
    "error.instance_already_running",
    "error.session_invalid_transition",
)


def load_strings(path: Path | None = None) -> dict[str, str]:
    """读取并扁平化 TOML 文案表。"""
    source = path or DEFAULT_LOCALE_PATH
    data = tomllib.loads(source.read_text(encoding="utf-8"))
    flattened: dict[str, str] = {}
    _flatten("", data, flattened)
    return flattened


def validate_message_catalog(catalog: dict[str, str]) -> list[str]:
    """返回不满足两段式的用户可见错误键。"""
    invalid: list[str] = []
    for key, value in catalog.items():
        if not key.startswith("error."):
            continue
        if "发生了什么" not in value or "下一步如何做" not in value:
            invalid.append(key)
    return invalid


def _flatten(prefix: str, value: dict[str, Any], output: dict[str, str]) -> None:
    for key, child in value.items():
        full_key = f"{prefix}.{key}" if prefix else key
        if isinstance(child, dict):
            _flatten(full_key, child, output)
        else:
            output[full_key] = str(child)
