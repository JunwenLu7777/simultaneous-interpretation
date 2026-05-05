"""首次运行向导集成测试。"""

import pytest

from teams_voice_interpreter.cli.wizard import FirstRunWizard
from teams_voice_interpreter.errors import UserFacingError


def test_wizard_success_path() -> None:
    """七步向导全部通过时返回 7 个步骤。"""
    steps = FirstRunWizard().run()

    assert len(steps) == 7
    assert all(step.passed for step in steps)


def test_wizard_failure_two_part_message() -> None:
    """任一步失败必须抛两段式用户提示。"""
    wizard = FirstRunWizard({"credential": False})

    with pytest.raises(UserFacingError) as exc_info:
        wizard.require_passed()

    assert exc_info.value.what_happened.startswith("发生了什么")
    assert exc_info.value.next_action.startswith("下一步如何做")
