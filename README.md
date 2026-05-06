# Teams 实时双向语音同传桥

本项目实现 macOS 上的 Microsoft Teams / 钉钉双向中英实时语音同传桥。v1 使用
BlackHole、Whisper.cpp、DeepSeek streaming、Edge-TTS 与 PyAV 流式解码组成低成本同传
管线，并提供 CLI 与本地 Web 控制台。

当前真实会议入口是 `tvi duplex`。`tvi start` 保留为早期会话管理路径，不作为当前真测入口。

## 快速开始

完整流程见
[`specs/001-teams-voice-interpreter/quickstart.md`](specs/001-teams-voice-interpreter/quickstart.md)。

```bash
uv sync --extra dev
scripts/install-blackhole.sh
cp config.example.toml config.toml
$EDITOR config.toml
tvi doctor --mode realtime --confirm-teams-route
tvi say --target default "Hello world"
tvi say --target blackhole "你好"
tvi duplex --show-latency
```

Web 控制台默认绑定 `http://127.0.0.1:8765`。

`config.toml` 用于保存本机 DeepSeek API Key，已被 `.gitignore` 忽略；也可继续使用
`DEEPSEEK_API_KEY` 环境变量，环境变量优先级更高。

`tvi doctor --mode realtime` 是进入会议前的硬门禁：设备、凭证、会议软件路由、
Edge-TTS、PyAV 解码和音频写出任一项未过，都会以非 0 退出。`tvi listen` 用于单向本地校准，
`tvi duplex` 用于真实双向：默认麦克风中文上行到上行虚拟设备，会议软件扬声器英文从下行
虚拟设备进入程序，再把中文译音写到默认输出。正式会议必须使用两路不同虚拟设备，避免回灌。

## 会议软件路由

推荐使用两路独立虚拟设备：

```toml
uplink_virtual_device_name = "BlackHole 2ch"
downlink_virtual_device_name = "BlackHole 16ch" # 或 TVI Downlink / Loopback 创建的独立设备
allow_shared_virtual_device = false
```

会议软件中按以下方式选择设备：

| 会议软件设置 | 选择 |
|--------------|------|
| 麦克风 | 上行虚拟设备，例如 `BlackHole 2ch` |
| 扬声器 | 下行虚拟设备，例如 `BlackHole 16ch` |
| macOS 默认输入 | 真实麦克风 / 耳机麦克风 |
| macOS 默认输出 | 真实耳机 |

如果 `tvi duplex` 没有下行识别，先确认会议软件扬声器确实写入下行虚拟设备。可用短探针检查
`BlackHole 16ch` 是否有明显电平；只有底噪时，通常需要在会议软件里把扬声器切到真实耳机，
再切回下行虚拟设备，或退出电脑音频后重新加入。

## 命令详解

### 真实双向同传：`tvi duplex`

真实会议主入口。程序同时启动两路管线：

- 上行：macOS 默认麦克风 -> 中文 ASR -> 英文翻译/TTS -> 上行虚拟设备，例如 `BlackHole 2ch`
- 下行：会议软件扬声器输出 -> 下行虚拟设备，例如 `BlackHole 16ch` -> 英文 ASR -> 中文翻译/TTS -> macOS 默认输出

```bash
tvi duplex --show-latency
tvi duplex --chunks 5 --show-latency
tvi duplex --chunk-seconds 6 --end-silence-ms 280 --min-speech-ms 300
tvi duplex --online-asr --chunks 5 --show-latency
```

参数：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--chunk-seconds FLOAT` | `6.0` | 单段最大秒数。到达该长度会强制切段；尾部静音可更早收段。低延迟实验可手动调小，但过短会伤准确度。 |
| `--chunks INTEGER` | 不限制 | 最多处理多少个分片。真测建议先用 `--chunks 5`，方便收敛和记录。 |
| `--end-silence-ms INTEGER` | `280` | 检测到人声后，连续静音多少毫秒即收段。值越小越快，但更容易断句。 |
| `--min-speech-ms INTEGER` | `300` | 少于该时长的人声片段当作噪声丢弃。值越小越敏感，也更容易误触发。 |
| `--overlap-seconds FLOAT` | `0.6` | 强制切段时带入下一段的音频重叠秒数，减少边界漏字。 |
| `--speech-rms-threshold FLOAT` | `180.0` | 判定有效人声的 RMS 阈值。环境噪声高时可调大，识别不触发时可调小。 |
| `--show-latency / --hide-latency` | `--show-latency` | 是否打印每段延迟剖面。真测时建议保持开启。 |
| `--online-asr / --segment-asr` | `--segment-asr` | 实验模式开关。启用后持续喂音频 chunk 产生稳定 partial；默认仍使用整段 ASR，避免未经真测的高频 Whisper 重跑影响准确率或 CPU。 |
| `--online-asr-early-prepare / --no-online-asr-early-prepare` | `--no-online-asr-early-prepare` | 更激进的实验开关，必须和 `--online-asr` 同时使用。只有先用 `scripts/probe_online_asr.py` 证明 final 可确认 partial 达标后，才建议启用；否则 partial 不会提前调用 MT/TTS。 |
| `--allow-shared-virtual-device` | 关闭 | 仅临时测试用，允许上行输出和下行输入使用同一个虚拟设备。正式会议不建议启用。 |

### 单向连续监听：`tvi listen`

用于单向校准。它只监听 macOS 默认麦克风，不直接监听会议软件下行虚拟设备。

```bash
tvi listen --target blackhole --direction uplink --chunks 3
tvi listen --target default --direction downlink --chunks 3 --show-latency
tvi listen --online-asr --target default --direction uplink --chunks 3
```

参数：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--target TEXT` | `blackhole` | 发声目标。`blackhole` 写入上行虚拟设备；`default` 写入本机默认输出。 |
| `--direction auto\|uplink\|downlink` | `auto` | 翻译方向。`uplink` = 中文到英文；`downlink` = 英文到中文；`auto` 根据 `target` 推断。 |
| `--chunk-seconds FLOAT` | `6.0` | 同 `duplex`。 |
| `--chunks INTEGER` | 不限制 | 同 `duplex`。 |
| `--end-silence-ms INTEGER` | `280` | 同 `duplex`。 |
| `--min-speech-ms INTEGER` | `300` | 同 `duplex`。 |
| `--overlap-seconds FLOAT` | `0.6` | 同 `duplex`。 |
| `--speech-rms-threshold FLOAT` | `180.0` | 同 `duplex`。 |
| `--show-latency / --hide-latency` | `--show-latency` | 同 `duplex`。 |
| `--online-asr / --segment-asr` | `--segment-asr` | 同 `duplex`。 |
| `--online-asr-early-prepare / --no-online-asr-early-prepare` | `--no-online-asr-early-prepare` | 同 `duplex`。 |

### 短句发声测试：`tvi say`

用于验证 DeepSeek -> Edge-TTS -> 音频输出是否可用。它不走实时丢弃/截断策略，会完整播放。

```bash
tvi say --target default "Hello world"
tvi say --target blackhole --direction uplink "你好，我们开始会议。"
```

参数：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `TEXT` | 必填 | 要翻译并播出的短句。 |
| `--target TEXT` | `blackhole` | `blackhole` 写入上行虚拟设备；`default` 写入本机默认输出。 |
| `--direction auto\|uplink\|downlink` | `auto` | 翻译方向。`auto` 根据 `target` 推断。 |

### Push-to-talk 测试：`tvi ptt`

录一段 macOS 默认麦克风，识别、翻译并播出。适合检查麦克风权限、Whisper 模型和上行播放。

```bash
tvi ptt --seconds 3 --target blackhole
tvi ptt --seconds 5 --target default --direction downlink
```

参数：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--seconds FLOAT` | `3.0` | 每次录音秒数。录完后才识别并播出。 |
| `--target TEXT` | `blackhole` | `blackhole` 写入上行虚拟设备；`default` 写入本机默认输出。 |
| `--direction auto\|uplink\|downlink` | `auto` | 翻译方向。`auto` 根据 `target` 推断。 |

### 会前检查：`tvi doctor`

进入会议前的阻断项检查。失败会非 0 退出，并输出「发生了什么 + 下一步如何做」。
`--mode realtime` 通过只表示会议测试通路可进入，不表示首段低延迟已经达标。

```bash
tvi doctor
tvi doctor --mode realtime --confirm-teams-route
tvi doctor --deepseek-api-key-env DEEPSEEK_API_KEY
```

参数：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--mode phrase\|realtime` | `phrase` | `phrase` 检查短句播入路径；`realtime` 检查真实双向同传路径。 |
| `--confirm-teams-route` | 关闭 | 表示你已手动确认会议软件麦克风/扬声器路由。 |
| `--deepseek-api-key-env TEXT` | `DEEPSEEK_API_KEY` | DeepSeek API Key 所在环境变量名。 |

### 设备清单：`tvi devices`

列出当前 CoreAudio 输入 / 输出设备、默认设备、虚拟路由标记和真实候选。用于处理
`doctor` 报出的默认输入 / 输出设备阻断项。

```bash
tvi devices
```

该命令只读，不修改系统音频设置。

### 首次向导：`tvi wizard`

运行首次使用向导，本质上是带引导文案的会前检查。

```bash
tvi wizard
tvi wizard --confirm-teams-route
```

参数：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--confirm-teams-route` | 关闭 | 表示你已手动确认会议软件麦克风/扬声器路由。 |

### Web 控制台：`tvi serve`

启动本地 Web 控制台。

```bash
tvi serve
tvi serve --host 127.0.0.1 --port 8765
```

参数：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--host TEXT` | `127.0.0.1` | 本地 Web 控制台绑定地址。 |
| `--port INTEGER` | `8765` | 本地 Web 控制台端口，范围 `1024-65535`。 |

### 会话管理命令

```bash
tvi start
tvi pause
tvi resume
tvi status
tvi stop
```

这些命令当前无额外参数。`status` 输出 JSON 状态；`start` 是早期会话管理路径，不作为当前
Teams / 钉钉真测入口。真实会议请使用 `tvi duplex`。

`--show-latency` 会输出每段：

- `ASR`：VAD 收段后 Whisper.cpp 整段识别耗时
- `prepare墙钟`：识别完成到译文 / 流式 PCM producer 准备完成
- `排队(q,drop)`：译音等待实时播放 worker 的时间，以及入队前丢弃的旧段数；默认不主动清队列，`drop` 应为 0，若 `q>=3` 会提示播放积压
- `首PCM`：识别完成到第一块 PCM 可喂入播放
- `首写`：识别完成到 `OutputStream` callback 真正取到有效音频
- `首字节`：优先使用 `首写`，没有 callback 指标时回退到 `首PCM`
- `播放`：实时播放耗时；仅显式配置播放上限时才可能显示 `截断`

## 当前实时策略与限制

`listen` / `duplex` 是实时路径，和 `tvi say` 的完整播放路径不同：

- 实时播放默认完整播放，不再用 3 秒上限截断长句译音。
- 同一长句被切成多个连续分片时，同 burst 分片按 FIFO 保留，不因 stale 检查丢弃中间段。
- 连续长句默认最大 6 秒强制切段，切段时保留 0.6 秒音频 overlap，并在文本层去掉相邻段重叠前缀，避免 overlap 重复播；实测过短强切会降低 Whisper 准确度，因此不默认改成 2.5 秒。
- 输入流结束但尚未等到尾部静音时，会 flush 已满足最短人声阈值的尾段，避免长句最后一段静默丢失。
- 入队前默认不主动丢弃旧 burst，避免长句分片被误判成新 burst 后丢失。
- 播放队列默认不使用 3 段小队列阻塞 ASR worker；积压达到 3 段时会明确提示「内容会保留，但端到端延迟正在升高」。
- 默认不启用 stale 播放跳过；低延迟实验若显式开启 stale 窗口，也不得用于长句切割后的同一语流。
- 实时 Edge-TTS 首音频超时为 3 秒，总合成超时为 8 秒；`tts.no_audio` 短文本/网络抖动保留一次轻量重试，仍失败或非可恢复错误时丢弃该段并继续后续内容。
- `tvi say` / `play_prepared` 保持完整播放和原重试契约，用于非实时测试。

截至 2026-05-05 钉钉真测，最新下行 5 段首字节约为：

```text
1.61s / 1.65s / 1.81s / 2.00s / 1.90s
```

队列积压已基本消除，但 P1 目标 `≤ 1.5s` 尚未稳定达成。剩余主要瓶颈是：

- ASR 仍是 VAD 收段后的整段 Whisper.cpp 识别，常见 1.25-2.30 秒。
- Edge-TTS 首写常见 1.6-2.0 秒。
- 已新增 LocalAgreement 稳定增量边界和 `--online-asr` 实验入口：累计全文、增量 `delta_text` 与 final 修正标记分离；真实 pywhispercpp `new_segment_callback` 在 one-shot 模式下接近 final 时才触发，因此默认 `listen` / `duplex` 路径仍是 VAD 收段 + 6 秒兜底强制切段 + overlap 保守去重。
- 当前 `--online-asr` 通过高频重跑本地 Whisper one-shot 模拟 partial。默认不会让 partial 提前调用 MT/TTS，避免未确认文本消耗外部调用；只有显式加 `--online-asr-early-prepare` 才会启用提前准备。
- 探针会按「final 可确认的可翻译 stable partial」判定真实收益，并把实时音频到达下界计入 partial 时间；若该指标为 `n/a`，说明提前准备的 partial 会被 final 修正取消，实际不会降低首段播出延迟。
- 播放积压超过内部阈值时，实时路径会整句级清理跨 burst 旧段；同一长句被强制切段形成的同 burst 片段仍按顺序保留，避免中段丢失。
- 若要继续压低延迟，需要引入真正的 sliding / partial ASR 引擎，而不是只依赖 pywhispercpp one-shot callback。
- 后续低延迟优化不得再通过丢弃同一长句分片或截断播放来达成，必须改在 ASR partial / LocalAgreement、MT 子句增量和 TTS 热流启动上。

本地 WAV 可用 `scripts/probe_online_asr.py` 探测 `--online-asr` 的 stable partial 延迟与 final 准确率：

```bash
uv run --extra dev scripts/probe_online_asr.py /tmp/long_zh.wav \
  --expected-text "我们今天讨论现金流预测方案和下季度预算安排" \
  --max-first-partial-s 1.2 \
  --max-cer 0.12
```

输出包含首个稳定 partial、首个可翻译 stable partial、首个 final 可确认的可翻译 stable partial、final 文本、CER 与阈值失败提示；partial 时间按「实时音频到达下界 + 同步 ASR 重跑耗时」估算，`--max-first-partial-s` 按 final 可确认的可翻译 partial 判定，避免把一个字的 early partial 或离线快速喂音频耗时误当成真实低延迟收益。

## 监管严格场景免责声明

发生了什么：本工具面向普通商务交流辅助场景，v1 **不**声明适用于医疗、律师、政府、金融、
HR 绩效谈判等监管严格或高风险场景，也不替代人工专业译员或正式会议记录。

下一步如何做：如会议涉及上述场景，请停止使用本工具作为唯一翻译来源，并改用符合所在组织
合规要求的人工同传、官方认证服务或经法务 / 合规团队批准的方案。

## 宪章合规性说明

| 原则 | 状态 |
|------|------|
| I. 代码质量 | OK：`ruff` / `mypy` / `radon` 纳入 `make lint` 与 CI |
| II. 测试纪律 | OK：阶段 2 TDD 证据见 `specs/001-teams-voice-interpreter/tdd-evidence.md` |
| III. UX 一致性 | OK：用户可见错误走 `UserFacingError` 两段式与共享中文文案 |
| IV. 性能要求 | OK：BM-1..13 + BM-10D 入口见 `tests/perf/`，报告见 `perf-report.md` |

## 开发命令

```bash
make lint
make typecheck
make test
make benchmark
make coverage
```

## 贡献指南

- 行为代码遵守 TDD：先写失败测试，再实现，再重构。
- 新增用户可见文案必须进入 `src/teams_voice_interpreter/glossary/i18n/zh-CN.toml`。
- 新增音频、ASR、MT、TTS 路径必须补充 `tests/perf/` benchmark 并更新 `perf-report.md`。
- PR 必须链接对应 `spec.md`、`plan.md`、`tasks.md`。

## License

当前仓库未声明开源许可证；除非后续补充 License 文件，否则按私有项目处理。
