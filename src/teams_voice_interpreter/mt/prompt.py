"""DeepSeek system prompt 与术语表注入。"""

from __future__ import annotations

from teams_voice_interpreter.data.audio_segment import AudioDirection
from teams_voice_interpreter.data.glossary import GlossaryEntry


def build_system_prompt(direction: AudioDirection, glossary: list[GlossaryEntry]) -> str:
    """构造同传翻译 system prompt。"""
    if len(glossary) > 200:
        msg = "glossary entries must be <= 200"
        raise ValueError(msg)
    source, target = ("中文", "英文") if direction is AudioDirection.UPLINK else ("英文", "中文")
    terms = "\n".join(f"- {item.zh} ↔ {item.en}" for item in glossary)
    glossary_block = terms or "- 无"
    return (
        "你是专业商务同声传译。"
        f"请将下列{source}文本翻译为流畅自然的{target}，"
        "保留专有名词、英文缩写、数字、日期、金额的原始或常见映射。\n\n"
        "专有名词术语表（必须严格使用）：\n"
        f"{glossary_block}\n\n"
        "规则：\n"
        "1. 输出仅含译文，不要解释、不要前缀。\n"
        "2. 流式输出，不必等到完整一句再开始。\n"
        "3. 数字、日期、金额、英文缩写保留原写法。"
    )
