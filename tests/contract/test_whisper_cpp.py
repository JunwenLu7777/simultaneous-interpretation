"""Whisper.cpp 边界契约测试。"""

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from teams_voice_interpreter.audio.capture import MicrophoneCapture
from teams_voice_interpreter.data.audio_segment import AudioDirection
from teams_voice_interpreter.data.transcript import TranscriptKind
from teams_voice_interpreter.errors import WhisperError
from teams_voice_interpreter.stt.client import WhisperClient
from teams_voice_interpreter.stt.vad import VadSegmenter
from teams_voice_interpreter.stt.whisper_streaming import choose_model_for_budget


def test_whisper_partial_final_order_and_model_load() -> None:
    """模型加载后必须输出 partial 再输出 final。"""
    frames = MicrophoneCapture().frames_from_samples(np.ones(16000, dtype=np.int16))
    client = WhisperClient()

    segments = client.recognize(frames, direction=AudioDirection.UPLINK, fixture_text="你好世界")

    assert [item.kind for item in segments] == [TranscriptKind.PARTIAL, TranscriptKind.FINAL]
    assert segments[-1].text == "你好世界"


def test_vad_close_segment_after_silence() -> None:
    """连续静音达到阈值时必须 close_segment。"""
    vad = VadSegmenter(silence_ms=60)
    silence = np.zeros(480, dtype=np.int16)

    first = vad.accept(silence)
    second = vad.accept(silence)

    assert not first.should_close_segment
    assert second.should_close_segment


def test_heartbeat_timeout_and_model_downgrade() -> None:
    """heartbeat 卡死和模型降档必须有明确行为。"""
    client = WhisperClient()
    client.last_heartbeat_at = datetime.now(UTC) - timedelta(seconds=10)

    with pytest.raises(WhisperError):
        client.require_heartbeat(timeout_seconds=3)
    assert choose_model_for_budget(measured_ram_mb=400, measured_wer_delta=6) == "ggml-tiny"
