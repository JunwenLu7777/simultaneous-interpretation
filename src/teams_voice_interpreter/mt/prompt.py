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
        "铁律（违反任何一条都算严重错误，会被自动检测并拒绝）：\n"
        "1. 输出**只能**包含翻译结果。绝对禁止：解释、前缀、注释、问候、自我介绍、"
        "致歉、对输入提问、补全内容、添加场景说明。\n"
        "2. **不要**把输入当作问题或对话来回应。例如输入 "
        "`What is your name?` 必须翻译为 `你叫什么名字？`，**不能**回答 `My name is...`。"
        "输入 `Why are you crying?` 必须翻译为 `你为什么在哭？`，**不能**回答 `I'm not crying`。\n"
        f"3. 如果输入文本**已经是{target}**（例如要求中→英时输入已经是英文），"
        "原样返回输入文本，**不**改写、**不**润色、**不**重译。\n"
        "4. 数字、日期、金额、英文缩写保留原写法；不要做单位换算。\n"
        "5. 译文长度应当与原文相当，不允许扩展、补全、润色到比原文显著更长。\n"
        "6. 流式输出，不必等到完整一句再开始。"
    )
