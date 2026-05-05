"""CoreAudio 设备发现测试。"""

import pytest

from teams_voice_interpreter.audio.routing import AudioDeviceProbe
from teams_voice_interpreter.errors import AggregateDeviceMissingError, BlackHoleMissingError


def test_probe_finds_blackhole_and_aggregate(monkeypatch: pytest.MonkeyPatch) -> None:
    """正常设备列表中应能定位 BlackHole 和聚合设备。"""
    devices = [
        {"name": "Built-in Microphone", "max_input_channels": 1, "max_output_channels": 0},
        {"name": "BlackHole 2ch", "max_input_channels": 2, "max_output_channels": 2},
        {"name": "Teams 同传聚合设备", "max_input_channels": 2, "max_output_channels": 2},
    ]
    monkeypatch.setattr("teams_voice_interpreter.audio.routing.sd.query_devices", lambda: devices)

    probe = AudioDeviceProbe()

    assert probe.find_blackhole_2ch().name == "BlackHole 2ch"
    assert probe.find_aggregate_with_blackhole().name == "Teams 同传聚合设备"


def test_probe_reports_missing_blackhole(monkeypatch: pytest.MonkeyPatch) -> None:
    """缺少 BlackHole 时必须抛两段式错误。"""
    monkeypatch.setattr("teams_voice_interpreter.audio.routing.sd.query_devices", lambda: [])

    with pytest.raises(BlackHoleMissingError):
        AudioDeviceProbe().find_blackhole_2ch()


def test_probe_reports_missing_aggregate(monkeypatch: pytest.MonkeyPatch) -> None:
    """缺少聚合设备时必须抛两段式错误。"""
    devices = [{"name": "BlackHole 2ch", "max_input_channels": 2, "max_output_channels": 2}]
    monkeypatch.setattr("teams_voice_interpreter.audio.routing.sd.query_devices", lambda: devices)

    with pytest.raises(AggregateDeviceMissingError):
        AudioDeviceProbe().find_aggregate_with_blackhole()
