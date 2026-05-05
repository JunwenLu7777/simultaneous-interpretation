"""CoreAudio 设备发现与 BlackHole 路由检查。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import sounddevice as sd

from teams_voice_interpreter.errors import AggregateDeviceMissingError, BlackHoleMissingError


@dataclass(frozen=True)
class AudioDevice:
    """sounddevice 设备的轻量视图。"""

    index: int
    name: str
    max_input_channels: int
    max_output_channels: int


class DeviceHealth(StrEnum):
    """运行期设备健康状态。"""

    HEALTHY = "healthy"
    MISSING = "missing"


class AudioDeviceProbe:
    """查询本机音频设备并定位 v1 必需设备。"""

    def find_blackhole_2ch(self) -> AudioDevice:
        """查找 BlackHole 2ch。"""
        for device in self._devices():
            if "BlackHole" in device.name and device.max_input_channels >= 2:
                return device
        raise BlackHoleMissingError()

    def find_aggregate_with_blackhole(self) -> AudioDevice:
        """查找包含 BlackHole 的聚合设备。"""
        self.find_blackhole_2ch()
        for device in self._devices():
            name = device.name.lower()
            if "blackhole" in name:
                continue
            if "aggregate" in name or "聚合" in device.name or "同传" in device.name:
                return device
        raise AggregateDeviceMissingError()

    def get_default_input(self) -> AudioDevice:
        """返回 sounddevice 默认输入设备。"""
        default_input = sd.default.device[0]
        return self._devices()[int(default_input)]

    def get_default_output(self) -> AudioDevice:
        """返回 sounddevice 默认输出设备。"""
        default_output = sd.default.device[1]
        return self._devices()[int(default_output)]

    def check_runtime_devices(self) -> dict[str, DeviceHealth]:
        """运行期检查 BlackHole 与聚合设备是否仍存在。"""
        result: dict[str, DeviceHealth] = {
            "blackhole": DeviceHealth.HEALTHY,
            "aggregate": DeviceHealth.HEALTHY,
        }
        try:
            self.find_blackhole_2ch()
        except BlackHoleMissingError:
            result["blackhole"] = DeviceHealth.MISSING
        try:
            self.find_aggregate_with_blackhole()
        except (AggregateDeviceMissingError, BlackHoleMissingError):
            result["aggregate"] = DeviceHealth.MISSING
        return result

    def _devices(self) -> list[AudioDevice]:
        raw_devices = sd.query_devices()
        return [_coerce_device(index, item) for index, item in enumerate(raw_devices)]


def _coerce_device(index: int, item: Any) -> AudioDevice:
    return AudioDevice(
        index=index,
        name=str(item["name"]),
        max_input_channels=int(item.get("max_input_channels", 0)),
        max_output_channels=int(item.get("max_output_channels", 0)),
    )
