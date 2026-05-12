"""语言检测与翻译跳过决策。

Chinese 和 English 使用字符集完全不同，简单的 CJK vs ASCII 比例即可准确分类。
"""

from __future__ import annotations

from teams_voice_interpreter.data.audio_segment import AudioDirection

_CJK_RANGES = [
    (0x4E00, 0x9FFF),  # CJK Unified Ideographs
    (0x3400, 0x4DBF),  # CJK Unified Ideographs Extension A
    (0xF900, 0xFAFF),  # CJK Compatibility Ideographs
]


def _is_cjk(ch: str) -> bool:
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in _CJK_RANGES)


def classify_language(text: str) -> str:
    """按字符比例分类：``'zh'`` / ``'en'`` / ``'mixed'`` / ``'unknown'``。"""
    cjk = 0
    alpha = 0
    for ch in text:
        if _is_cjk(ch):
            cjk += 1
        elif ch.isascii() and ch.isalpha():
            alpha += 1
    total = cjk + alpha
    if total == 0:
        return "unknown"
    ratio = cjk / total
    if ratio >= 0.7:
        return "zh"
    if ratio <= 0.3:
        return "en"
    return "mixed"


def has_speakable_content(text: str) -> bool:
    """文本中是否包含可朗读的内容（CJK 字符或英文单词）。

    纯标点、空白、数字、符号 → 不可朗读。
    """
    for ch in text:
        if _is_cjk(ch):
            return True
        if ch.isascii() and ch.isalpha():
            return True
    return False


def count_english_words(text: str) -> int:
    """数文本中英文单词个数。"""
    import re

    return len(re.findall(r"[a-zA-Z]+", text))


def should_skip_translation(text: str, *, direction: AudioDirection) -> bool:
    """根据语言分类和方向决定是否跳过 MT 翻译。

    上行（中转英）：
    - 纯英文 → 跳过（不需要翻译）
    - 纯中文 / 混合 → 翻译

    下行（英转中）：
    - 纯中文 → 跳过（不需要翻译）
    - 混合且英文单词 ≤ 5 个 → 跳过（少量英文夹杂，直接返回）
    - 纯英文 / 混合且英文多 → 翻译
    """
    lang = classify_language(text)
    if direction is AudioDirection.UPLINK:
        return lang == "en"
    # DOWNLINK
    if lang == "zh":
        return True
    if lang == "mixed" and count_english_words(text) <= 5:
        return True
    return False
