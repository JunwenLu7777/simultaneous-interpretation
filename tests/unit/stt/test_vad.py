"""VAD backend 抽象与 VadSegmenter 边界测试。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pytest

from teams_voice_interpreter.stt.silero_vad import SileroOnnxVad
from teams_voice_interpreter.stt.vad import (
    SileroBackend,
    VadBackendProtocol,
    VadSegmenter,
    WebRtcBackend,
)


def test_webrtc_backend_exposes_30ms_frame() -> None:
    """webrtcvad 后端必须暴露 30 ms / 480 samples 帧规格。"""
    backend = WebRtcBackend()
    assert backend.frame_samples == 480
    assert backend.sample_rate_hz == 16000


def test_silero_backend_exposes_32ms_frame(tmp_path: Path) -> None:
    """Silero v5 后端必须暴露 32 ms / 512 samples 帧规格。"""
    model = tmp_path / "silero.onnx"
    model.write_bytes(b"fake")

    class _StubSession:
        def run(
            self,
            output_names: list[str] | None,
            input_feed: dict[str, np.ndarray],
        ) -> list[np.ndarray]:
            del output_names, input_feed
            return [
                np.array([[0.0]], dtype=np.float32),
                np.zeros((2, 1, 128), dtype=np.float32),
            ]

    silero = SileroOnnxVad(
        model_path=model,
        session_factory=lambda _: _StubSession(),
    )
    backend = SileroBackend(silero_vad=silero)
    assert backend.frame_samples == 512
    assert backend.sample_rate_hz == 16000


@dataclass
class _ScriptedBackend:
    """按预设序列产出 is_speech 结果的测试 backend。"""

    speech_sequence: list[bool]
    frame_samples: int = 480
    sample_rate_hz: int = 16000
    reset_calls: int = field(default=0)
    _idx: int = field(default=0)

    def is_speech(self, samples: np.ndarray) -> bool:
        del samples
        if self._idx >= len(self.speech_sequence):
            return False
        value = self.speech_sequence[self._idx]
        self._idx += 1
        return value

    def reset(self) -> None:
        self.reset_calls += 1


def test_segmenter_uses_backend_frame_samples() -> None:
    """VadSegmenter 必须以 backend.frame_samples 决定 frame_ms 与 silence_frames。"""
    backend = _ScriptedBackend(speech_sequence=[], frame_samples=512)
    seg = VadSegmenter(backend=backend, silence_ms=300)

    assert seg.frame_samples == 512
    assert seg.frame_ms == 32  # 512 / 16000 ≈ 32 ms
    assert seg.silence_frames_to_close == 9  # 300 / 32 = 9


def test_segmenter_close_segment_after_silence_ms() -> None:
    """连续静音帧达到 silence_ms 必须触发 close_segment。"""
    backend = _ScriptedBackend(speech_sequence=[False] * 30)
    seg = VadSegmenter(backend=backend, silence_ms=500)
    samples = np.zeros(backend.frame_samples, dtype=np.int16)

    decisions = [seg.accept(samples) for _ in range(20)]

    closes = [i for i, decision in enumerate(decisions) if decision.should_close_segment]
    expected_first = max(1, 500 // seg.frame_ms) - 1
    assert closes[0] == expected_first


def test_segmenter_speech_resets_silence_counter() -> None:
    """检测到 speech 帧后静音计数必须归零，下一个静音段重新累计。"""
    backend = _ScriptedBackend(
        speech_sequence=[False, False, True, False, False, False, False],
    )
    seg = VadSegmenter(backend=backend, silence_ms=90)  # 3 帧静音触发
    samples = np.zeros(backend.frame_samples, dtype=np.int16)

    decisions = [seg.accept(samples) for _ in range(7)]

    assert decisions[1].should_close_segment is False  # 累计 2 静音帧
    assert decisions[2].is_speech is True  # 重置点
    assert decisions[4].should_close_segment is False  # 重置后只 2 帧静音
    assert decisions[5].should_close_segment is True  # 第 3 帧静音触发


def test_segmenter_close_segment_with_silero_frame_size(tmp_path: Path) -> None:
    """silero 32 ms 帧时 silence_ms=320 必须 10 帧触发。"""
    backend = _ScriptedBackend(speech_sequence=[False] * 20, frame_samples=512)
    seg = VadSegmenter(backend=backend, silence_ms=320)
    samples = np.zeros(backend.frame_samples, dtype=np.int16)

    decisions = [seg.accept(samples) for _ in range(15)]

    closes = [i for i, decision in enumerate(decisions) if decision.should_close_segment]
    assert closes[0] == 9  # 320 / 32 = 10 frames，0-indexed 第 10 帧 = 9


def test_segmenter_default_backend_is_webrtc() -> None:
    """不传 backend 时必须默认使用 WebRtcBackend，保证旧调用兼容。"""
    seg = VadSegmenter(silence_ms=300)
    assert isinstance(seg._backend, WebRtcBackend)
    assert seg.frame_samples == 480


def test_segmenter_legacy_signature_is_accepted() -> None:
    """旧 sample_rate_hz / frame_ms kwargs 在新签名下必须仍能构造（backward-compat）。"""
    seg = VadSegmenter(sample_rate_hz=16000, frame_ms=30, silence_ms=300)
    assert seg.frame_samples == 480


def test_segmenter_sample_rate_mismatch_raises() -> None:
    """传入的 sample_rate_hz 与 backend 不一致必须立刻报错避免静默错配。"""
    backend = _ScriptedBackend(speech_sequence=[], sample_rate_hz=8000)
    with pytest.raises(ValueError):
        VadSegmenter(backend=backend, sample_rate_hz=16000)


def test_backend_protocol_runtime_check() -> None:
    """_ScriptedBackend / WebRtcBackend / SileroBackend 都必须满足 VadBackendProtocol。"""
    assert isinstance(WebRtcBackend(), VadBackendProtocol)
    assert isinstance(_ScriptedBackend(speech_sequence=[]), VadBackendProtocol)
