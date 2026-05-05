"""翻译滚动上下文窗口。"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class ContextPair:
    """一组已完成的源文与译文。"""

    source_text: str
    target_text: str


class RollingContextWindow:
    """保留最近 8 句双语对照。"""

    def __init__(self, *, max_pairs: int = 8) -> None:
        self._pairs: deque[ContextPair] = deque(maxlen=max_pairs)

    def add(self, *, source_text: str, target_text: str) -> None:
        """加入一组 final 双语对照。"""
        self._pairs.append(ContextPair(source_text=source_text, target_text=target_text))

    def messages(self) -> list[dict[str, str]]:
        """转换为 DeepSeek messages 历史。"""
        output: list[dict[str, str]] = []
        for pair in self._pairs:
            output.append({"role": "user", "content": pair.source_text})
            output.append({"role": "assistant", "content": pair.target_text})
        return output

    def __len__(self) -> int:
        return len(self._pairs)
