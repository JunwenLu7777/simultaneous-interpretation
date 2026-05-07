"""首次运行向导集成测试。"""

import pytest

from teams_voice_interpreter.cli.wizard import FirstRunWizard
from teams_voice_interpreter.errors import UserFacingError


def test_wizard_success_path() -> None:
    """向导全部通过时返回完整步骤清单。"""
    steps = FirstRunWizard().run()

    assert len(steps) == 8
    assert all(step.passed for step in steps)
    assert [step.key for step in steps] == [
        "blackhole",
        "aggregate",
        "teams_route",
        "mic_permission",
        "credential",
        "piper_models",
        "glossary",
        "disclaimer",
    ]


def test_wizard_failure_two_part_message() -> None:
    """任一步失败必须抛两段式用户提示。"""
    wizard = FirstRunWizard({"credential": False})

    with pytest.raises(UserFacingError) as exc_info:
        wizard.require_passed()

    assert exc_info.value.what_happened.startswith("发生了什么")
    assert exc_info.value.next_action.startswith("下一步如何做")


def test_wizard_piper_model_failure_guides_download() -> None:
    """Piper 模型缺失时必须给出模型下载目录与文件名。"""
    wizard = FirstRunWizard({"piper_models": False})

    with pytest.raises(UserFacingError) as exc_info:
        wizard.require_passed()

    assert "下载 Piper voice 模型" in exc_info.value.what_happened
    assert "en_US-amy-medium.onnx" in exc_info.value.next_action
    assert "zh_CN-huayan-medium.onnx" in exc_info.value.next_action
    assert "piper_models_dir" in exc_info.value.next_action
