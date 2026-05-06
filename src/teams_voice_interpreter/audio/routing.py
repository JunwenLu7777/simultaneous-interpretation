"""CoreAudio 设备发现与 BlackHole 路由检查。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import sounddevice as sd

from teams_voice_interpreter.errors import (
    AggregateDeviceMissingError,
    AudioDeviceMissingError,
    BlackHoleMissingError,
    UserFacingError,
)


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

    def find_input_device_by_name(self, device_name: str, *, min_channels: int = 1) -> AudioDevice:
        """按名称查找输入设备。"""
        for device in self._devices():
            if device.name == device_name and device.max_input_channels >= min_channels:
                return device
        raise AudioDeviceMissingError(
            device_name=device_name,
            direction="输入",
            min_channels=min_channels,
        )

    def find_output_device_by_name(self, device_name: str, *, min_channels: int = 1) -> AudioDevice:
        """按名称查找输出设备。"""
        for device in self._devices():
            if device.name == device_name and device.max_output_channels >= min_channels:
                return device
        raise AudioDeviceMissingError(
            device_name=device_name,
            direction="输出",
            min_channels=min_channels,
        )

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
        if int(default_input) < 0:
            raise UserFacingError(
                code="audio.default_input_missing",
                what_happened="发生了什么：未设置 macOS 默认输入设备。",
                next_action="下一步如何做：请在系统设置中选择可用麦克风，并授权终端访问麦克风。",
            )
        devices = self._devices()
        default_index = int(default_input)
        if default_index >= len(devices):
            raise UserFacingError(
                code="audio.default_input_stale",
                what_happened=f"发生了什么：macOS 默认输入设备索引 {default_index} 已不存在。",
                next_action="下一步如何做：请在系统设置中重新选择可用麦克风。",
            )
        return devices[default_index]

    def get_default_output(self) -> AudioDevice:
        """返回 sounddevice 默认输出设备。"""
        default_output = sd.default.device[1]
        if int(default_output) < 0:
            raise UserFacingError(
                code="audio.default_output_missing",
                what_happened="发生了什么：未设置 macOS 默认输出设备。",
                next_action="下一步如何做：请在系统设置中选择可用耳机或扬声器。",
            )
        devices = self._devices()
        default_index = int(default_output)
        if default_index >= len(devices):
            raise UserFacingError(
                code="audio.default_output_stale",
                what_happened=f"发生了什么：macOS 默认输出设备索引 {default_index} 已不存在。",
                next_action="下一步如何做：请在系统设置中重新选择可用耳机或扬声器。",
            )
        return devices[default_index]

    def input_devices(self) -> list[AudioDevice]:
        """返回所有带输入通道的 CoreAudio 设备。"""
        return [device for device in self._devices() if device.max_input_channels > 0]

    def output_devices(self) -> list[AudioDevice]:
        """返回所有带输出通道的 CoreAudio 设备。"""
        return [device for device in self._devices() if device.max_output_channels > 0]

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
