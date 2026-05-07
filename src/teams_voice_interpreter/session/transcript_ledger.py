"""实时同传 transcript ledger，保证播放策略不等于数据丢失。"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from teams_voice_interpreter.data.audio_segment import AudioDirection


@dataclass
class TranscriptLedger:
    """按 JSONL 追加记录实时会话的识别、译文和播放状态。"""

    path: Path
    session_id: str = field(default_factory=lambda: str(uuid4()))
    _sequence: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @classmethod
    def create_default(cls) -> TranscriptLedger:
        """创建默认会话账本，路径位于用户 cache，避免污染仓库。"""
        root = Path.home() / ".cache/teams-voice-interpreter/transcripts"
        session_id = str(uuid4())
        return cls(path=root / f"{session_id}.jsonl", session_id=session_id)

    def record_prepared(
        self,
        *,
        label: str,
        index: int,
        burst_id: int,
        direction: AudioDirection,
        source_text: str,
        target_text: str,
        preview_only: bool,
    ) -> str:
        """记录译音准备完成；streaming 场景此时可能只有首片译文。"""
        record_id = self._next_record_id(label=label, index=index)
        self._write(
            {
                "event": "prepared",
                "record_id": record_id,
                "label": label,
                "index": index,
                "burst_id": burst_id,
                "direction": direction.value,
                "source_text": source_text,
                "target_text": target_text,
                "target_text_preview_only": preview_only,
            }
        )
        return record_id

    def record_playback(
        self,
        *,
        record_id: str,
        status: str,
        target_text: str,
        reason: str = "",
        wait_seconds: float | None = None,
    ) -> None:
        """记录播放结果；target_text 应尽量填完整译文。"""
        payload: dict[str, object] = {
            "event": "playback",
            "record_id": record_id,
            "status": status,
            "target_text": target_text,
        }
        if reason:
            payload["reason"] = reason
        if wait_seconds is not None:
            payload["wait_seconds"] = round(wait_seconds, 3)
        self._write(payload)

    def record_drop(
        self,
        *,
        record_id: str,
        reason: str,
    ) -> None:
        """记录 pending 队列里的音频层丢弃；源文和译文仍已在 prepared 事件保留。"""
        self._write(
            {
                "event": "playback",
                "record_id": record_id,
                "status": "dropped_pending",
                "reason": reason,
            }
        )

    def _next_record_id(self, *, label: str, index: int) -> str:
        with self._lock:
            self._sequence += 1
            sequence = self._sequence
        safe_label = label or "listen"
        return f"{self.session_id}:{safe_label}:{index}:{sequence}"

    def _write(self, payload: dict[str, object]) -> None:
        event = {
            "schema_version": 1,
            "session_id": self.session_id,
            "recorded_at": datetime.now(UTC).isoformat(),
            **payload,
        }
        line = json.dumps(event, ensure_ascii=False, sort_keys=True)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as file:
                file.write(f"{line}\n")
