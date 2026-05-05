"""US2 下行端到端管线测试。"""

import numpy as np
import pytest

from teams_voice_interpreter.data.audio_segment import AudioDirection
from teams_voice_interpreter.session.manager import SessionManager


@pytest.mark.asyncio
async def test_downlink_pipeline_meets_budget() -> None:
    """英文 fixture 输入后应写出中文译音并满足首段预算。"""
    manager = SessionManager()
    samples = np.ones((16000, 2), dtype=np.int16)

    result = await manager.run_pipeline(
        direction=AudioDirection.DOWNLINK,
        samples=samples.reshape(-1),
        fixture_text="hello team",
    )

    assert "你好" in result.target_text
    assert result.first_segment_latency_ms <= 800
