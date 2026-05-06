"""Silero VAD v5 ONNX 推理边界，跨帧维护 LSTM state。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

import numpy as np
import numpy.typing as npt

from teams_voice_interpreter.errors import UserFacingError

try:
    import onnxruntime as _ort
except ImportError:  # pragma: no cover - 仅在未安装 onnxruntime 时触发
    _ort = None

Int16Array = npt.NDArray[np.int16]


@dataclass(frozen=True)
class SileroVadDecision:
    """单帧 silero 推理结果。"""

    is_speech: bool
    probability: float


class _OnnxSessionProtocol(Protocol):
    """最小 onnxruntime InferenceSession 协议，便于测试注入 fake。"""

    def run(
        self,
        output_names: list[str] | None,
        input_feed: dict[str, np.ndarray],
    ) -> list[np.ndarray]: ...


class SileroOnnxVad:
    """Silero VAD v5 ONNX 推理 + 跨帧 LSTM state 维护。"""

    FRAME_SAMPLES: int = 512
    SAMPLE_RATE_HZ: int = 16000
    _STATE_SHAPE: tuple[int, int, int] = (2, 1, 128)

    def __init__(
        self,
        *,
        model_path: Path,
        threshold: float = 0.5,
        session_factory: Callable[[Path], _OnnxSessionProtocol] | None = None,
    ) -> None:
        if not model_path.exists():
            raise UserFacingError(
                code="vad.silero_model_missing",
                what_happened=(
                    f"发生了什么：未找到 Silero VAD 模型文件 `{model_path}`。"
                ),
                next_action=(
                    "下一步如何做：请运行 `bash scripts/install-silero-vad.sh` "
                    "下载并校验模型。"
                ),
            )
        self._threshold = threshold
        self._session = (session_factory or _default_session_factory)(model_path)
        self._state = np.zeros(self._STATE_SHAPE, dtype=np.float32)
        self._sr = np.array(self.SAMPLE_RATE_HZ, dtype=np.int64)

    def reset(self) -> None:
        """重置 LSTM state；新 segment 边界调用以避免上段 state 污染。"""
        self._state = np.zeros(self._STATE_SHAPE, dtype=np.float32)

    def predict(self, samples: Int16Array) -> SileroVadDecision:
        """对 32 ms / 512 samples int16 PCM 推理 speech 概率并返回决策。"""
        frame = np.asarray(samples, dtype=np.int16).reshape(-1)
        if frame.size != self.FRAME_SAMPLES:
            raise UserFacingError(
                code="vad.silero_frame_size_invalid",
                what_happened=(
                    f"发生了什么：Silero VAD v5 模型仅接受 {self.FRAME_SAMPLES} samples / "
                    f"32 ms 帧；实际收到 {frame.size} samples。"
                ),
                next_action=(
                    "下一步如何做：请把上游 frame_ms 设为 32 "
                    "（默认 webrtc backend 是 30），或在采样路径上做缓冲累积。"
                ),
            )
        audio = (frame.astype(np.float32) / 32768.0).reshape(1, -1)
        outputs = self._session.run(
            None,
            {"input": audio, "state": self._state, "sr": self._sr},
        )
        probability = float(outputs[0].item())
        self._state = np.asarray(outputs[1], dtype=np.float32)
        return SileroVadDecision(
            is_speech=probability >= self._threshold,
            probability=probability,
        )


def _default_session_factory(model_path: Path) -> _OnnxSessionProtocol:
    """默认走真 onnxruntime；失败时给两段式错误指向 uv sync。"""
    if _ort is None:
        raise UserFacingError(
            code="vad.onnxruntime_unavailable",
            what_happened=(
                "发生了什么：未能 import onnxruntime；Silero VAD 需要它做 ONNX 推理。"
            ),
            next_action="下一步如何做：请运行 `uv sync --extra dev` 重新安装依赖。",
        )
    return cast(
        _OnnxSessionProtocol,
        _ort.InferenceSession(
            str(model_path),
            providers=["CoreMLExecutionProvider", "CPUExecutionProvider"],
        ),
    )
