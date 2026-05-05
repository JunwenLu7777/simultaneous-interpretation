"""配置加载优先级与凭证提示测试。"""

from pathlib import Path

import pytest

from teams_voice_interpreter.config import load_settings
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


def test_missing_deepseek_key_raises_two_part_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """缺少 DeepSeek key 时必须给两段式用户提示。"""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    with pytest.raises(UserFacingError) as exc_info:
        load_settings(validate_credentials=True)

    assert exc_info.value.code == "config.deepseek_key_missing"
    assert exc_info.value.what_happened.startswith("发生了什么")
    assert exc_info.value.next_action.startswith("下一步如何做")
