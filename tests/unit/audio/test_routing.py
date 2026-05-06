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


def test_probe_finds_named_virtual_input_and_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """双向隔离模式应能按名称定位两路虚拟设备。"""
    devices = [
        {"name": "TVI Uplink", "max_input_channels": 2, "max_output_channels": 2},
        {"name": "TVI Downlink", "max_input_channels": 2, "max_output_channels": 2},
    ]
    monkeypatch.setattr("teams_voice_interpreter.audio.routing.sd.query_devices", lambda: devices)

    probe = AudioDeviceProbe()

    assert probe.find_output_device_by_name("TVI Uplink", min_channels=2).index == 0
    assert probe.find_input_device_by_name("TVI Downlink", min_channels=2).index == 1


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


def test_probe_rejects_unset_default_input(monkeypatch: pytest.MonkeyPatch) -> None:
    """sounddevice 默认输入为 -1 时不得误用最后一个设备。"""
    devices = [{"name": "Speaker", "max_input_channels": 0, "max_output_channels": 2}]
    monkeypatch.setattr("teams_voice_interpreter.audio.routing.sd.query_devices", lambda: devices)
    monkeypatch.setattr("teams_voice_interpreter.audio.routing.sd.default.device", [-1, 0])

    with pytest.raises(Exception, match="默认输入设备"):
        AudioDeviceProbe().get_default_input()


def test_probe_rejects_stale_default_input_index(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认输入索引指向已移除设备时必须 fail-closed，而不是越界异常。"""
    devices = [{"name": "Speaker", "max_input_channels": 0, "max_output_channels": 2}]
    monkeypatch.setattr("teams_voice_interpreter.audio.routing.sd.query_devices", lambda: devices)
    monkeypatch.setattr("teams_voice_interpreter.audio.routing.sd.default.device", [4, 0])

    with pytest.raises(Exception, match="默认输入设备索引 4 已不存在"):
        AudioDeviceProbe().get_default_input()


def test_probe_rejects_stale_default_output_index(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认输出索引指向已移除设备时必须 fail-closed，而不是越界异常。"""
    devices = [{"name": "Mic", "max_input_channels": 1, "max_output_channels": 0}]
    monkeypatch.setattr("teams_voice_interpreter.audio.routing.sd.query_devices", lambda: devices)
    monkeypatch.setattr("teams_voice_interpreter.audio.routing.sd.default.device", [0, 9])

    with pytest.raises(Exception, match="默认输出设备索引 9 已不存在"):
        AudioDeviceProbe().get_default_output()
