"""配置模型与本地配置加载。"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from teams_voice_interpreter.errors import UserFacingError


class Settings(BaseSettings):
    """运行期设置，真实密钥可来自环境变量或本地忽略的 config 文件。"""

    model_config = SettingsConfigDict(env_prefix="TVI_", extra="ignore")

    web_port: int = Field(default=8765, ge=1024, le=65535)
    host: str = "127.0.0.1"
    model_name: str = "small-q5_1"
    # 仅放专有名词词典：small 模型会把描述性长句当 "风格种子" 在弱语音段衍生输出。
    asr_initial_prompt: str = "DeepSeek BlackHole Teams AirPods 同声传译 实时同传"
    deepseek_api_key_env: str = "DEEPSEEK_API_KEY"
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-v4-flash"
    tts_rate: str = "+20%"
    tts_engine: Literal["edge_tts", "piper"] = "piper"
    # Piper voice 模型目录（含 `<voice>.onnx` 与 `<voice>.onnx.json`）；
    # 空字符串回落到 `~/.cache/teams-voice-interpreter/piper-models`。
    piper_models_dir: str = ""
    # Piper ONNX 实例池大小：每个 voice 最多同时合成 N 个流。
    # 多会议场景每个会议最多占用 2 个 voice（上下行），默认 3 覆盖 3-4 个并发会议。
    piper_pool_size: int = Field(default=3, ge=1, le=16)
    uplink_virtual_device_name: str = "BlackHole 2ch"
    downlink_virtual_device_name: str = ""
    allow_shared_virtual_device: bool = False
    vad_backend: Literal["silero", "webrtc"] = "silero"

    def resolved_deepseek_api_key(self) -> str:
        """返回最终使用的 DeepSeek API Key，进程环境变量优先于 config 文件。"""
        return os.getenv(self.deepseek_api_key_env, "") or self.deepseek_api_key

    def resolved_whisper_model_name(self) -> str:
        """返回当前 pywhispercpp 支持的 Whisper 模型名。"""
        return normalize_whisper_model_name(self.model_name)

    def resolved_downlink_virtual_device_name(self) -> str:
        """返回下行捕获使用的虚拟设备名；未配置时回落到上行设备名。"""
        return self.downlink_virtual_device_name or self.uplink_virtual_device_name

    def silero_vad_model_path(self) -> Path:
        """返回 Silero VAD ONNX 模型缓存路径。"""
        return Path.home() / ".cache/teams-voice-interpreter/vad/silero_vad.onnx"

    def resolved_piper_models_dir(self) -> Path:
        """返回 Piper voice 模型目录；空配置回落到默认路径。

        默认路径与 `scripts/measure_piper_first_byte.py` 与首次运行向导
        引导用户下载到的目录一致。
        """
        if self.piper_models_dir:
            return Path(self.piper_models_dir).expanduser()
        return Path.home() / ".cache/teams-voice-interpreter/piper-models"


def load_settings(
    *,
    config_path: Path | None = None,
    env_file: Path | None = None,
    validate_credentials: bool = True,
) -> Settings:
    """按环境变量 > config.toml > .env 的优先级加载配置。"""
    config_values = _read_config_values(config_path)
    dotenv_values = _read_dotenv(env_file or Path(".env"))
    env_values = _read_prefixed_env(os.environ)

    settings = Settings(**(dotenv_values | config_values | env_values))
    if validate_credentials and not settings.resolved_deepseek_api_key():
        raise UserFacingError(
            code="config.deepseek_key_missing",
            what_happened="发生了什么：缺少 DeepSeek API Key。",
            next_action=(
                f"下一步如何做：请设置 {settings.deepseek_api_key_env}，"
                "或在本地 config.toml 中填写 deepseek_api_key。"
            ),
        )
    return settings


def _read_config_values(config_path: Path | None) -> dict[str, Any]:
    if config_path is not None:
        return _read_toml(config_path)
    local_config = Path("config.toml")
    user_config = Path.home() / ".config" / "teams-voice-interpreter" / "config.toml"
    return _read_toml(local_config) | _read_toml(user_config)


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
        if key == "DEEPSEEK_API_KEY":
            values["deepseek_api_key"] = raw_value.strip().strip("\"'")
        elif key.startswith("TVI_"):
            values[_env_key_to_field(key)] = raw_value.strip().strip("\"'")
    return values


def _read_prefixed_env(environ: os._Environ[str]) -> dict[str, str]:
    return {
        _env_key_to_field(key): value for key, value in environ.items() if key.startswith("TVI_")
    }


def _env_key_to_field(key: str) -> str:
    return key.removeprefix("TVI_").lower()


def normalize_whisper_model_name(model_name: str) -> str:
    """兼容旧文档中的 ggml 模型名与当前 pywhispercpp 模型名。"""
    normalized = model_name.removesuffix(".bin")
    normalized = normalized.removeprefix("ggml-")
    if normalized == "small-q5_0":
        return "small-q5_1"
    return normalized
