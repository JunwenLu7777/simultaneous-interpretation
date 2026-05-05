# 快速上手：Teams 实时双向语音同传桥（macOS）

**关联**：[plan.md](plan.md) · [spec.md](spec.md)
**目标用户**：以中文为母语、参加英语 Teams 商务会议的 macOS 用户
**预计完成时间**：≤ 15 分钟（SC-007）

> ⚠️ **监管严格场景免责**（SC-011）：本工具仅适用于**普通商务交流场景**。**不**适用于医疗诊疗、律师-客户特权对话、政府或机要事务、金融合规通话、HR 与员工敏感事务等任何对录音 / 转写 / 翻译有特殊法律义务的场景。用户**必须**自行确保所在司法管辖区（GDPR、《个人信息保护法》、CIPA 等）下的合规性，包括事先告知远端会议方"本端正在使用语音转写与机器翻译"。

---

## 1. 先决条件

| 项 | 要求 |
|----|------|
| 操作系统 | macOS 13 (Ventura) 及以上 |
| 硬件 | Apple Silicon Mac 优先；Intel x86_64 兼容但性能受限 |
| Python | 3.11 或更高 |
| Homebrew | 已安装（[brew.sh](https://brew.sh/)） |
| 网络 | 可访问 `api.deepseek.com` 和 `speech.platform.bing.com`（Edge-TTS） |
| Microsoft Teams 桌面端 | 已安装 + 账号已登录 |
| DeepSeek API Key | 在 [platform.deepseek.com](https://platform.deepseek.com/) 注册并领取 |
| 内置麦克风 | 已可用（系统设置 → 隐私与安全性 → 麦克风可见） |
| 耳机 / 扬声器 | 推荐**有线或蓝牙耳机**；外放扬声器场景下回声抑制不在 v1 强保证范围 |

---

## 2. 安装（首次约 5 分钟）

```bash
# 1) 克隆代码
git clone https://github.com/<your-org>/teams-voice-interpreter.git
cd teams-voice-interpreter

# 2) 创建并激活虚拟环境
python3.11 -m venv .venv
source .venv/bin/activate

# 3) 安装本系统及依赖
pip install -e .

# 4) 安装 BlackHole 2ch（虚拟音频驱动；首次必须）
brew install blackhole-2ch

# 5) 重启 macOS（让 BlackHole 驱动注册）
sudo shutdown -r now
```

> 💡 安装完 BlackHole 必须重启一次 macOS。重启后回到本目录继续。

---

## 3. 首次运行向导（约 5 分钟）

```bash
source .venv/bin/activate
tvi wizard            # 等价于 python -m teams_voice_interpreter wizard
```

向导会按步骤检查并引导：

### 步骤 (a) 验证 BlackHole 2ch 安装

向导通过 `sounddevice.query_devices()` 确认 BlackHole 2ch 已注册为 CoreAudio 设备。失败时会给出可点击的安装指引。

### 步骤 (b) 创建 Aggregate Device

向导显示分步截图，引导你：

1. 打开 `应用 → 实用工具 → 音频 MIDI 设置`
2. 左下角点 `+` → 「创建多输出设备」（也可用 Aggregate Device，效果相同）
3. 命名为 `Teams 同传聚合`
4. 在右侧勾选：
   - `BlackHole 2ch`
   - 你的当前耳机（如 `MacBook Pro 扬声器` 或 `AirPods Pro`）
5. **Master Device 选你的耳机**（保证时钟同步）

向导通过 CoreAudio HAL 自动检测 Aggregate Device 是否含 BlackHole 2ch；通过则进入下一步。

### 步骤 (c) 配置 Microsoft Teams

向导提示：

1. 打开 Microsoft Teams
2. `Settings → Devices`
3. **Microphone** 下拉选择 `BlackHole 2ch`
4. **Speaker** 下拉选择 `Teams 同传聚合`
5. 点 Teams 内置「Make a test call」验证

向导通过本系统在 BlackHole 写入测试音 + 提示你在 Teams 测试通话中确认是否听到来回环验证。

### 步骤 (d) 授予麦克风权限

向导引导你前往 `系统设置 → 隐私与安全性 → 麦克风`，勾选你的终端 (Terminal) 或 IDE。

### 步骤 (e) 配置 DeepSeek API Key

向导提示你把 API Key 写入环境变量（推荐）：

```bash
echo 'export DEEPSEEK_API_KEY="sk-xxxxx"' >> ~/.zshrc
source ~/.zshrc
```

或写到本地 `.env`（仅开发用，**不要 commit**）：

```bash
echo 'DEEPSEEK_API_KEY=sk-xxxxx' > .env
```

向导通过一次轻量 ping 调用 DeepSeek API 验证凭证。

### 步骤 (f)（可选）配置专有名词术语表

向导提示你在 `~/.config/teams-voice-interpreter/glossary.toml` 中维护专有名词中英对照（FR-012）：

```toml
[[entries]]
zh = "K8s"
en = "K8s"
note = "Kubernetes 缩写，不展开"

[[entries]]
zh = "DeepSeek"
en = "DeepSeek"
note = "保留品牌名"

[[entries]]
zh = "福昕"
en = "Foxit"
```

最多 200 条。空文件或缺失文件 → 不报错，仅依赖 LLM 默认能力。

### 步骤 (g) 阅读监管严格场景免责声明

向导显示完整 SC-011 免责声明文本，**必须勾选「我已阅读并同意」+ 时间戳**才能完成向导。未确认时无法启动同传会话。

---

## 4. 启动同传

### 方式 1：Web 控制台（推荐）

```bash
tvi start
```

CLI 输出：

```text
[10:30:00] 同传桥已启动 SessionId=0193bc12-...-7f21
[10:30:00] Web 控制台 http://localhost:8765
[10:30:00] 上行：用户麦 → BlackHole 2ch
[10:30:00] 下行：BlackHole 2ch → 用户耳机（中文译音）
按 Ctrl+C 停止；或在 Web 控制台点击「停止」按钮。
```

打开浏览器访问 [http://localhost:8765](http://localhost:8765)。Web 控制台显示：

- 当前会话状态与运行时长
- 双向最近一段识别原文与译文
- 首段译音延迟 / 端到端延迟（滚动 p50/p95）
- DeepSeek / Whisper / Edge-TTS 三服务连接健康状态
- 「开始 / 暂停 / 继续 / 停止 / 导出」按钮

首次访问会请求**浏览器原生通知权限**（用于会议中异常告警），建议同意。

### 方式 2：仅 CLI

```bash
tvi start              # 启动
tvi pause              # 暂停
tvi resume             # 继续
tvi stop               # 停止
tvi status             # 当前状态
```

---

## 5. 使用：进入 Teams 会议

1. 在 Teams 中加入或发起会议
2. 确认 Teams 设置中麦克风源仍是 `BlackHole 2ch`、扬声器源仍是 `Teams 同传聚合`
3. 用中文自然发言；远端会议方应在 ≤ 800 ms 内开始听到流式英文译音
4. 远端用英文发言；你应在 ≤ 800 ms 内开始听到流式中文译音

会议中可在 Web 控制台实时查看：

- 你说了什么 / 对方说了什么
- 每段被翻译为什么
- 当前延迟健康度

---

## 6. 结束会议：导出对话记录（可选）

会议结束后：

1. 在 Web 控制台点「停止」按钮（或 `tvi stop`）
2. 控制台会显示一个 30 秒倒计时的「导出对话记录」按钮
3. 点击下载，得到 `teams-session-2026-05-05T10-30-00.md`，含双向时间戳 + 中英对照
4. 30 秒后内存自动释放，导出按钮置灰

> 💡 v1 **不**支持自动持久化、**不**支持 JSON / SRT / 字幕格式（FR-024）。仅在用户**主动**点导出按钮时才会写文件。

---

## 7. 常见故障排查

### 远端听不到我的译音

检查：

1. Teams 麦克风源是否仍是 `BlackHole 2ch`（Teams Settings → Devices）
2. CLI 输出是否有"上行：用户麦 → BlackHole 2ch"成功提示
3. Web 控制台「DeepSeek 健康」是否绿
4. 在 Web 控制台「上行（中 → 英）」区是否能看到识别原文 + 英文译文流式更新
5. 用 Teams 内置「测试通话」反向播放，应能听到自己的译音

### 我听不到中文译音

检查：

1. 系统默认输出是否仍是你的耳机（菜单栏 → 音频图标）
2. **不**应是 `Teams 同传聚合`（那只是给 Teams 用的，不是默认输出）
3. Web 控制台「下行（英 → 中）」区是否有英文识别 + 中文译文流式更新
4. 调高耳机音量

### Web 控制台打不开

```bash
tvi status     # 检查会话是否运行
lsof -i :8765  # 检查端口是否被占用
```

### Whisper.cpp 子进程频繁崩溃

通常是内存压力。在 `~/.config/teams-voice-interpreter/config.toml` 中：

```toml
[models]
whisper = "tiny"      # 从 small-q5_0 降到 tiny（约 200 MB RAM）
```

注意 tiny 普通话识别质量明显下降。

### Edge-TTS 持续 401 / 403

社区接口被微软封禁的风险。临时解决：

- 等几小时再试（通常社区会快速更新 token）
- 升级 `edge-tts` 包（`pip install -U edge-tts`）
- v1.1+ 切到 Coqui XTTS-v2 本地降级

### DeepSeek API 配额耗尽

```bash
# 在 platform.deepseek.com 充值
# 或使用新的 API Key
export DEEPSEEK_API_KEY=sk-yyyyy
tvi start
```

### 崩溃报告位置

```bash
ls -lt ~/.cache/teams-voice-interpreter/crash-*.log | head -5
```

报告**不**包含原始音频 / 对话文本 / API Key（FR-029）。可附在 GitHub issue 中提交。

---

## 8. 性能预期（来自 SC-001..013 + 宪章 IV）

| 指标 | 预期 |
|------|------|
| 首段译音延迟 | 正式预算中位 ≤ 800 ms / p95 ≤ 1.5 s；800–1200 ms 仅为风险观测区间 |
| 整段端到端 | 中位 ≤ 2.5 s |
| 24h 内存增长 | ≤ 5% |
| 稳态 RAM | 正式预算 ≤ 500 MB；1.0–1.5 GB 仅为风险观测区间，详见 plan.md 复杂度追踪 |
| 60 分钟 0 中断 | 是 |
| 月度运行成本 | < ¥10（仅 DeepSeek 翻译） |

---

## 9. 卸载

```bash
# 1) 停止任何活跃会话
tvi stop

# 2) 卸载本系统
pip uninstall teams-voice-interpreter

# 3) 删除配置与缓存
rm -rf ~/.config/teams-voice-interpreter
rm -rf ~/.cache/teams-voice-interpreter

# 4)（可选）卸载 BlackHole 2ch
brew uninstall blackhole-2ch

# 5)（可选）删除「音频 MIDI 设置」中的 Teams 同传聚合设备
# 在「音频 MIDI 设置」中右键 → 删除

# 6) 在 Teams 中把麦克风源切回 MacBook 内置麦克风
```

---

## 10. 进一步阅读

- 完整规约：[spec.md](spec.md)
- 实施计划：[plan.md](plan.md)
- 数据模型：[data-model.md](data-model.md)
- 外部接口契约：[contracts/](contracts/)
- 性能基线：[perf-report.md](perf-report.md)（按阶段 0 / 实现期 benchmark 持续产出）
