"""Silero VAD ONNX 推理边界测试（mock onnxruntime 验证调用契约与决策逻辑）。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pytest

from teams_voice_interpreter.errors import UserFacingError
from teams_voice_interpreter.stt.silero_vad import SileroOnnxVad


@dataclass
class _FakeOnnxSession:
    """记录 silero v5 ONNX 调用的轻量 mock。"""

    next_probs: list[float] = field(default_factory=list)
    calls: list[dict[str, np.ndarray]] = field(default_factory=list)

    def run(
        self,
        output_names: list[str] | None,
        input_feed: dict[str, np.ndarray],
    ) -> list[np.ndarray]:
        del output_names
        self.calls.append({key: value.copy() for key, value in input_feed.items()})
        prob = self.next_probs.pop(0) if self.next_probs else 0.5
        new_state = input_feed["state"].copy() + 0.01
        return [
            np.array([[prob]], dtype=np.float32),
            new_state,
        ]


def _factory(session: _FakeOnnxSession) -> Callable[[Path], _FakeOnnxSession]:
    return lambda model_path: session  # noqa: ARG005


def test_predict_returns_speech_above_threshold(tmp_path: Path) -> None:
    """speech 概率 ≥ threshold 时必须判为人声。"""
    model = tmp_path / "silero.onnx"
    model.write_bytes(b"fake")
    session = _FakeOnnxSession(next_probs=[0.85])

    vad = SileroOnnxVad(model_path=model, threshold=0.5, session_factory=_factory(session))
    decision = vad.predict(np.zeros(SileroOnnxVad.FRAME_SAMPLES, dtype=np.int16))

    assert decision.is_speech is True
    assert decision.probability == pytest.approx(0.85, abs=1e-6)


def test_predict_returns_silence_below_threshold(tmp_path: Path) -> None:
    """speech 概率 < threshold 必须判为非人声。"""
    model = tmp_path / "silero.onnx"
    model.write_bytes(b"fake")
    session = _FakeOnnxSession(next_probs=[0.20])

    vad = SileroOnnxVad(model_path=model, threshold=0.5, session_factory=_factory(session))
    decision = vad.predict(np.zeros(SileroOnnxVad.FRAME_SAMPLES, dtype=np.int16))

    assert decision.is_speech is False
    assert decision.probability == pytest.approx(0.20, abs=1e-6)


def test_state_carries_across_predict_calls(tmp_path: Path) -> None:
    """连续两次 predict 时 state 必须从上次输出 carry 过来，不能每次重置。"""
    model = tmp_path / "silero.onnx"
    model.write_bytes(b"fake")
    session = _FakeOnnxSession(next_probs=[0.5, 0.5])

    vad = SileroOnnxVad(model_path=model, session_factory=_factory(session))
    samples = np.zeros(SileroOnnxVad.FRAME_SAMPLES, dtype=np.int16)
    vad.predict(samples)
    vad.predict(samples)

    first_state = session.calls[0]["state"]
    second_state = session.calls[1]["state"]
    assert np.allclose(first_state, np.zeros_like(first_state))
    # 第二次 state 应当包含 mock 的 +0.01 增量，证明跨帧维护
    assert not np.allclose(second_state, np.zeros_like(second_state))
    assert np.allclose(second_state, first_state + 0.01)


def test_reset_clears_state(tmp_path: Path) -> None:
    """reset 必须把 state 清零，便于新 segment 边界重置。"""
    model = tmp_path / "silero.onnx"
    model.write_bytes(b"fake")
    session = _FakeOnnxSession(next_probs=[0.5, 0.5, 0.5])

    vad = SileroOnnxVad(model_path=model, session_factory=_factory(session))
    samples = np.zeros(SileroOnnxVad.FRAME_SAMPLES, dtype=np.int16)
    vad.predict(samples)
    vad.predict(samples)
    vad.reset()
    vad.predict(samples)

    third_state = session.calls[2]["state"]
    assert np.allclose(third_state, np.zeros_like(third_state))


def test_predict_normalizes_int16_to_float32_audio(tmp_path: Path) -> None:
    """int16 PCM 必须按 / 32768 归一化为 float32 [-1, 1]。"""
    model = tmp_path / "silero.onnx"
    model.write_bytes(b"fake")
    session = _FakeOnnxSession(next_probs=[0.5])

    vad = SileroOnnxVad(model_path=model, session_factory=_factory(session))
    samples = np.full(SileroOnnxVad.FRAME_SAMPLES, 16384, dtype=np.int16)
    vad.predict(samples)

    audio_in = session.calls[0]["input"]
    assert audio_in.dtype == np.float32
    assert audio_in.shape == (1, SileroOnnxVad.FRAME_SAMPLES)
    assert np.allclose(audio_in[0], 16384.0 / 32768.0, atol=1e-3)


def test_predict_passes_sample_rate_as_int64_scalar(tmp_path: Path) -> None:
    """ONNX session 要求 sr 是 0-d int64 numpy；普通 Python int 会被 onnxruntime 拒绝。"""
    model = tmp_path / "silero.onnx"
    model.write_bytes(b"fake")
    session = _FakeOnnxSession(next_probs=[0.5])

    vad = SileroOnnxVad(model_path=model, session_factory=_factory(session))
    vad.predict(np.zeros(SileroOnnxVad.FRAME_SAMPLES, dtype=np.int16))

    sr = session.calls[0]["sr"]
    assert sr.dtype == np.int64
    assert sr.shape == ()
    assert int(sr) == SileroOnnxVad.SAMPLE_RATE_HZ


def test_predict_rejects_wrong_frame_size(tmp_path: Path) -> None:
    """v5 模型训练用 32 ms / 512 samples；其它帧大小必须给两段式提示。"""
    model = tmp_path / "silero.onnx"
    model.write_bytes(b"fake")
    session = _FakeOnnxSession()

    vad = SileroOnnxVad(model_path=model, session_factory=_factory(session))
    with pytest.raises(UserFacingError) as exc:
        vad.predict(np.zeros(480, dtype=np.int16))

    assert "发生了什么" in exc.value.what_happened
    assert "下一步如何做" in exc.value.next_action


def test_model_path_missing_raises_two_part_error(tmp_path: Path) -> None:
    """模型文件缺失必须直接抛 UserFacingError 指向 install 脚本。"""
    missing = tmp_path / "missing.onnx"

    with pytest.raises(UserFacingError) as exc:
        SileroOnnxVad(model_path=missing)

    assert "发生了什么" in exc.value.what_happened
    assert "scripts/install-silero-vad.sh" in exc.value.next_action
