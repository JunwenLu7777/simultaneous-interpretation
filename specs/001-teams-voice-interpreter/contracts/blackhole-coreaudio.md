# 契约：BlackHole 2ch + macOS CoreAudio 路由

**关联**：[plan.md](../plan.md) · [research.md](../research.md) §4 · [spec.md FR-002 / FR-003 / FR-005 / FR-006](../spec.md)
**实现位置**：`src/teams_voice_interpreter/audio/{capture,playback,routing}.py`

## 1. 适用范围

- **上行路由**：本系统合成英文音频 → 写入 BlackHole 2ch（CoreAudio 输出客户端） → Teams 应用麦克风源选 BlackHole 2ch → 远端听到
- **下行路由**：Teams 输出 → macOS Aggregate Device（成员 = BlackHole 2ch + 用户耳机） → BlackHole 2ch 被本系统作为输入源捕获（CoreAudio 输入客户端） + 用户耳机直接听到 Teams 原英文
- **中文回放**：本系统合成中文音频 → Mac 默认输出设备（用户耳机/扬声器）

## 2. 设备发现契约

```python
class AudioDeviceProbe:
    """启动期 + 运行期对 macOS CoreAudio 设备的发现与校验。"""

    @staticmethod
    def find_blackhole_2ch() -> AudioDevice:
        """
        通过 sounddevice.query_devices() 找到 name 包含 'BlackHole 2ch' 的设备。
        缺失时抛 BlackHoleMissingError，触发 wizard 引导。
        """

    @staticmethod
    def find_aggregate_with_blackhole() -> AudioDevice | None:
        """
        通过 CoreAudio HAL 查询 AggregateDevice，验证至少一个 Aggregate 包含 BlackHole 2ch 作为子设备。
        缺失时抛 AggregateDeviceMissingError，触发 wizard 引导。
        """

    @staticmethod
    def get_default_input() -> AudioDevice:
        """系统默认输入（通常是 MacBook 内置麦或外接耳机麦）。"""

    @staticmethod
    def get_default_output() -> AudioDevice:
        """系统默认输出（通常是用户耳机或 Mac 扬声器）。"""
```

## 3. 上行路由契约（FR-002）

### 3.1 写入 BlackHole 2ch

```python
class BlackHoleWriter:
    """
    把流式 PCM16 音频写入 BlackHole 2ch（作为 sounddevice 输出客户端）。
    Teams 麦克风源选 BlackHole 2ch 后即可接收。
    """

    def __init__(
        self,
        device_index: int,                # BlackHole 2ch 的 sounddevice index
        sample_rate_hz: int = 16000,
        channels: int = 2,                # BlackHole 2ch 是 2 通道；mono 输入需复制到双通道
        blocksize_frames: int = 320,      # 20 ms @ 16 kHz
    ): ...

    async def open(self) -> None:
        """启动 sounddevice OutputStream。"""

    async def write(self, pcm16_mono: bytes) -> None:
        """写入 mono PCM16；内部复制到双通道写出。"""

    async def close(self) -> None: ...
```

### 3.2 Teams 麦克风源切换

**用户操作**（由 wizard 在 FR-006 步骤 (c) 引导）：

1. 打开 Microsoft Teams
2. Settings → Devices → Microphone
3. 下拉选择 `BlackHole 2ch`
4. 用 Teams 内置「测试通话」验证

**自动验证**：本系统**无法**直接读取 Teams 应用配置（避免触碰 Teams 插件红线），改用「在 BlackHole 写入测试音 + 用户在 Teams 通话中确认是否听到」的回环验证。

## 4. 下行路由契约（FR-003）

### 4.1 Aggregate Device 配置

**用户操作**（由 wizard 在 FR-006 步骤 (b) 引导）：

1. 打开「应用 → 实用工具 → 音频 MIDI 设置」
2. 左下角 `+` → 创建 Aggregate Device，命名为 `Teams 同传聚合`
3. 在右侧设备列表勾选：
   - `BlackHole 2ch`
   - 用户当前耳机（如 `MacBook Pro Speakers` 或 `AirPods Pro`）
4. 设置 Master Device = 用户耳机（保证时钟同步）

**自动验证**：通过 CoreAudio HAL `AudioObjectGetPropertyData(kAudioObjectPropertyOwnedObjects)` 列举所有 AggregateDevice，校验其子设备列表含 BlackHole 2ch。

### 4.2 Teams 扬声器源切换

**用户操作**（wizard 步骤 (c)）：

1. Teams Settings → Devices → Speaker
2. 下拉选择 `Teams 同传聚合`（或用户自定义命名的 Aggregate Device）

### 4.3 BlackHole 2ch 输入捕获

```python
class BlackHoleReader:
    """
    把 BlackHole 2ch 作为输入设备捕获 Teams 输出音频。
    """

    def __init__(
        self,
        device_index: int,                  # BlackHole 2ch 在输入侧的 index（与输出侧相同）
        sample_rate_hz: int = 16000,
        channels: int = 1,                   # 双通道立体声 → mono（简单平均）
        blocksize_frames: int = 480,        # 30 ms @ 16 kHz，匹配 Whisper.cpp 帧
    ): ...

    async def open(self) -> None:
        """启动 sounddevice InputStream。"""

    async def read(self) -> AsyncIterator[bytes]:
        """yield 30 ms PCM16 mono 帧。"""

    async def close(self) -> None: ...
```

## 5. 中文译音回放契约

```python
class DefaultOutputWriter:
    """
    把下行 TTS 合成的中文音频写到 Mac 默认输出（用户耳机），与 Teams 原英文在用户耳机端混合。
    """

    def __init__(
        self,
        device_index: int,                  # sounddevice.default.device[1]
        sample_rate_hz: int = 16000,
        channels: int = 2,                   # 大多数耳机要求 stereo；mono 复制到双通道
        blocksize_frames: int = 320,
    ): ...
    # API 同 BlackHoleWriter
```

## 6. 路由完整性自检清单（FR-006 wizard）

| 编号 | 检查项 | 通过条件 | 失败动作 |
|------|--------|----------|----------|
| RT-1 | BlackHole 2ch 已安装 | `sounddevice.query_devices()` 含名为 'BlackHole 2ch' 的设备 | 提示 `brew install blackhole-2ch` + 重启 |
| RT-2 | Aggregate Device 已创建并含 BlackHole 2ch | CoreAudio HAL 查询 ≥ 1 个 Aggregate 含 BlackHole | 提示打开「音频 MIDI 设置」+ 配置截图 |
| RT-3 | Teams 麦克风源 | 用户主观确认（无法自动检测） | 提示 Teams 设置路径截图 |
| RT-4 | Teams 扬声器源 | 用户主观确认 | 同上 |
| RT-5 | macOS 麦克风权限 | `AVCaptureDevice.authorizationStatus(.audio) == .authorized` | 提示「系统设置 → 隐私与安全性 → 麦克风」 |
| RT-6 | 默认输出可写 | 短促测试音验证 | 提示检查耳机连接 |

## 7. 性能 SLA

| 指标 | 预算 | 测量位置 |
|------|------|----------|
| BlackHole 路由开销（写入到 Teams 听到） | ≤ 50 ms | BM-8 |
| Aggregate Device jitter | ≤ 10 ms | BM-9 |
| `sounddevice` callback 抖动 | ≤ 5 ms | 单元测试 |

## 8. 错误处理契约

| 错误 | 客户端动作 |
|------|-----------|
| `PortAudioError`（设备占用） | 抛 `UserFacingError`（"BlackHole 2ch 被其他应用占用，请关闭后重试"） |
| 设备运行时消失（蓝牙断开） | FR-020 立即停止该方向 + 两段式提示（"耳机已断开。请重连后点击重启同传"） |
| Buffer underrun（采样率不匹配） | 自动重采样（`numpy.interp`）；若仍失败则降级到 PCM16 默认采样 |
| BlackHole 通道数 mismatch | 自动 mono ↔ stereo 转换 |

## 9. 契约测试要求

`tests/contract/test_audio_routing.py` 必须覆盖：

- ✅ BlackHole 2ch 设备发现（mock CoreAudio 设备列表）
- ✅ Aggregate Device 检测
- ✅ 上行 mono → BlackHole 2ch 双通道复制正确
- ✅ 下行 BlackHole 双通道 → mono 平均正确
- ✅ 设备消失时的 FR-020 异常路径
- ✅ 100% 分支覆盖

## 10. 平台约束声明

- **仅 macOS 13+**：CoreAudio HAL API 在不同 macOS 版本可能有 ABI 差异；最低版本由 `pyobjc` 与 `sounddevice` 决定
- **Apple Silicon 优先**：Metal + Core ML 加速（用于 Whisper.cpp）；Intel x86_64 兼容但性能下降
- **必须**用户已 `brew install blackhole-2ch` 并重启过 macOS
- **必须**Aggregate Device 已配置（wizard 引导，不自动创建——避免触碰 sudo / 系统级修改）

## 11. 安全 / 隐私

- **不得**默认录制原始音频（FR-023）
- 调试模式可启用音频录制到 `~/.cache/teams-voice-interpreter/debug-audio/`，但**必须**在 wizard 中显式开启 + 在状态面板顶部红色提示 "原始音频录制中"
- 设备名称（用户耳机型号）**不**写入崩溃报告（FR-029）
