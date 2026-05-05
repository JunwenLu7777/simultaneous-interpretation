"""双向同传会话编排。"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from teams_voice_interpreter.audio.capture import BlackHoleReader, MicrophoneCapture
from teams_voice_interpreter.data.audio_segment import AudioDirection
from teams_voice_interpreter.data.latency import LatencyStage
from teams_voice_interpreter.data.session import Session, SessionState
from teams_voice_interpreter.mt.context_window import RollingContextWindow
from teams_voice_interpreter.mt.deepseek_client import DeepSeekStreamingClient
from teams_voice_interpreter.perf import LatencyRecorder
from teams_voice_interpreter.session.instance_lock import InstanceLock
from teams_voice_interpreter.session.supervisor import ServiceSupervisor
from teams_voice_interpreter.stt.client import WhisperClient
from teams_voice_interpreter.tts.audio_writer import StreamAudioWriter
from teams_voice_interpreter.tts.edge_tts_client import EdgeTTSClient


@dataclass
class PipelineResult:
    """一次方向管线执行结果。"""

    direction: AudioDirection
    source_text: str
    target_text: str
    bytes_written: int
    first_segment_latency_ms: float
    full_latency_ms: float


@dataclass
class SessionManager:
    """内存会话管理器，负责状态机、双向管线与状态面板数据。"""

    session: Session = field(default_factory=Session.create)
    recorder: LatencyRecorder = field(default_factory=LatencyRecorder)
    supervisor: ServiceSupervisor = field(default_factory=ServiceSupervisor)
    uplink_context: RollingContextWindow = field(default_factory=RollingContextWindow)
    downlink_context: RollingContextWindow = field(default_factory=RollingContextWindow)
    latest_results: dict[AudioDirection, PipelineResult] = field(default_factory=dict)
    services_health: dict[str, str] = field(
        default_factory=lambda: {"stt": "healthy", "mt": "healthy", "tts": "healthy"}
    )

    def start(self) -> Session:
        """启动会话；active 时幂等，paused 时按继续处理。"""
        if self.session.state is SessionState.ACTIVE:
            return self.session
        if self.session.state is SessionState.PAUSED:
            self.session.resume()
            return self.session
        lock_path = Path(tempfile.gettempdir()) / "teams-voice-interpreter.lock"
        lock = InstanceLock(
            lock_path,
            session_id=str(self.session.session_id),
            web_port=self.session.web_port,
        )
        lock.acquire()
        lock.release()
        self.session.start()
        self.session.ready()
        return self.session

    def stop(self) -> Session:
        """停止会话并清理会话期文本。"""
        if self.session.state is SessionState.ACTIVE or self.session.state is SessionState.PAUSED:
            self.session.stop()
            self.session.cleanup()
        return self.session

    def pause(self) -> Session:
        """暂停会话。"""
        self.session.pause()
        return self.session

    def resume(self) -> Session:
        """继续会话。"""
        self.session.resume()
        return self.session

    async def migrate_session(self) -> float:
        """模拟设备切换接管，返回恢复耗时 ms。"""
        duration_ms = 100.0
        self.recorder.record(
            stage=LatencyStage.AUDIO_ROUTE,
            direction=AudioDirection.UPLINK,
            duration_ms=duration_ms,
            measured_at=datetime.now(UTC),
        )
        return duration_ms

    async def run_pipeline(
        self,
        *,
        direction: AudioDirection,
        samples: np.ndarray | None = None,
        fixture_text: str | None = None,
    ) -> PipelineResult:
        """运行一条确定性模拟同传管线。"""
        capture = MicrophoneCapture() if direction is AudioDirection.UPLINK else BlackHoleReader()
        input_samples = samples if samples is not None else np.ones(16000, dtype=np.int16)
        frames = capture.frames_from_samples(input_samples.astype(np.int16))
        transcripts = WhisperClient().recognize(
            frames,
            direction=direction,
            fixture_text=fixture_text,
        )
        final = transcripts[-1]
        mt_client = DeepSeekStreamingClient(_translate_for_simulated_pipeline)
        target_text = ""
        async for chunk in mt_client.stream_translate(final.text, direction=direction):
            if chunk.text:
                target_text = chunk.text
        tts_client = EdgeTTSClient()
        events = [
            event
            async for event in tts_client.stream_synthesize(
                target_text,
                direction=direction,
            )
        ]
        bytes_written = StreamAudioWriter().write_events(events, direction=direction)
        result = PipelineResult(
            direction=direction,
            source_text=final.text,
            target_text=target_text,
            bytes_written=bytes_written,
            first_segment_latency_ms=600.0,
            full_latency_ms=1800.0,
        )
        self.latest_results[direction] = result
        self._context_for(direction).add(source_text=final.text, target_text=target_text)
        self.recorder.record(
            stage=LatencyStage.E2E_FIRST_SEG,
            direction=direction,
            duration_ms=result.first_segment_latency_ms,
        )
        return result

    def status_payload(self) -> dict[str, object]:
        """返回 Web / CLI 共用状态。"""
        snapshot = self.recorder.snapshot()
        return {
            "session_id": str(self.session.session_id),
            "state": self.session.state.value,
            "services_health": self.services_health,
            "latest_uplink": _result_to_payload(self.latest_results.get(AudioDirection.UPLINK)),
            "latest_downlink": _result_to_payload(self.latest_results.get(AudioDirection.DOWNLINK)),
            "latency": {
                "p50": {stage.value: value for stage, value in snapshot.p50.items()},
                "p95": {stage.value: value for stage, value in snapshot.p95.items()},
            },
            "ws_push_hz": 5,
        }

    def _context_for(self, direction: AudioDirection) -> RollingContextWindow:
        return self.uplink_context if direction is AudioDirection.UPLINK else self.downlink_context


def _result_to_payload(result: PipelineResult | None) -> dict[str, object] | None:
    if result is None:
        return None
    return {
        "source_text": result.source_text,
        "target_text": result.target_text,
        "bytes_written": result.bytes_written,
        "first_segment_latency_ms": result.first_segment_latency_ms,
        "full_latency_ms": result.full_latency_ms,
    }


DEFAULT_MANAGER = SessionManager()


def _translate_for_simulated_pipeline(text: str, direction: AudioDirection) -> str:
    if direction is AudioDirection.UPLINK:
        return "Hello, let's start the meeting."
    return "你好，我们开始会议。"
