"""配置加载优先级与凭证提示测试。"""

from pathlib import Path

import pytest

from teams_voice_interpreter.config import load_settings, normalize_whisper_model_name
from teams_voice_interpreter.errors import UserFacingError


def test_settings_priority_env_over_config_over_dotenv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """环境变量优先，其次 config.toml，最后 .env。"""
    config_path = tmp_path / "config.toml"
    env_path = tmp_path / ".env"
    config_path.write_text('web_port = 9000\nmodel_name = "small"\n', encoding="utf-8")
    env_path.write_text("TVI_WEB_PORT=7000\nTVI_MODEL_NAME=tiny\n", encoding="utf-8")
    monkeypatch.setenv("TVI_WEB_PORT", "8765")

    settings = load_settings(config_path=config_path, env_file=env_path, validate_credentials=False)

    assert settings.web_port == 8765
    assert settings.model_name == "small"


def test_deepseek_api_key_can_be_loaded_from_config_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """本地 config.toml 可存储 DeepSeek API Key。"""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    config_path = tmp_path / "config.toml"
    env_path = tmp_path / ".env"
    config_path.write_text('deepseek_api_key = "sk-config"\n', encoding="utf-8")

    settings = load_settings(config_path=config_path, env_file=env_path, validate_credentials=True)

    assert settings.resolved_deepseek_api_key() == "sk-config"


def test_deepseek_env_overrides_config_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """进程环境变量中的 DeepSeek API Key 优先于 config.toml。"""
    config_path = tmp_path / "config.toml"
    env_path = tmp_path / ".env"
    config_path.write_text('deepseek_api_key = "sk-config"\n', encoding="utf-8")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-env")

    settings = load_settings(config_path=config_path, env_file=env_path, validate_credentials=True)

    assert settings.resolved_deepseek_api_key() == "sk-env"


def test_deepseek_api_key_can_be_loaded_from_dotenv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """开发用 .env 可提供 DeepSeek API Key。"""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    config_path = tmp_path / "missing.toml"
    env_path = tmp_path / ".env"
    env_path.write_text("DEEPSEEK_API_KEY=sk-dotenv\n", encoding="utf-8")

    settings = load_settings(config_path=config_path, env_file=env_path, validate_credentials=True)

    assert settings.resolved_deepseek_api_key() == "sk-dotenv"


def test_whisper_model_name_normalizes_legacy_pywhispercpp_name() -> None:
    """旧配置里的 ggml small q5_0 应映射到当前 pywhispercpp 支持的模型名。"""
    assert normalize_whisper_model_name("ggml-small-q5_0") == "small-q5_1"
    assert normalize_whisper_model_name("ggml-small-q5_0.bin") == "small-q5_1"


def test_asr_initial_prompt_can_be_loaded_from_config(tmp_path: Path) -> None:
    """本地 config.toml 可注入 ASR 热词提示。"""
    config_path = tmp_path / "config.toml"
    env_path = tmp_path / ".env"
    config_path.write_text('asr_initial_prompt = "同声传译软件 优雅设计"\n', encoding="utf-8")

    settings = load_settings(config_path=config_path, env_file=env_path, validate_credentials=False)

    assert settings.asr_initial_prompt == "同声传译软件 优雅设计"


def test_duplex_virtual_device_names_can_be_loaded_from_config(tmp_path: Path) -> None:
    """本地 config.toml 可配置双向隔离虚拟设备名。"""
    config_path = tmp_path / "config.toml"
    env_path = tmp_path / ".env"
    config_path.write_text(
        "\n".join(
            [
                'uplink_virtual_device_name = "TVI Uplink"',
                'downlink_virtual_device_name = "TVI Downlink"',
                "allow_shared_virtual_device = false",
            ]
        ),
        encoding="utf-8",
    )

    settings = load_settings(config_path=config_path, env_file=env_path, validate_credentials=False)

    assert settings.uplink_virtual_device_name == "TVI Uplink"
    assert settings.resolved_downlink_virtual_device_name() == "TVI Downlink"
    assert not settings.allow_shared_virtual_device


def test_missing_deepseek_key_raises_two_part_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """缺少 DeepSeek key 时必须给两段式用户提示。"""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    with pytest.raises(UserFacingError) as exc_info:
        load_settings(
            config_path=Path("/tmp/nonexistent-tvi-config.toml"),
            env_file=Path("/tmp/nonexistent-tvi-env"),
            validate_credentials=True,
        )

    assert exc_info.value.code == "config.deepseek_key_missing"
    assert exc_info.value.what_happened.startswith("发生了什么")
    assert exc_info.value.next_action.startswith("下一步如何做")
