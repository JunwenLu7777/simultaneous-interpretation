"""Teams 使用前 readiness 门禁测试。"""

import os

from teams_voice_interpreter import readiness as readiness_mod
from teams_voice_interpreter.audio.routing import AudioDevice
from teams_voice_interpreter.errors import BlackHoleMissingError
from teams_voice_interpreter.readiness import CheckStatus, ReadinessChecker


class PassingProbe:
    """返回完整可用设备的测试探针。"""

    def find_blackhole_2ch(self) -> AudioDevice:
        return AudioDevice(1, "BlackHole 2ch", 2, 2)

    def find_aggregate_with_blackhole(self) -> AudioDevice:
        return AudioDevice(2, "Teams 同传聚合", 2, 2)

    def get_default_input(self) -> AudioDevice:
        return AudioDevice(3, "Built-in Microphone", 1, 0)

    def get_default_output(self) -> AudioDevice:
        return AudioDevice(4, "Headphones", 0, 2)

    def find_input_device_by_name(self, device_name: str, *, min_channels: int = 1) -> AudioDevice:
        del min_channels
        if device_name == "TVI Downlink":
            return AudioDevice(5, "TVI Downlink", 2, 2)
        return AudioDevice(1, device_name, 2, 2)

    def find_output_device_by_name(self, device_name: str, *, min_channels: int = 1) -> AudioDevice:
        del min_channels
        return AudioDevice(1, device_name, 2, 2)


class MissingBlackHoleProbe(PassingProbe):
    """模拟 BlackHole 缺失。"""

    def find_blackhole_2ch(self) -> AudioDevice:
        raise BlackHoleMissingError()


class VirtualDefaultProbe(PassingProbe):
    """模拟把系统默认输入 / 输出误设为虚拟路由设备。"""

    def get_default_input(self) -> AudioDevice:
        return AudioDevice(1, "BlackHole 2ch", 2, 2)

    def get_default_output(self) -> AudioDevice:
        return AudioDevice(2, "Teams 同传聚合", 2, 2)


class VirtualDefaultWithCandidatesProbe(VirtualDefaultProbe):
    """模拟默认设备是虚拟路由，但系统能看到真实候选设备。"""

    def input_devices(self) -> list[AudioDevice]:
        return [
            AudioDevice(1, "BlackHole 2ch", 2, 2),
            AudioDevice(3, "外置麦克风", 1, 0),
        ]

    def output_devices(self) -> list[AudioDevice]:
        return [
            AudioDevice(2, "Teams 同传聚合", 0, 2),
            AudioDevice(4, "外置耳机", 0, 2),
        ]


class VirtualDefaultWithoutPhysicalInputProbe(VirtualDefaultProbe):
    """模拟当前 Mac mini：输入设备只有虚拟路由。"""

    def input_devices(self) -> list[AudioDevice]:
        return [
            AudioDevice(1, "BlackHole 16ch", 16, 16),
            AudioDevice(2, "BlackHole 2ch", 2, 2),
        ]

    def output_devices(self) -> list[AudioDevice]:
        return [
            AudioDevice(2, "BlackHole 2ch", 2, 2),
            AudioDevice(4, "Mac mini扬声器", 0, 2),
        ]


def test_readiness_passes_only_after_teams_route_confirmation() -> None:
    """Teams 路由必须人工确认后才允许进入会议。"""
    report = ReadinessChecker(
        device_probe=PassingProbe(),
        env={"DEEPSEEK_API_KEY": "sk-test"},
        teams_route_confirmed=True,
        require_live_pipeline=False,
    ).run()

    assert report.is_ready
    assert report.by_key["teams_route"].status is CheckStatus.PASS


def test_readiness_blocks_without_teams_route_confirmation() -> None:
    """未确认 Teams 麦克风/扬声器路由时不得假装可用。"""
    report = ReadinessChecker(
        device_probe=PassingProbe(),
        env={"DEEPSEEK_API_KEY": "sk-test"},
        teams_route_confirmed=False,
        require_live_pipeline=False,
    ).run()

    assert not report.is_ready
    assert report.by_key["teams_route"].status is CheckStatus.FAIL
    assert "Teams Settings" in report.by_key["teams_route"].next_action


def test_readiness_reports_missing_blackhole() -> None:
    """BlackHole 缺失必须成为阻断项。"""
    report = ReadinessChecker(
        device_probe=MissingBlackHoleProbe(),
        env={"DEEPSEEK_API_KEY": "sk-test"},
        teams_route_confirmed=True,
        mode="phrase",
        require_live_pipeline=False,
    ).run()

    assert not report.is_ready
    assert report.by_key["blackhole"].status is CheckStatus.FAIL
    assert "brew install blackhole-2ch" in report.by_key["blackhole"].next_action


def test_readiness_checks_deepseek_key_env_name() -> None:
    """DeepSeek key 使用配置指定的环境变量名。"""
    report = ReadinessChecker(
        device_probe=PassingProbe(),
        env={"CUSTOM_DEEPSEEK_KEY": "sk-test"},
        deepseek_api_key_env="CUSTOM_DEEPSEEK_KEY",
        teams_route_confirmed=True,
        require_live_pipeline=False,
    ).run()

    assert report.by_key["deepseek_key"].status is CheckStatus.PASS


def test_readiness_accepts_deepseek_key_from_config() -> None:
    """readiness 可接受本地 config.toml 中的 DeepSeek API Key。"""
    report = ReadinessChecker(
        device_probe=PassingProbe(),
        env={},
        deepseek_api_key="sk-config",
        teams_route_confirmed=True,
        mode="phrase",
    ).run()

    assert report.is_ready
    assert report.by_key["deepseek_key"].detail == "sk***"


def test_readiness_uses_os_environ_by_default(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """默认环境来源应是当前进程环境。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")

    report = ReadinessChecker(
        device_probe=PassingProbe(),
        teams_route_confirmed=True,
        require_live_pipeline=False,
    ).run()

    assert report.by_key["deepseek_key"].detail == os.environ["DEEPSEEK_API_KEY"][:2] + "***"


def test_readiness_realtime_mode_allows_live_duplex_path() -> None:
    """实时模式应按已接入的双向真实管线放行。"""
    report = ReadinessChecker(
        device_probe=PassingProbe(),
        env={"DEEPSEEK_API_KEY": "sk-test"},
        teams_route_confirmed=True,
        downlink_virtual_device_name="TVI Downlink",
    ).run()

    assert report.is_ready
    assert report.by_key["live_pipeline"].status is CheckStatus.PASS
    assert "tvi duplex" in report.by_key["live_pipeline"].detail
    assert report.by_key["latency_scope"].status is CheckStatus.INFO
    assert not report.by_key["latency_scope"].required
    assert "只证明会议测试通路" in report.by_key["latency_scope"].detail


def test_readiness_realtime_mode_blocks_shared_virtual_device() -> None:
    """实时模式默认必须阻断单虚拟设备双向回灌风险。"""
    report = ReadinessChecker(
        device_probe=PassingProbe(),
        env={"DEEPSEEK_API_KEY": "sk-test"},
        teams_route_confirmed=True,
    ).run()

    assert not report.is_ready
    assert report.by_key["live_pipeline"].status is CheckStatus.FAIL
    assert "同一个 CoreAudio 设备" in report.by_key["live_pipeline"].detail


def test_readiness_blocks_virtual_devices_as_mac_defaults() -> None:
    """macOS 默认输入 / 输出必须是真实麦克风和耳机，不能是虚拟路由设备。"""
    report = ReadinessChecker(
        device_probe=VirtualDefaultProbe(),
        env={"DEEPSEEK_API_KEY": "sk-test"},
        teams_route_confirmed=True,
        downlink_virtual_device_name="TVI Downlink",
    ).run()

    assert not report.is_ready
    assert report.by_key["default_input"].status is CheckStatus.FAIL
    assert report.by_key["default_output"].status is CheckStatus.FAIL
    assert "真实麦克风" in report.by_key["default_input"].detail
    assert "真实耳机" in report.by_key["default_output"].detail


def test_readiness_lists_physical_candidates_for_virtual_defaults() -> None:
    """默认设备错误时，应列出可切换的真实输入/输出候选。"""
    report = ReadinessChecker(
        device_probe=VirtualDefaultWithCandidatesProbe(),
        env={"DEEPSEEK_API_KEY": "sk-test"},
        teams_route_confirmed=True,
        downlink_virtual_device_name="TVI Downlink",
    ).run()

    assert not report.is_ready
    assert "外置麦克风 (index=3)" in report.by_key["default_input"].next_action
    assert "外置耳机 (index=4)" in report.by_key["default_output"].next_action


def test_readiness_reports_empty_physical_input_candidates() -> None:
    """没有真实输入候选时，readiness 必须直接说清楚不是单纯选错默认设备。"""
    report = ReadinessChecker(
        device_probe=VirtualDefaultWithoutPhysicalInputProbe(),
        env={"DEEPSEEK_API_KEY": "sk-test"},
        teams_route_confirmed=True,
        downlink_virtual_device_name="TVI Downlink",
    ).run()

    assert not report.is_ready
    assert "未检测到真实输入候选" in report.by_key["default_input"].next_action
    assert "Mac mini扬声器 (index=4)" in report.by_key["default_output"].next_action


def test_readiness_phrase_mode_allows_short_phrase_path() -> None:
    """短句模式应按已接入的真实发声路径放行。"""
    report = ReadinessChecker(
        device_probe=PassingProbe(),
        env={"DEEPSEEK_API_KEY": "sk-test"},
        teams_route_confirmed=True,
        mode="phrase",
    ).run()

    assert report.is_ready
    assert report.by_key["live_pipeline"].title == "短句真实发声路径"


def test_readiness_includes_pyav_decode_check() -> None:
    """readiness 必须检查 PyAV 可导入且能解码最小 MP3。"""
    report = ReadinessChecker(
        device_probe=PassingProbe(),
        env={"DEEPSEEK_API_KEY": "sk-test"},
        teams_route_confirmed=True,
        mode="phrase",
    ).run()

    assert report.by_key["pyav"].status is CheckStatus.PASS
    assert "PyAV" in report.by_key["pyav"].title


def test_readiness_reports_pyav_unavailable(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """PyAV 不可用时必须返回 pyav.unavailable 和安装建议。"""

    def fail_decode() -> None:
        raise RuntimeError("missing av")

    monkeypatch.setattr(readiness_mod, "_decode_minimal_mp3_with_pyav", fail_decode)

    report = ReadinessChecker(
        device_probe=PassingProbe(),
        env={"DEEPSEEK_API_KEY": "sk-test"},
        teams_route_confirmed=True,
        mode="phrase",
    ).run()

    assert not report.is_ready
    assert report.by_key["pyav"].status is CheckStatus.FAIL
    assert "pyav.unavailable" in report.by_key["pyav"].detail
    assert "uv sync" in report.by_key["pyav"].next_action


def test_readiness_silero_check_skipped_for_webrtc_backend(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """vad_backend=webrtc 时 silero 检查必须跳过为 PASS。"""
    report = ReadinessChecker(
        device_probe=PassingProbe(),
        env={"DEEPSEEK_API_KEY": "sk-test"},
        teams_route_confirmed=True,
        mode="phrase",
        vad_backend="webrtc",
        silero_vad_model_path=tmp_path / "absent.onnx",
    ).run()

    assert report.by_key["silero_vad"].status is CheckStatus.PASS
    assert "vad_backend=webrtc" in report.by_key["silero_vad"].detail


def test_readiness_silero_check_fails_when_model_missing(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """silero 模式下模型文件缺失必须 FAIL 并指向 install 脚本。"""
    report = ReadinessChecker(
        device_probe=PassingProbe(),
        env={"DEEPSEEK_API_KEY": "sk-test"},
        teams_route_confirmed=True,
        mode="phrase",
        vad_backend="silero",
        silero_vad_model_path=tmp_path / "missing.onnx",
    ).run()

    assert not report.is_ready
    assert report.by_key["silero_vad"].status is CheckStatus.FAIL
    assert "scripts/install-silero-vad.sh" in report.by_key["silero_vad"].next_action


def test_readiness_silero_check_fails_on_sha_mismatch(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """silero 模式下 SHA256 不匹配必须 FAIL 并要求重新运行 install 脚本。"""
    fake_model = tmp_path / "silero_vad.onnx"
    fake_model.write_bytes(b"corrupted")

    report = ReadinessChecker(
        device_probe=PassingProbe(),
        env={"DEEPSEEK_API_KEY": "sk-test"},
        teams_route_confirmed=True,
        mode="phrase",
        vad_backend="silero",
        silero_vad_model_path=fake_model,
    ).run()

    assert not report.is_ready
    assert report.by_key["silero_vad"].status is CheckStatus.FAIL
    assert "SHA256 不匹配" in report.by_key["silero_vad"].detail
