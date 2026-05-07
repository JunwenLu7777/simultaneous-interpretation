"""用户可见错误基类与共享领域错误。"""

from __future__ import annotations

from typing import Any


class UserFacingError(Exception):
    """所有用户可见错误都必须包含两段式说明。"""

    def __init__(self, *, code: str, what_happened: str, next_action: str) -> None:
        self.code = self._require_text("code", code)
        self.what_happened = self._require_text("what_happened", what_happened)
        self.next_action = self._require_text("next_action", next_action)
        super().__init__(str(self))

    @staticmethod
    def _require_text(field: str, value: str) -> str:
        text = value.strip()
        if not text:
            msg = f"{field} must not be empty"
            raise ValueError(msg)
        return text

    def to_dict(self) -> dict[str, Any]:
        """返回 API / CLI 可直接序列化的错误结构。"""
        return {
            "code": self.code,
            "what_happened": self.what_happened,
            "next_action": self.next_action,
        }

    def __str__(self) -> str:
        return f"{self.what_happened}\n{self.next_action}"


class BlackHoleMissingError(UserFacingError):
    """BlackHole 2ch 未安装或未注册。"""

    def __init__(self) -> None:
        super().__init__(
            code="audio.blackhole_missing",
            what_happened="发生了什么：未找到 BlackHole 2ch 虚拟音频设备。",
            next_action="下一步如何做：请运行 `brew install blackhole-2ch`，重启 macOS 后再启动。",
        )


class AggregateDeviceMissingError(UserFacingError):
    """Teams 同传聚合设备未配置。"""

    def __init__(self) -> None:
        super().__init__(
            code="audio.aggregate_missing",
            what_happened="发生了什么：未找到包含 BlackHole 2ch 的聚合设备。",
            next_action=(
                "下一步如何做：请在「音频 MIDI 设置」中创建包含 BlackHole 与耳机的聚合设备。"
            ),
        )


class AudioDeviceMissingError(UserFacingError):
    """按名称查找 CoreAudio 设备失败。"""

    def __init__(self, *, device_name: str, direction: str, min_channels: int) -> None:
        super().__init__(
            code="audio.device_missing",
            what_happened=(
                f"发生了什么：未找到名为 `{device_name}` 且至少包含 {min_channels} 个"
                f"{direction}通道的音频设备。"
            ),
            next_action=(
                "下一步如何做：请在「音频 MIDI 设置」确认该虚拟设备存在，"
                "或在 config.toml 中更新对应的虚拟设备名称。"
            ),
        )


class DeepSeekError(UserFacingError):
    """DeepSeek 翻译服务错误。"""


class EdgeTTSError(UserFacingError):
    """Edge-TTS 合成服务错误。"""


class PiperTTSError(UserFacingError):
    """Piper 本地 TTS 合成服务错误（含模型缺失、ONNX 推理失败等）。"""


class WhisperError(UserFacingError):
    """Whisper.cpp 识别服务错误。"""
