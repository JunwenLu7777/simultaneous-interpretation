"""DeepSeek 同传 system prompt 测试。"""

from __future__ import annotations

import pytest

from teams_voice_interpreter.data.audio_segment import AudioDirection
from teams_voice_interpreter.mt.prompt import build_system_prompt


@pytest.mark.parametrize(
    "direction",
    [AudioDirection.UPLINK, AudioDirection.DOWNLINK],
)
def test_prompt_contains_iron_rules(direction: AudioDirection) -> None:
    """铁律段必须出现在两个方向的 prompt 里，避免 DeepSeek 把短英文输入当对话回应。"""
    prompt = build_system_prompt(direction, [])

    assert "铁律" in prompt
    assert "只能" in prompt
    assert "翻译结果" in prompt
    assert "What is your name?" in prompt
    assert "Why are you crying?" in prompt
    assert "原样返回" in prompt
    assert "不**改写" in prompt or "不改写" in prompt


def test_prompt_uplink_specifies_zh_to_en() -> None:
    """上行 prompt 必须明确指出方向是中→英。"""
    prompt = build_system_prompt(AudioDirection.UPLINK, [])

    assert "请将下列中文文本翻译为流畅自然的英文" in prompt


def test_prompt_downlink_specifies_en_to_zh() -> None:
    """下行 prompt 必须明确指出方向是英→中。"""
    prompt = build_system_prompt(AudioDirection.DOWNLINK, [])

    assert "请将下列英文文本翻译为流畅自然的中文" in prompt


def test_prompt_rejects_oversized_glossary() -> None:
    """术语表超过 200 项必须显式拒绝，避免吃光上下文窗口。"""
    from teams_voice_interpreter.data.glossary import GlossaryEntry

    glossary = [GlossaryEntry(zh=f"项{i}", en=f"item{i}") for i in range(201)]

    with pytest.raises(ValueError):
        build_system_prompt(AudioDirection.UPLINK, glossary)
