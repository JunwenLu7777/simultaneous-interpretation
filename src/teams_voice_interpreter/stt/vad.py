"""WebRTC VAD 封装与 5 秒静音闭合规则。"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

try:
    import webrtcvad
except ModuleNotFoundError:  # pragma: no cover - depends on optional runtime packaging
    webrtcvad = None

Int16Array = npt.NDArray[np.int16]


@dataclass
class VadDecision:
    """单帧 VAD 判定。"""

    is_speech: bool
    should_close_segment: bool


class VadSegmenter:
    """30 ms 帧 VAD，连续 5 秒静音触发 close_segment。"""

    def __init__(
        self,
        *,
        sample_rate_hz: int = 16000,
        frame_ms: int = 30,
        silence_ms: int = 5000,
    ) -> None:
        self.sample_rate_hz = sample_rate_hz
        self.frame_ms = frame_ms
        self.silence_frames_to_close = max(1, silence_ms // frame_ms)
        self._vad = webrtcvad.Vad(2) if webrtcvad is not None else None
        self._silent_frames = 0

    def accept(self, samples: Int16Array) -> VadDecision:
        """接收一帧 PCM 并返回是否应闭合当前段。"""
        frame = np.asarray(samples, dtype=np.int16).reshape(-1)
        is_speech = self._is_speech(frame)
        if is_speech:
            self._silent_frames = 0
        else:
            self._silent_frames += 1
        return VadDecision(
            is_speech=is_speech,
            should_close_segment=self._silent_frames >= self.silence_frames_to_close,
        )

    def _is_speech(self, frame: Int16Array) -> bool:
        if frame.size == 0:
            return False
        expected = int(self.sample_rate_hz * self.frame_ms / 1000)
        if len(frame) != expected:
            return bool(np.max(np.abs(frame)) > 128)
        if self._vad is None:
            return bool(np.max(np.abs(frame)) > 128)
        return bool(self._vad.is_speech(frame.tobytes(), self.sample_rate_hz))
