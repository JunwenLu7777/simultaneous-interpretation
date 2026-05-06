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
from teams_voice_interpreter.stt.whisper_streaming import (
    OnlineASRProcessor,
    WhisperStreamingWrapper,
    choose_model_for_budget,
)


def test_whisper_partial_final_order_and_model_load() -> None:
    """模型加载后必须输出 partial 再输出 final。"""
    frames = MicrophoneCapture().frames_from_samples(np.ones(16000, dtype=np.int16))
    client = WhisperClient()

    segments = client.recognize(frames, direction=AudioDirection.UPLINK, fixture_text="你好世界")

    assert [item.kind for item in segments] == [TranscriptKind.PARTIAL, TranscriptKind.FINAL]
    assert segments[-1].text == "你好世界"


def test_whisper_local_agreement_filters_unstable_partials() -> None:
    """Whisper partial 边界必须只提交稳定前缀，final 再补齐尾巴。"""
    wrapper = WhisperStreamingWrapper()

    chunks = wrapper.stable_chunks_from_partial_updates(
        ["我们今天", "我们今天讨论现金流", "我们今天讨论现金流预测"],
        final_text="我们今天讨论现金流预测",
        direction=AudioDirection.UPLINK,
    )

    assert [item.kind for item in chunks] == [
        TranscriptKind.PARTIAL,
        TranscriptKind.PARTIAL,
        TranscriptKind.FINAL,
    ]
    assert [item.text for item in chunks] == [
        "我们今天",
        "我们今天讨论现金流",
        "我们今天讨论现金流预测",
    ]
    assert [item.delta_text for item in chunks] == ["我们今天", "讨论现金流", "预测"]


def test_whisper_local_agreement_always_emits_final_completion() -> None:
    """即使 partial 已提交全文，也必须发出 final 收口事件。"""
    wrapper = WhisperStreamingWrapper()

    chunks = wrapper.stable_chunks_from_partial_updates(
        ["我们今天", "我们今天讨论", "我们今天讨论"],
        final_text="我们今天讨论",
        direction=AudioDirection.UPLINK,
    )

    assert [item.kind for item in chunks] == [
        TranscriptKind.PARTIAL,
        TranscriptKind.PARTIAL,
        TranscriptKind.FINAL,
    ]
    assert chunks[-1].text == "我们今天讨论"
    assert chunks[-1].delta_text == ""
    assert not chunks[-1].revision


def test_whisper_online_processor_contract_emits_partial_before_close_segment() -> None:
    """流式契约必须支持 feed 多个音频 chunk 后先产出 partial，再由 close_segment 收口。"""
    scripted_texts = iter(["hello team", "hello team today", "hello team today"])
    processor = OnlineASRProcessor(
        direction=AudioDirection.DOWNLINK,
        transcribe_buffer=lambda _samples: next(scripted_texts),
    )

    first = processor.insert_audio_chunk(np.ones(4800, dtype=np.int16))
    second = processor.insert_audio_chunk(np.ones(4800, dtype=np.int16))
    final = processor.close_segment()

    assert first == []
    assert [item.kind for item in second + final] == [
        TranscriptKind.PARTIAL,
        TranscriptKind.FINAL,
    ]
    assert second[0].delta_text == "hello"
    assert final[0].delta_text == "team today"


def test_whisper_client_exposes_online_processor_boundary() -> None:
    """业务侧必须能通过 WhisperClient 创建在线 processor，而不是绕过 STT 契约。"""
    scripted_texts = iter(["我们今天", "我们今天讨论", "我们今天讨论"])
    client = WhisperClient()
    processor = client.start_online(
        direction=AudioDirection.UPLINK,
        transcribe_buffer=lambda _samples: next(scripted_texts),
    )

    processor.insert_audio_chunk(np.ones(4800, dtype=np.int16))
    partial = processor.insert_audio_chunk(np.ones(4800, dtype=np.int16))
    final = client.close_online_segment(processor)

    assert partial[0].kind is TranscriptKind.PARTIAL
    assert final[0].kind is TranscriptKind.FINAL
    assert final[0].delta_text == "讨论"


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
    assert choose_model_for_budget(measured_ram_mb=400, measured_wer_delta=6) == "tiny"
