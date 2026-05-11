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
    glossary_block = f"术语表：\n{terms}\n" if terms else ""
    return (
        f"你是同传翻译。将{source}直接译为{target}。只能输出翻译结果。不改写，不解释，不回答。\n"
        f"{glossary_block}"
        f"铁律：输入若已是{target}则原样返回；"
        "What is your name?→你叫什么名字？；Why are you crying?→你为什么在哭？；"
        "保留数字/日期/缩写原写法。流式输出。"
    )
