"""配置模型与本地配置加载。"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from teams_voice_interpreter.errors import UserFacingError


class Settings(BaseSettings):
    """运行期设置，真实密钥只通过环境变量引用。"""

    model_config = SettingsConfigDict(env_prefix="TVI_", extra="ignore")

    web_port: int = Field(default=8765, ge=1024, le=65535)
    host: str = "127.0.0.1"
    model_name: str = "ggml-small-q5_0"
    deepseek_api_key_env: str = "DEEPSEEK_API_KEY"


def load_settings(
    *,
    config_path: Path | None = None,
    env_file: Path | None = None,
    validate_credentials: bool = True,
) -> Settings:
    """按环境变量 > config.toml > .env 的优先级加载配置。"""
    default_config = Path.home() / ".config" / "teams-voice-interpreter" / "config.toml"
    config_values = _read_toml(config_path or default_config)
    dotenv_values = _read_dotenv(env_file or Path(".env"))
    env_values = _read_prefixed_env(os.environ)

    settings = Settings(**(dotenv_values | config_values | env_values))
    if validate_credentials and not os.getenv(settings.deepseek_api_key_env):
        raise UserFacingError(
            code="config.deepseek_key_missing",
            what_happened="发生了什么：缺少 DeepSeek API Key 环境变量。",
            next_action=(
                f"下一步如何做：请设置 {settings.deepseek_api_key_env}，"
                "或在 config.toml 中改用正确的 key_env_var。"
            ),
        )
    return settings


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _read_dotenv(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, raw_value = stripped.split("=", 1)
        if key.startswith("TVI_"):
            values[_env_key_to_field(key)] = raw_value.strip().strip("\"'")
    return values


def _read_prefixed_env(environ: os._Environ[str]) -> dict[str, str]:
    return {
        _env_key_to_field(key): value for key, value in environ.items() if key.startswith("TVI_")
    }


def _env_key_to_field(key: str) -> str:
    return key.removeprefix("TVI_").lower()
