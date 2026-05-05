"""领域数据模型验证测试。"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from teams_voice_interpreter.data.audio_segment import AudioDirection, AudioStream
from teams_voice_interpreter.data.crash import CrashReport
from teams_voice_interpreter.data.credential import ServiceCredential, ServiceKind
from teams_voice_interpreter.data.glossary import GlossaryEntry
from teams_voice_interpreter.data.latency import LatencySample, LatencyStage
from teams_voice_interpreter.data.transcript import (
    TranscriptKind,
    TranscriptSegment,
    TranslationSegment,
)


def test_audio_stream_validates_blackhole_routes() -> None:
    """上行输出必须写入 BlackHole。"""
    stream = AudioStream(
        direction=AudioDirection.UPLINK,
        source_device_name="Built-in Microphone",
        source_device_index=0,
        sink_device_name="BlackHole 2ch",
        sink_device_index=1,
    )

    assert stream.channels == 1

    with pytest.raises(ValidationError):
        AudioStream(
            direction=AudioDirection.UPLINK,
            source_device_name="Built-in Microphone",
            source_device_index=0,
            sink_device_name="MacBook Speakers",
            sink_device_index=2,
        )


def test_transcript_final_requires_end_time_and_text() -> None:
    """final 识别片段必须有结束时间与非空文本。"""
    with pytest.raises(ValidationError):
        TranscriptSegment(
            segment_id=uuid4(),
            direction=AudioDirection.UPLINK,
            kind=TranscriptKind.FINAL,
            started_at=datetime.now(UTC),
            text="",
            confidence=0.9,
        )


def test_translation_direction_controls_target_language() -> None:
    """上行只能输出英文，下行只能输出中文。"""
    segment_id = uuid4()
    translation = TranslationSegment(
        segment_id=segment_id,
        source_segment_id=segment_id,
        direction=AudioDirection.DOWNLINK,
        started_at=datetime.now(UTC),
        target_text="你好",
        target_language="zh",
    )

    assert translation.target_language == "zh"

    with pytest.raises(ValidationError):
        TranslationSegment(
            segment_id=segment_id,
            source_segment_id=segment_id,
            direction=AudioDirection.DOWNLINK,
            started_at=datetime.now(UTC),
            target_text="hello",
            target_language="en",
        )


def test_latency_sample_rejects_negative_duration() -> None:
    """延迟样本不得为负数。"""
    with pytest.raises(ValidationError):
        LatencySample(
            stage=LatencyStage.MT_FIRST_TOKEN,
            direction=AudioDirection.UPLINK,
            duration_ms=-1,
            measured_at=datetime.now(UTC),
        )


def test_service_credential_never_contains_real_key() -> None:
    """凭证模型只能保存环境变量名，不得保存真实 key。"""
    credential = ServiceCredential(
        id="deepseek-prod",
        service=ServiceKind.MT,
        provider="deepseek",
        endpoint="https://api.deepseek.com",
        key_env_var="DEEPSEEK_API_KEY",
    )

    assert credential.key_env_var == "DEEPSEEK_API_KEY"

    with pytest.raises(ValidationError):
        ServiceCredential(
            id="bad",
            service=ServiceKind.MT,
            provider="deepseek",
            endpoint="https://api.deepseek.com",
            key_env_var="sk-live-secret",
        )


def test_glossary_entry_bounds() -> None:
    """术语表条目必须有长度边界。"""
    assert GlossaryEntry(zh="福昕", en="Foxit").source == "user"

    with pytest.raises(ValidationError):
        GlossaryEntry(zh="", en="Foxit")


def test_crash_report_redacts_private_fields() -> None:
    """崩溃报告不得包含家目录绝对路径、文本、音频或 API Key。"""
    with pytest.raises(ValidationError):
        CrashReport(
            occurred_at=datetime.now(UTC),
            python_version="3.11",
            os_version="macOS",
            arch="arm64",
            dependency_versions={},
            stack_trace="/Users/alice/project failed with sk-live-secret",
            services_health_snapshot={ServiceKind.MT: False},
            resource_snapshot={"ram_mb": 100.0, "cpu_pct": 5.0},
        )
