"""WebRTC VAD / Silero VAD 后端封装与帧级 segmenter。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

import numpy as np
import numpy.typing as npt

from teams_voice_interpreter.stt.silero_vad import SileroOnnxVad

try:
    import webrtcvad
except ModuleNotFoundError:  # pragma: no cover - 依赖可选
    webrtcvad = None

Int16Array = npt.NDArray[np.int16]


@runtime_checkable
class VadBackendProtocol(Protocol):
    """VAD 后端最小接口；frame_samples 决定上游切帧粒度。"""

    frame_samples: int
    sample_rate_hz: int

    def is_speech(self, samples: Int16Array) -> bool:
        """单帧 PCM 判断是否为人声。"""

    def reset(self) -> None:
        """重置内部 state（如 LSTM）；新 segment 边界调用。"""


class WebRtcBackend:
    """webrtcvad 后端，固定 30 ms / 480 samples 帧。"""

    frame_samples: int = 480
    sample_rate_hz: int = 16000

    def __init__(self, *, aggressiveness: int = 2) -> None:
        self._vad = webrtcvad.Vad(aggressiveness) if webrtcvad is not None else None

    def is_speech(self, samples: Int16Array) -> bool:
        frame = np.asarray(samples, dtype=np.int16).reshape(-1)
        if frame.size == 0:
            return False
        if self._vad is None:
            return bool(np.max(np.abs(frame)) > 128)
        return bool(self._vad.is_speech(frame.tobytes(), self.sample_rate_hz))

    def reset(self) -> None:
        return  # webrtcvad 是 stateless


class SileroBackend:
    """Silero ONNX 后端，固定 32 ms / 512 samples 帧。"""

    frame_samples: int = SileroOnnxVad.FRAME_SAMPLES
    sample_rate_hz: int = SileroOnnxVad.SAMPLE_RATE_HZ

    def __init__(
        self,
        *,
        model_path: Path | None = None,
        threshold: float = 0.5,
        silero_vad: SileroOnnxVad | None = None,
    ) -> None:
        if silero_vad is not None:
            self._vad = silero_vad
        elif model_path is not None:
            self._vad = SileroOnnxVad(model_path=model_path, threshold=threshold)
        else:
            msg = "SileroBackend requires either model_path or silero_vad"
            raise ValueError(msg)

    def is_speech(self, samples: Int16Array) -> bool:
        return self._vad.predict(samples).is_speech

    def reset(self) -> None:
        self._vad.reset()


@dataclass
class VadDecision:
    """单帧 VAD 判定。"""

    is_speech: bool
    should_close_segment: bool


class VadSegmenter:
    """帧级 VAD + 连续静音 silence_ms 触发 close_segment。"""

    def __init__(
        self,
        *,
        backend: VadBackendProtocol | None = None,
        sample_rate_hz: int | None = None,
        frame_ms: int | None = None,
        silence_ms: int = 5000,
    ) -> None:
        del frame_ms  # 由 backend.frame_samples 决定，旧参数仅作 backward-compat 占位
        if backend is None:
            backend = WebRtcBackend()
        if sample_rate_hz is not None and sample_rate_hz != backend.sample_rate_hz:
            msg = (
                f"backend.sample_rate_hz={backend.sample_rate_hz} 与传入 "
                f"sample_rate_hz={sample_rate_hz} 不一致"
            )
            raise ValueError(msg)
        self._backend = backend
        self.sample_rate_hz = backend.sample_rate_hz
        self.frame_samples = backend.frame_samples
        self.frame_ms = round(self.frame_samples * 1000 / self.sample_rate_hz)
        self.silence_frames_to_close = max(1, silence_ms // self.frame_ms)
        self._silent_frames = 0

    def accept(self, samples: Int16Array) -> VadDecision:
        """接收单帧 PCM 并返回 VAD 决策。"""
        frame = np.asarray(samples, dtype=np.int16).reshape(-1)
        if frame.size != self.frame_samples:
            is_speech = bool(np.max(np.abs(frame)) > 128) if frame.size > 0 else False
        else:
            is_speech = self._backend.is_speech(frame)
        if is_speech:
            self._silent_frames = 0
        else:
            self._silent_frames += 1
        return VadDecision(
            is_speech=is_speech,
            should_close_segment=self._silent_frames >= self.silence_frames_to_close,
        )
