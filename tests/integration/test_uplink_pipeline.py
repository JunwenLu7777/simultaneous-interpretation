"""US1 上行端到端管线测试。"""

import numpy as np
import pytest

from teams_voice_interpreter.data.audio_segment import AudioDirection
from teams_voice_interpreter.session.manager import SessionManager


@pytest.mark.asyncio
async def test_uplink_pipeline_meets_budget() -> None:
    """中文 fixture 输入后应写出英文译音并满足首段 / 整段预算。"""
    manager = SessionManager()
    samples = np.ones(16000, dtype=np.int16)

    result = await manager.run_pipeline(
        direction=AudioDirection.UPLINK,
        samples=samples,
        fixture_text="你好，我们开始会议。",
    )

    assert result.target_text
    assert result.bytes_written > 0
    assert result.first_segment_latency_ms <= 800
    assert result.full_latency_ms <= 2500
