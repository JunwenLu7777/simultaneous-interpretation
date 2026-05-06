"""Silero VAD ONNX 真模型契约测试 — 验证我们对 v5 模型的 IO 假设与上游保持一致。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from teams_voice_interpreter.stt.silero_vad import SileroOnnxVad

SILERO_MODEL_PATH = Path.home() / ".cache/teams-voice-interpreter/vad/silero_vad.onnx"


@pytest.mark.skipif(
    not SILERO_MODEL_PATH.exists(),
    reason="silero_vad.onnx 未下载，请先运行 `bash scripts/install-silero-vad.sh`。",
)
def test_real_silero_distinguishes_silence_and_loud_audio() -> None:
    """真 Silero v5 模型对全静音 / 高能量噪声必须给出区分度大的概率。"""
    vad = SileroOnnxVad(model_path=SILERO_MODEL_PATH, threshold=0.5)

    silence = np.zeros(SileroOnnxVad.FRAME_SAMPLES, dtype=np.int16)
    decision_silence = vad.predict(silence)
    assert decision_silence.is_speech is False, (
        f"全静音 512 samples 必须判为非人声，实测概率 {decision_silence.probability:.4f}"
    )

    rng = np.random.default_rng(42)
    noisy = (rng.standard_normal(SileroOnnxVad.FRAME_SAMPLES) * 6000).astype(np.int16)
    vad.reset()
    decision_noise = vad.predict(noisy)
    assert decision_noise.probability < 0.5, (
        "高能量随机噪声不应被误判为人声（Silero 训练集对噪声鲁棒）；"
        f"实测概率 {decision_noise.probability:.4f}"
    )


@pytest.mark.skipif(
    not SILERO_MODEL_PATH.exists(),
    reason="silero_vad.onnx 未下载，请先运行 `bash scripts/install-silero-vad.sh`。",
)
def test_real_silero_state_evolves_across_frames() -> None:
    """真模型 LSTM state 必须跨帧推进（不能保持初始 zeros）。"""
    vad = SileroOnnxVad(model_path=SILERO_MODEL_PATH)
    samples = np.zeros(SileroOnnxVad.FRAME_SAMPLES, dtype=np.int16)
    vad.predict(samples)
    state_after_one = vad._state.copy()
    vad.predict(samples)
    state_after_two = vad._state.copy()

    assert not np.allclose(state_after_one, np.zeros_like(state_after_one))
    assert not np.allclose(state_after_one, state_after_two)
