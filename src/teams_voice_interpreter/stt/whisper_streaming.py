"""Whisper.cpp 流式 wrapper 的本地可测试实现。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

import numpy as np

from teams_voice_interpreter.audio.capture import AudioFrame
from teams_voice_interpreter.data.audio_segment import AudioDirection
from teams_voice_interpreter.data.transcript import (
    StableTranscriptChunk,
    TranscriptKind,
    TranscriptSegment,
)

_BOUNDARY_CHARS = set(" \t\n\r,.!?;:，。！？；：、")
TranscribeBuffer = Callable[[np.ndarray], str]


@dataclass(frozen=True)
class WhisperStreamingConfig:
    """Whisper streaming 基本配置。"""

    model_name: str = "small-q5_1"
    language: str = "zh"
    sample_rate_hz: int = 16000
    step_ms: int = 300
    context_seconds: int = 5
    metal: bool = True
    core_ml: bool = True


@dataclass(frozen=True)
class LocalAgreementFinal:
    """LocalAgreement final 收口结果。"""

    text: str
    delta_text: str
    revision: bool = False


class LocalAgreementCommitter:
    """把抖动 partial 收敛为可提前翻译的稳定前缀。

    Whisper partial 会随滑窗上下文反复修正。该类只提交连续两次 partial 的最长公共前缀，
    并对英文等空格分词文本避免提交半个单词；final 到来时再补齐未提交尾巴。
    """

    def __init__(self) -> None:
        self._previous_partial = ""
        self._committed_text = ""

    def accept_partial(self, text: str) -> str:
        """接收一次 partial，返回本次新增的稳定文本；没有新增则返回空串。"""
        current = _normalize_transcript_text(text)
        if not current:
            return ""
        if not self._previous_partial:
            self._previous_partial = current
            return ""
        stable_prefix = _common_prefix(self._previous_partial, current)
        commit_len = _safe_commit_length(stable_prefix)
        self._previous_partial = current
        return self._commit_prefix(stable_prefix, commit_len=commit_len)

    def accept_final(self, text: str) -> LocalAgreementFinal:
        """接收 final 文本，返回 final 全文、未提交尾巴和是否修正已提交前缀。"""
        final_text = _normalize_transcript_text(text)
        if not final_text:
            self.reset()
            return LocalAgreementFinal(text="", delta_text="")
        revision = bool(self._committed_text and not final_text.startswith(self._committed_text))
        if revision:
            delta_text = ""
        elif final_text.startswith(self._committed_text):
            delta_text = final_text[len(self._committed_text) :]
        else:
            delta_text = final_text
        result = LocalAgreementFinal(
            text=final_text,
            delta_text=delta_text.strip(),
            revision=revision,
        )
        self._previous_partial = final_text
        self._committed_text = final_text
        return result

    @property
    def committed_text(self) -> str:
        """已提交的稳定累计文本。"""
        return self._committed_text

    def reset(self) -> None:
        """开始新 segment 前清空 LocalAgreement 状态。"""
        self._previous_partial = ""
        self._committed_text = ""

    def _commit_prefix(self, stable_prefix: str, *, commit_len: int) -> str:
        if commit_len <= len(self._committed_text):
            return ""
        committed_prefix = stable_prefix[:commit_len]
        emitted = committed_prefix[len(self._committed_text) :]
        self._committed_text = committed_prefix
        return emitted.strip()


class WhisperStreamingWrapper:
    """把音频帧转换为 partial/final 片段的边界封装。"""

    def __init__(self, config: WhisperStreamingConfig | None = None) -> None:
        self.config = config or WhisperStreamingConfig()
        self.loaded = False

    def load_model(self) -> None:
        """加载模型；当前测试实现只记录状态。"""
        self.loaded = True

    def transcribe_frames(
        self,
        frames: list[AudioFrame],
        *,
        direction: AudioDirection,
        fixture_text: str | None = None,
    ) -> list[TranscriptSegment]:
        """返回一个 partial 和一个 final，保持与真实流式顺序一致。"""
        if not self.loaded:
            self.load_model()
        text = fixture_text or (
            "你好，我们开始会议。" if direction is AudioDirection.UPLINK else "hello team"
        )
        now = datetime.now(UTC)
        segment_id = uuid4()
        partial_text = text[: max(1, len(text) // 2)]
        return [
            TranscriptSegment(
                segment_id=segment_id,
                direction=direction,
                kind=TranscriptKind.PARTIAL,
                started_at=now,
                text=partial_text,
                confidence=0.8,
                provider_model=self.config.model_name,
            ),
            TranscriptSegment(
                segment_id=segment_id,
                direction=direction,
                kind=TranscriptKind.FINAL,
                started_at=now,
                ended_at=datetime.now(UTC),
                text=text,
                confidence=0.95 if frames else 0.75,
                provider_model=self.config.model_name,
            ),
        ]

    def stable_chunks_from_partial_updates(
        self,
        partial_texts: list[str],
        *,
        final_text: str,
        direction: AudioDirection,
    ) -> list[StableTranscriptChunk]:
        """把 Whisper partial 更新流过滤为稳定增量 chunks + final 收口。"""
        if not self.loaded:
            self.load_model()
        segment_id = uuid4()
        started_at = datetime.now(UTC)
        committer = LocalAgreementCommitter()
        segments: list[StableTranscriptChunk] = []
        for partial_text in partial_texts:
            delta_text = committer.accept_partial(partial_text)
            if not delta_text:
                continue
            segments.append(
                StableTranscriptChunk(
                    segment_id=segment_id,
                    direction=direction,
                    kind=TranscriptKind.PARTIAL,
                    started_at=started_at,
                    text=committer.committed_text,
                    delta_text=delta_text,
                    confidence=0.82,
                    provider_model=self.config.model_name,
                )
            )
        final = committer.accept_final(final_text)
        if final.text:
            segments.append(
                StableTranscriptChunk(
                    segment_id=segment_id,
                    direction=direction,
                    kind=TranscriptKind.FINAL,
                    started_at=started_at,
                    ended_at=datetime.now(UTC),
                    text=final.text,
                    delta_text=final.delta_text,
                    confidence=0.95,
                    revision=final.revision,
                    provider_model=self.config.model_name,
                )
            )
        return segments


class OnlineASRProcessor:
    """持续喂音频 chunk，周期性重跑 ASR，并用 LocalAgreement 输出稳定 partial。"""

    def __init__(
        self,
        *,
        direction: AudioDirection,
        transcribe_buffer: TranscribeBuffer,
        config: WhisperStreamingConfig | None = None,
    ) -> None:
        self.config = config or WhisperStreamingConfig()
        self._direction = direction
        self._transcribe_buffer = transcribe_buffer
        self._step_samples = max(1, int(self.config.step_ms * self.config.sample_rate_hz / 1000))
        self._segment_id = uuid4()
        self._started_at = datetime.now(UTC)
        self._audio_buffer = np.array([], dtype=np.int16)
        self._samples_since_process = 0
        self._committer = LocalAgreementCommitter()

    def insert_audio_chunk(self, samples: np.ndarray) -> list[StableTranscriptChunk]:
        """喂入一段 PCM；达到 step 后返回稳定 partial chunks。"""
        chunk = _normalize_audio_samples(samples)
        if chunk.size == 0:
            return []
        self._audio_buffer = np.concatenate([self._audio_buffer, chunk])
        self._samples_since_process += int(chunk.size)
        if self._samples_since_process < self._step_samples:
            return []
        self._samples_since_process = 0
        partial_text = _normalize_transcript_text(self._transcribe_buffer(self._audio_buffer))
        delta_text = self._committer.accept_partial(partial_text)
        if not delta_text:
            return []
        return [
            StableTranscriptChunk(
                segment_id=self._segment_id,
                direction=self._direction,
                kind=TranscriptKind.PARTIAL,
                started_at=self._started_at,
                text=self._committer.committed_text,
                delta_text=delta_text,
                confidence=0.82,
                provider_model=self.config.model_name,
            )
        ]

    def close_segment(self, *, final_text: str | None = None) -> list[StableTranscriptChunk]:
        """把当前在线 segment 收口为 final，并重置下一段状态。"""
        if final_text is None and self._audio_buffer.size == 0:
            self.reset()
            return []
        if final_text is None:
            final_text = self._transcribe_buffer(self._audio_buffer)
        final = self._committer.accept_final(final_text)
        if not final.text:
            self.reset()
            return []
        chunk = StableTranscriptChunk(
            segment_id=self._segment_id,
            direction=self._direction,
            kind=TranscriptKind.FINAL,
            started_at=self._started_at,
            ended_at=datetime.now(UTC),
            text=final.text,
            delta_text=final.delta_text,
            confidence=0.95,
            revision=final.revision,
            provider_model=self.config.model_name,
        )
        self.reset()
        return [chunk]

    def reset(self) -> None:
        """开始新的在线 segment。"""
        self._segment_id = uuid4()
        self._started_at = datetime.now(UTC)
        self._audio_buffer = np.array([], dtype=np.int16)
        self._samples_since_process = 0
        self._committer.reset()


def choose_model_for_budget(*, measured_ram_mb: float, measured_wer_delta: float) -> str:
    """根据资源与准确率预算选择模型档位。"""
    if measured_ram_mb <= 500 or measured_wer_delta < 5:
        return "tiny"
    return "small-q5_1"


def _normalize_transcript_text(text: str) -> str:
    return " ".join(text.split())


def _normalize_audio_samples(samples: np.ndarray) -> np.ndarray:
    return np.asarray(samples, dtype=np.int16).reshape(-1)


def _common_prefix(left: str, right: str) -> str:
    prefix_length = 0
    max_length = min(len(left), len(right))
    while prefix_length < max_length and left[prefix_length] == right[prefix_length]:
        prefix_length += 1
    return left[:prefix_length]


def _safe_commit_length(text: str) -> int:
    if not text:
        return 0
    if _contains_cjk(text):
        return len(text)
    if text[-1] in _BOUNDARY_CHARS:
        return len(text.rstrip())
    boundary = max(text.rfind(char) for char in _BOUNDARY_CHARS)
    return max(0, boundary)


def _contains_cjk(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)
