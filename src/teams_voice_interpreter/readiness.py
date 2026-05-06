"""进入 Teams 会议前的本机 readiness 门禁。"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from io import BytesIO
from pathlib import Path
from typing import Literal, Protocol, cast

import numpy as np

from teams_voice_interpreter.audio.capture import BlackHoleReader
from teams_voice_interpreter.audio.playback import BlackHoleWriter, InMemoryAudioSink
from teams_voice_interpreter.audio.routing import AudioDevice, AudioDeviceProbe
from teams_voice_interpreter.data.audio_segment import AudioDirection
from teams_voice_interpreter.errors import UserFacingError
from teams_voice_interpreter.tts.edge_tts_client import DEFAULT_VOICES, EdgeTTSClient

_EXPECTED_SILERO_SHA256 = "2623a2953f6ff3d2c1e61740c6cdb7168133479b267dfef114a4a3cc5bdd788f"
_DEFAULT_SILERO_VAD_MODEL_PATH = Path.home() / ".cache/teams-voice-interpreter/vad/silero_vad.onnx"


class DeviceProbe(Protocol):
    """readiness 检查所需的设备探针协议。"""

    def find_blackhole_2ch(self) -> AudioDevice:
        """返回 BlackHole 2ch 设备。"""

    def find_aggregate_with_blackhole(self) -> AudioDevice:
        """返回包含 BlackHole 的聚合设备。"""

    def get_default_input(self) -> AudioDevice:
        """返回默认输入设备。"""

    def get_default_output(self) -> AudioDevice:
        """返回默认输出设备。"""

    def find_input_device_by_name(self, device_name: str, *, min_channels: int = 1) -> AudioDevice:
        """按名称返回输入设备。"""

    def find_output_device_by_name(self, device_name: str, *, min_channels: int = 1) -> AudioDevice:
        """按名称返回输出设备。"""


class CheckStatus(StrEnum):
    """readiness 检查结果。"""

    PASS = "pass"
    FAIL = "fail"
    INFO = "info"


@dataclass(frozen=True)
class ReadinessCheck:
    """单项 readiness 检查结果。"""

    key: str
    title: str
    status: CheckStatus
    detail: str
    next_action: str = ""
    required: bool = True

    @property
    def passed(self) -> bool:
        """该项是否通过。"""
        return self.status is CheckStatus.PASS


@dataclass(frozen=True)
class ReadinessReport:
    """readiness 汇总报告。"""

    checks: list[ReadinessCheck]

    @property
    def is_ready(self) -> bool:
        """所有必需项是否通过。"""
        return all(check.passed or not check.required for check in self.checks)

    @property
    def by_key(self) -> dict[str, ReadinessCheck]:
        """按 key 查找检查结果。"""
        return {check.key: check for check in self.checks}


@dataclass
class ReadinessChecker:
    """执行 Teams 使用前 readiness 检查。"""

    device_probe: DeviceProbe | None = None
    env: Mapping[str, str] | None = None
    deepseek_api_key_env: str = "DEEPSEEK_API_KEY"
    deepseek_api_key: str = ""
    teams_route_confirmed: bool = False
    mode: Literal["phrase", "realtime"] = "realtime"
    require_low_latency: bool = False
    low_latency_verified: bool = False
    require_live_pipeline: bool = True
    live_pipeline_enabled: bool = False
    uplink_virtual_device_name: str = "BlackHole 2ch"
    downlink_virtual_device_name: str = ""
    allow_shared_virtual_device: bool = False
    vad_backend: Literal["silero", "webrtc"] = "silero"
    silero_vad_model_path: Path = _DEFAULT_SILERO_VAD_MODEL_PATH

    def run(self) -> ReadinessReport:
        """执行全部阻断性检查。"""
        return ReadinessReport(
            checks=[
                self._blackhole_check(),
                self._aggregate_check(),
                self._default_input_check(),
                self._default_output_check(),
                self._deepseek_key_check(),
                self._edge_tts_voice_check(),
                self._afconvert_check(),
                self._pyav_check(),
                self._silero_vad_check(),
                self._blackhole_write_dry_run_check(),
                self._blackhole_read_dry_run_check(),
                self._teams_route_check(),
                self._live_pipeline_check(),
                self._latency_scope_check(),
            ]
        )

    @property
    def _probe(self) -> DeviceProbe:
        return self.device_probe or AudioDeviceProbe()

    @property
    def _env(self) -> Mapping[str, str]:
        return self.env if self.env is not None else os.environ

    def _blackhole_check(self) -> ReadinessCheck:
        if self.mode == "realtime":
            return self._device_check(
                key="blackhole",
                title="上行虚拟输出设备",
                lookup=lambda: self._probe.find_output_device_by_name(
                    self.uplink_virtual_device_name,
                    min_channels=2,
                ),
                next_action=(
                    "下一步如何做：请在「音频 MIDI 设置」确认上行虚拟设备存在，"
                    "或在 config.toml 更新 `uplink_virtual_device_name`。"
                ),
            )
        return self._device_check(
            key="blackhole",
            title="BlackHole 2ch",
            lookup=self._probe.find_blackhole_2ch,
            next_action="下一步如何做：请运行 `brew install blackhole-2ch`，重启 macOS 后再重试。",
        )

    def _aggregate_check(self) -> ReadinessCheck:
        if self.mode == "realtime":
            return self._device_check(
                key="aggregate",
                title="下行虚拟输入设备",
                lookup=lambda: self._probe.find_input_device_by_name(
                    self._resolved_downlink_virtual_device_name(),
                    min_channels=2,
                ),
                next_action=(
                    "下一步如何做：请在「音频 MIDI 设置」确认下行虚拟设备存在，"
                    "或在 config.toml 更新 `downlink_virtual_device_name`。"
                ),
            )
        return self._device_check(
            key="aggregate",
            title="Teams 同传聚合设备",
            lookup=self._probe.find_aggregate_with_blackhole,
            next_action=(
                "下一步如何做：请在「音频 MIDI 设置」中创建包含 BlackHole 2ch 与耳机的"
                "聚合设备，并命名为 `Teams 同传聚合`。"
            ),
        )

    def _default_input_check(self) -> ReadinessCheck:
        check = self._device_check(
            key="default_input",
            title="默认输入设备",
            lookup=self._probe.get_default_input,
            next_action="下一步如何做：请在 macOS 系统设置中选择可用麦克风，并授权终端访问麦克风。",
        )
        if not check.passed:
            return check
        device = self._probe.get_default_input()
        if device.max_input_channels <= 0:
            return self._fail(
                "default_input",
                "默认输入设备",
                f"{device.name} 没有输入通道",
                "下一步如何做：请在 macOS 系统设置中选择带输入通道的麦克风。",
            )
        if self._looks_like_virtual_route_device(device.name):
            return self._fail(
                "default_input",
                "默认输入设备",
                f"{device.name} 是虚拟路由设备，不是真实麦克风",
                _device_switch_next_action(
                    base=(
                        "下一步如何做：请把 macOS 默认输入切回 AirPods、"
                        "内置麦克风或真实外接麦克风。"
                    ),
                    candidates=self._physical_device_candidates(kind="input"),
                    empty_candidates=(
                        "当前未检测到真实输入候选；请先连接或启用真实麦克风，并确认终端有麦克风权限。"
                    ),
                ),
            )
        return check

    def _default_output_check(self) -> ReadinessCheck:
        check = self._device_check(
            key="default_output",
            title="默认输出设备",
            lookup=self._probe.get_default_output,
            next_action="下一步如何做：请在 macOS 系统设置中选择可用耳机或扬声器。",
        )
        if not check.passed:
            return check
        device = self._probe.get_default_output()
        if device.max_output_channels <= 0:
            return self._fail(
                "default_output",
                "默认输出设备",
                f"{device.name} 没有输出通道",
                "下一步如何做：请在 macOS 系统设置中选择带输出通道的耳机或扬声器。",
            )
        if self._looks_like_virtual_route_device(device.name):
            return self._fail(
                "default_output",
                "默认输出设备",
                f"{device.name} 是虚拟路由设备，不是真实耳机",
                _device_switch_next_action(
                    base="下一步如何做：请把 macOS 默认输出切回 AirPods、有线耳机或真实扬声器。",
                    candidates=self._physical_device_candidates(kind="output"),
                    empty_candidates="当前未检测到真实输出候选；请先连接或启用真实耳机/扬声器。",
                ),
            )
        return check

    def _deepseek_key_check(self) -> ReadinessCheck:
        value = self._env.get(self.deepseek_api_key_env, "") or self.deepseek_api_key
        if not value:
            return self._fail(
                "deepseek_key",
                "DeepSeek API Key",
                f"缺少环境变量 {self.deepseek_api_key_env}，且 config.toml 未填写 deepseek_api_key",
                (
                    f"下一步如何做：请运行 `export {self.deepseek_api_key_env}=sk-...`，"
                    "或在本地 config.toml 中填写 deepseek_api_key 后重试。"
                ),
            )
        return self._pass("deepseek_key", "DeepSeek API Key", _redact_secret(value))

    def _edge_tts_voice_check(self) -> ReadinessCheck:
        try:
            client = EdgeTTSClient()
            client.validate_voice(DEFAULT_VOICES[AudioDirection.UPLINK])
            client.validate_voice(DEFAULT_VOICES[AudioDirection.DOWNLINK])
        except UserFacingError as error:
            return self._fail(
                "edge_tts_voice",
                "Edge-TTS 音色",
                error.what_happened,
                error.next_action,
            )
        return self._pass("edge_tts_voice", "Edge-TTS 音色", "默认中英文音色可用")

    def _afconvert_check(self) -> ReadinessCheck:
        path = shutil.which("afconvert")
        if not path:
            return self._fail(
                "afconvert",
                "macOS 音频解码器",
                "未找到 afconvert",
                "下一步如何做：请确认当前系统是 macOS，并且 `/usr/bin` 在 PATH 中。",
            )
        return self._pass("afconvert", "macOS 音频解码器", path)

    def _pyav_check(self) -> ReadinessCheck:
        try:
            _decode_minimal_mp3_with_pyav()
        except Exception as error:
            return self._fail(
                "pyav",
                "PyAV 流式 MP3 解码器",
                f"pyav.unavailable: {error}",
                "下一步如何做：请运行 `uv sync --extra dev` 安装 PyAV 后重试。",
            )
        return self._pass("pyav", "PyAV 流式 MP3 解码器", "可导入并解码最小 MP3")

    def _silero_vad_check(self) -> ReadinessCheck:
        if self.vad_backend != "silero":
            return self._pass(
                "silero_vad",
                "Silero VAD ONNX",
                f"vad_backend={self.vad_backend}，跳过检查",
            )
        if importlib.util.find_spec("onnxruntime") is None:
            return self._fail(
                "silero_vad",
                "Silero VAD ONNX",
                "onnxruntime 未安装",
                "下一步如何做：请运行 `uv sync --extra dev` 安装依赖后重试。",
            )
        model_path = self.silero_vad_model_path
        if not model_path.exists():
            return self._fail(
                "silero_vad",
                "Silero VAD ONNX",
                f"silero_vad.onnx 不存在：{model_path}",
                "下一步如何做：请运行 `bash scripts/install-silero-vad.sh` 下载并校验模型。",
            )
        actual_sha256 = _sha256_of_file(model_path)
        if actual_sha256 != _EXPECTED_SILERO_SHA256:
            return self._fail(
                "silero_vad",
                "Silero VAD ONNX",
                f"模型 SHA256 不匹配（实际 {actual_sha256[:8]}…）",
                "下一步如何做：请重跑 `bash scripts/install-silero-vad.sh` 重新下载并校验。",
            )
        return self._pass(
            "silero_vad",
            "Silero VAD ONNX",
            f"已就位（{model_path.name}，SHA256 锁定匹配）",
        )

    def _blackhole_write_dry_run_check(self) -> ReadinessCheck:
        sink = InMemoryAudioSink()
        writer = BlackHoleWriter(sink=sink)
        written = writer.write_mono(np.array([0, 1000, -1000], dtype=np.int16))
        if written.shape != (3, 2) or sink.bytes_written <= 0:
            return self._fail(
                "blackhole_write_dry_run",
                "BlackHole 写入封装",
                "dry-run 未写出双通道 PCM",
                "下一步如何做：请先修复音频写出封装，再进入 Teams 会议。",
            )
        return self._pass("blackhole_write_dry_run", "BlackHole 写入封装", "mono 已复制为 stereo")

    def _blackhole_read_dry_run_check(self) -> ReadinessCheck:
        reader = BlackHoleReader()
        mixed = reader.downmix_stereo(np.array([[100, 300], [200, 400]], dtype=np.int16))
        if mixed.tolist() != [200, 300]:
            return self._fail(
                "blackhole_read_dry_run",
                "BlackHole 读取封装",
                "dry-run 双声道混音结果错误",
                "下一步如何做：请先修复 BlackHole 输入读取封装，再进入 Teams 会议。",
            )
        return self._pass("blackhole_read_dry_run", "BlackHole 读取封装", "stereo 已正确 downmix")

    def _teams_route_check(self) -> ReadinessCheck:
        if self.teams_route_confirmed:
            return self._pass(
                "teams_route",
                "Teams 音频路由",
                "已人工确认 Teams 麦克风和扬声器路由",
            )
        return self._fail(
            "teams_route",
            "Teams 音频路由",
            "尚未确认 Teams 麦克风/扬声器选择",
            (
                "下一步如何做：请在 Teams Settings → Devices 中确认 Microphone = 上行虚拟设备，"
                "Speaker = 下行虚拟设备，确认后重新运行 `tvi doctor --confirm-teams-route`。"
            ),
        )

    def _live_pipeline_check(self) -> ReadinessCheck:
        if self.mode == "phrase":
            return self._pass(
                "live_pipeline",
                "短句真实发声路径",
                "已接入 DeepSeek HTTP、Edge-TTS live、afconvert 解码和 sounddevice 写出",
            )
        if not self.require_live_pipeline or self.live_pipeline_enabled:
            return self._pass("live_pipeline", "真实同传管线", "已允许真实同传管线门禁通过")
        route_check = self._isolated_duplex_route_check()
        if route_check is not None:
            return route_check
        return self._pass(
            "live_pipeline",
            "真实双向同传管线",
            (
                "已接入 tvi duplex：默认麦克风上行、独立虚拟设备下行、DeepSeek HTTP、"
                "Edge-TTS live、afconvert 解码和 sounddevice 写出"
            ),
        )

    def _latency_scope_check(self) -> ReadinessCheck:
        if self.mode == "phrase":
            if self.require_low_latency:
                return self._fail(
                    "latency_scope",
                    "低延迟验收",
                    "phrase 模式只证明短句发声路径，不能验收实时同传低延迟。",
                    (
                        "下一步如何做：请改用 `tvi doctor --mode realtime --require-low-latency`，"
                        "并先接入 true streaming ASR 与探针基线。"
                    ),
                )
            return self._info(
                "latency_scope",
                "延迟范围说明",
                "phrase 模式只证明短句发声路径，不证明实时同传延迟。",
            )
        if self.require_low_latency:
            if self.low_latency_verified:
                return self._pass(
                    "latency_scope",
                    "低延迟验收",
                    "已通过 true streaming ASR 探针基线",
                )
            return self._fail(
                "latency_scope",
                "低延迟验收",
                "当前 ASR 仍以整段 Whisper.cpp 为主，尚未证明首段低延迟达标。",
                (
                    "下一步如何做：请先接入 true streaming ASR，并用 `scripts/probe_online_asr.py` "
                    "证明 final 可确认的可翻译 stable partial 达标。"
                ),
            )
        return self._info(
            "latency_scope",
            "低延迟状态说明",
            (
                "doctor 只证明会议测试通路可进入；当前 ASR 仍以整段 Whisper.cpp 为主，"
                "低延迟达标需要 true streaming ASR 和探针另行证明。"
            ),
        )

    def _isolated_duplex_route_check(self) -> ReadinessCheck | None:
        try:
            uplink_device = self._probe.find_output_device_by_name(
                self.uplink_virtual_device_name,
                min_channels=2,
            )
            downlink_device = self._probe.find_input_device_by_name(
                self._resolved_downlink_virtual_device_name(),
                min_channels=2,
            )
        except UserFacingError as error:
            return self._fail(
                "live_pipeline",
                "真实双向同传管线",
                error.what_happened,
                error.next_action,
            )
        if uplink_device.index == downlink_device.index and not self.allow_shared_virtual_device:
            return self._fail(
                "live_pipeline",
                "双向虚拟设备隔离",
                (
                    f"上行输出 `{uplink_device.name}` 与下行输入 `{downlink_device.name}` "
                    "是同一个 CoreAudio 设备，会造成回灌。"
                ),
                (
                    "下一步如何做：请使用两个不同虚拟音频设备，例如上行 `TVI Uplink`、"
                    "下行 `TVI Downlink`，并在 config.toml 设置对应名称。"
                ),
            )
        return None

    def _resolved_downlink_virtual_device_name(self) -> str:
        return self.downlink_virtual_device_name or self.uplink_virtual_device_name

    def _looks_like_virtual_route_device(self, device_name: str) -> bool:
        lower_name = device_name.lower()
        route_names = {
            self.uplink_virtual_device_name,
            self._resolved_downlink_virtual_device_name(),
        }
        return (
            device_name in route_names
            or "blackhole" in lower_name
            or "aggregate" in lower_name
            or "聚合" in device_name
            or "同传" in device_name
        )

    def _physical_device_candidates(self, *, kind: Literal["input", "output"]) -> list[AudioDevice]:
        method_name = "input_devices" if kind == "input" else "output_devices"
        method = getattr(self._probe, method_name, None)
        if not callable(method):
            return []
        devices = cast("list[AudioDevice]", method())
        return [
            device for device in devices if not self._looks_like_virtual_route_device(device.name)
        ]

    def _device_check(
        self,
        *,
        key: str,
        title: str,
        lookup: DeviceLookup,
        next_action: str,
    ) -> ReadinessCheck:
        try:
            device = lookup()
        except UserFacingError as error:
            return self._fail(key, title, error.what_happened, error.next_action)
        except Exception as error:  # pragma: no cover - defensive hardware boundary
            return self._fail(key, title, f"检查异常：{error}", next_action)
        return self._pass(key, title, f"{device.name} (index={device.index})")

    def _pass(self, key: str, title: str, detail: str) -> ReadinessCheck:
        return ReadinessCheck(key=key, title=title, status=CheckStatus.PASS, detail=detail)

    def _fail(self, key: str, title: str, detail: str, next_action: str) -> ReadinessCheck:
        return ReadinessCheck(
            key=key,
            title=title,
            status=CheckStatus.FAIL,
            detail=detail,
            next_action=next_action,
        )

    def _info(self, key: str, title: str, detail: str) -> ReadinessCheck:
        return ReadinessCheck(
            key=key,
            title=title,
            status=CheckStatus.INFO,
            detail=detail,
            required=False,
        )


DeviceLookup = Callable[[], AudioDevice]


def _device_switch_next_action(
    *,
    base: str,
    candidates: list[AudioDevice],
    empty_candidates: str,
) -> str:
    if not candidates:
        return f"{base} {empty_candidates}"
    names = ", ".join(f"{device.name} (index={device.index})" for device in candidates)
    return f"{base} 当前可选真实设备：{names}。"


def _redact_secret(value: str) -> str:
    if len(value) < 2:
        return "***"
    return f"{value[:2]}***"


def _decode_minimal_mp3_with_pyav() -> None:
    import av

    from teams_voice_interpreter.tts.audio_decode import _decode_mp3_buffer_to_pcm16

    buffer = BytesIO()
    with av.open(buffer, mode="w", format="mp3") as container:
        stream = container.add_stream("mp3", rate=16000)
        stream.layout = "mono"
        frame = av.AudioFrame.from_ndarray(
            np.zeros((1, 320), dtype=np.int16),
            format="s16",
            layout="mono",
        )
        frame.sample_rate = 16000
        for packet in stream.encode(frame):
            container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    if _decode_mp3_buffer_to_pcm16(buffer.getvalue(), sample_rate_hz=16000).size == 0:
        msg = "PyAV decoded zero samples"
        raise RuntimeError(msg)


def _sha256_of_file(path: Path) -> str:
    """计算文件 SHA256 hex digest，按 64 KB 块流式读取避免大文件 OOM。"""
    digest = hashlib.sha256()
    with path.open("rb") as fp:
        for chunk in iter(lambda: fp.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()
