"""在线 ASR processor 的 sliding / partial 行为测试。"""

from __future__ import annotations

import numpy as np

from teams_voice_interpreter.data.audio_segment import AudioDirection
from teams_voice_interpreter.data.transcript import TranscriptKind
from teams_voice_interpreter.stt.whisper_streaming import (
    OnlineASRProcessor,
    WhisperStreamingConfig,
)


def test_online_asr_processor_emits_stable_partial_before_final() -> None:
    """持续喂音频时，达到 step 后必须能在 close_segment 前输出稳定 partial。"""
    scripted_texts = iter(["我们今天", "我们今天讨论现金流", "我们今天讨论现金流预测"])
    processor = OnlineASRProcessor(
        direction=AudioDirection.UPLINK,
        transcribe_buffer=lambda _samples: next(scripted_texts),
        config=WhisperStreamingConfig(step_ms=300),
    )

    assert processor.insert_audio_chunk(_audio_ms(300)) == []
    partials = processor.insert_audio_chunk(_audio_ms(300))
    finals = processor.close_segment()

    assert [(chunk.kind, chunk.text, chunk.delta_text) for chunk in partials + finals] == [
        (TranscriptKind.PARTIAL, "我们今天", "我们今天"),
        (TranscriptKind.FINAL, "我们今天讨论现金流预测", "讨论现金流预测"),
    ]


def test_online_asr_processor_waits_for_step_before_transcribing() -> None:
    """不足 step 的小 chunk 只能缓存音频，不能触发昂贵 ASR 推理。"""
    calls = 0

    def transcribe(_samples: np.ndarray) -> str:
        nonlocal calls
        calls += 1
        return "hello"

    processor = OnlineASRProcessor(
        direction=AudioDirection.DOWNLINK,
        transcribe_buffer=transcribe,
        config=WhisperStreamingConfig(step_ms=300),
    )

    assert processor.insert_audio_chunk(_audio_ms(100)) == []
    assert calls == 0


def test_online_asr_processor_resets_between_segments() -> None:
    """close_segment 后新一段不得继承上一段已提交前缀。"""
    scripted_texts = iter(["第一段", "第一段预算", "第二段", "第二段风险"])
    processor = OnlineASRProcessor(
        direction=AudioDirection.UPLINK,
        transcribe_buffer=lambda _samples: next(scripted_texts),
        config=WhisperStreamingConfig(step_ms=300),
    )

    processor.insert_audio_chunk(_audio_ms(300))
    first_partial = processor.insert_audio_chunk(_audio_ms(300))
    first_final = processor.close_segment(final_text="第一段预算")
    processor.insert_audio_chunk(_audio_ms(300))
    second_partial = processor.insert_audio_chunk(_audio_ms(300))
    second_final = processor.close_segment(final_text="第二段风险")

    assert first_partial[0].text == "第一段"
    assert first_final[0].delta_text == "预算"
    assert second_partial[0].text == "第二段"
    assert second_final[0].delta_text == "风险"
    assert first_partial[0].segment_id != second_partial[0].segment_id


def test_online_asr_processor_marks_final_revision_without_duplicate_delta() -> None:
    """final 改写已提交 partial 时必须标记 revision，避免下游重复翻译。"""
    scripted_texts = iter(["我们今天", "我们今天讨论"])
    processor = OnlineASRProcessor(
        direction=AudioDirection.UPLINK,
        transcribe_buffer=lambda _samples: next(scripted_texts),
        config=WhisperStreamingConfig(step_ms=300),
    )

    processor.insert_audio_chunk(_audio_ms(300))
    partial = processor.insert_audio_chunk(_audio_ms(300))[0]
    final = processor.close_segment(final_text="今天我们讨论")[0]

    assert partial.text == "我们今天"
    assert final.text == "今天我们讨论"
    assert final.delta_text == ""
    assert final.revision


def test_online_asr_processor_close_empty_segment_does_not_transcribe() -> None:
    """空 segment 收口不得触发 ASR，避免静音路径产生异常或幻觉文本。"""
    calls = 0

    def transcribe(_samples: np.ndarray) -> str:
        nonlocal calls
        calls += 1
        raise AssertionError("empty segment must not call ASR")

    processor = OnlineASRProcessor(
        direction=AudioDirection.UPLINK,
        transcribe_buffer=transcribe,
    )

    assert processor.close_segment() == []
    assert calls == 0


def _audio_ms(duration_ms: int) -> np.ndarray:
    return np.ones(int(16000 * duration_ms / 1000), dtype=np.int16)
