"""用户可见错误两段式契约测试。"""

import pytest

from teams_voice_interpreter.errors import BlackHoleMissingError, UserFacingError


def test_user_facing_error_contains_two_required_parts() -> None:
    """用户可见错误必须同时说明发生了什么和下一步怎么做。"""
    error = UserFacingError(
        code="config.missing",
        what_happened="发生了什么：缺少 DeepSeek API Key。",
        next_action="下一步如何做：请设置 DEEPSEEK_API_KEY 环境变量。",
    )

    assert "缺少 DeepSeek API Key" in str(error)
    assert "请设置 DEEPSEEK_API_KEY" in str(error)
    assert error.to_dict() == {
        "code": "config.missing",
        "what_happened": "发生了什么：缺少 DeepSeek API Key。",
        "next_action": "下一步如何做：请设置 DEEPSEEK_API_KEY 环境变量。",
    }


@pytest.mark.parametrize("field", ["what_happened", "next_action"])
def test_user_facing_error_rejects_empty_parts(field: str) -> None:
    """任一段为空时都不得构造用户可见错误。"""
    kwargs = {
        "code": "invalid",
        "what_happened": "发生了什么：错误。",
        "next_action": "下一步如何做：修复配置。",
    }
    kwargs[field] = ""

    with pytest.raises(ValueError, match=field):
        UserFacingError(**kwargs)


def test_error_subclasses_keep_two_part_contract() -> None:
    """领域错误子类也必须继承同一两段式输出契约。"""
    error = BlackHoleMissingError()

    assert isinstance(error, UserFacingError)
    assert error.code == "audio.blackhole_missing"
    assert error.what_happened.startswith("发生了什么")
    assert error.next_action.startswith("下一步如何做")
