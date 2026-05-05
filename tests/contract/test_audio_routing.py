"""BlackHole / CoreAudio 契约测试。"""

import numpy as np
import pytest

from teams_voice_interpreter.audio.capture import BlackHoleReader
from teams_voice_interpreter.audio.routing import AudioDeviceProbe, DeviceHealth


def test_blackhole_stereo_to_mono_conversion() -> None:
    """BlackHole 双通道输入必须平均为 mono。"""
    stereo = np.array([[100, 300], [200, 400]], dtype=np.int16)

    mono = BlackHoleReader().downmix_stereo(stereo)

    assert mono.tolist() == [200, 300]


def test_runtime_device_disappearance(monkeypatch: pytest.MonkeyPatch) -> None:
    """设备运行中消失必须暴露 missing 状态。"""
    monkeypatch.setattr("teams_voice_interpreter.audio.routing.sd.query_devices", lambda: [])

    result = AudioDeviceProbe().check_runtime_devices()

    assert result["blackhole"] is DeviceHealth.MISSING
    assert result["aggregate"] is DeviceHealth.MISSING
