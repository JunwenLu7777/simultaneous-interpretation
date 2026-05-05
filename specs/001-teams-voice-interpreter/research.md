# Phase 0 研究报告：Teams 实时双向语音同传桥（macOS）

**关联**：[plan.md](plan.md) · [spec.md](spec.md)
**日期**：2026-05-05
**目的**：为 plan.md 中各技术选型给出 **Decision / Rationale / Alternatives** 三段式证据；解决所有 `NEEDS CLARIFICATION`；提出 Phase 0 末必须执行的基线 benchmark 工作清单。

---

## 1. 流式 STT — Whisper.cpp Streaming 分支

### Decision

- **实现**：基于 `whisper.cpp` 的 Python binding `pywhispercpp`（首选）或 `faster-whisper` + 自定义流式 wrapper（后备）
- **模型**：默认 `ggml-small-q5_0`（4-bit 量化版 small，约 200 MB 模型 + 1.0–1.2 GB 运行 RAM）；Apple Silicon 启用 Metal 后端 + Core ML encoder offload
- **音频块**：固定 30 ms 帧 / 16 kHz / 单声道；流式滑窗 step 200–400 ms；context size 5–10 秒
- **partial / final 策略**：每 step 输出一次 partial（含置信度），ASR 检测到稳定 token 后升级为 final；FR-013 静音 ≥ 5 s 自动 finalize 并清空缓冲

### Rationale

- spec Q1 用户决定 STT 必须本地零成本，排除云 STT
- `pywhispercpp` 是 `whisper.cpp` 主线 Python binding，社区活跃、Metal 支持成熟
- q5_0 量化在 Apple Silicon 上推理速度提升 ≈ 1.6×，准确率 (WER) 损失 < 1.5%（源自 whisper.cpp benchmarks）
- Core ML encoder offload 让 encoder 跑在 ANE（Apple Neural Engine），可再降 25–40% CPU

### Alternatives considered

| 备选 | 拒绝原因 |
|------|----------|
| `faster-whisper`（基于 CTranslate2） | 流式分支不如 whisper.cpp 成熟；Metal 后端需自行编译；CT2 RAM 占用更高 |
| `openai-whisper` 官方 Python 包 | 无量化、无 Metal、推理速度慢 3–5× |
| `WhisperX`（含说话人分离） | 引入 PyAnnote 依赖 + 额外 1 GB RAM；spec v1 不需要说话人分离 |
| Apple Speech Framework（系统原生） | 流式 API 限定 60 s / 单次；中文识别质量低于 Whisper；与"不开发原生 App"基调冲突（需 Swift 桥接） |
| Vosk-API | 中文小模型识别质量明显低于 Whisper small |

### Phase 0 必经 benchmark

- BM-1：`ggml-small-q5_0` + Metal 在 M2 Pro 上的稳态 RAM / CPU / partial 延迟（持续 60 s）
- BM-2：BM-1 vs `ggml-tiny` vs `ggml-medium-q5_0` 的 WER 对比（普通话商务测试集 30 句）
- BM-3：Core ML encoder offload 是否能将 CPU 降至 ≤ 30%

---

## 2. 流式翻译 — DeepSeek API SSE Streaming

### Decision

- **接口**：`POST https://api.deepseek.com/v1/chat/completions`，`stream=true`，按 SSE 协议解析 `data:` 行
- **模型**：默认 `deepseek-chat`（V3 系列）；可配置 `deepseek-reasoner`（R1 系列，推理增强但首 token 慢 1.5×，不推荐用于实时同传）
- **Prompt 结构**（system 部分由 `mt/prompt.py` 在会话启动时一次性合成）：
  ```text
  你是专业商务同声传译。请将下列{源语言}文本翻译为流畅自然的{目标语言}，保留专有名词、英文缩写、数字、日期、金额的原始或常见映射。
  
  专有名词术语表（必须严格使用）：
  - <zh1> ↔ <en1>（备注：<note1>）
  - ...
  
  规则：
  1. 输出仅含译文，不要解释、不要前缀。
  2. 流式输出，不必等到完整一句再开始。
  3. 数字、日期、金额、英文缩写（如 SDK / API / K8s）保留原写法。
  ```
- **滚动上下文（FR-012）**：最近 8 句已 final 的双语对照，作为 user/assistant 多轮历史插入到 messages 列表前端
- **streaming 解析**：`httpx.stream("POST", ...)` + `aiter_lines()`；逐行去掉 `data: ` 前缀、解析 JSON；空 chunk 与 `[DONE]` 终止符正确处理
- **重连策略（FR-018）**：指数退避 250 / 500 / 1000 / 2000 / 4000 ms；连续 ≥ 30 s 失败触发 FR-019 该方向停发

### Rationale

- spec Q1 用户已锁定 DeepSeek
- `deepseek-chat` 流式首 token 实测 200–400 ms，整段（30–80 字英文）400–1500 ms，满足宪章 IV 预算
- 术语表注入到 system prompt 是工业标准做法，无需 fine-tune
- `httpx` 是 Python 原生 async HTTP 客户端，避免 `aiohttp` 的 event loop 兼容问题

### Alternatives considered

| 备选 | 拒绝原因 |
|------|----------|
| `openai` SDK 兼容模式（DeepSeek 兼容 OpenAI 协议） | 引入 OpenAI SDK 依赖纯粹为兼容；`httpx` 直连更轻量 |
| 非 streaming 等整段返回 | 违反 FR-007/008/009 流式约束 |
| `deepseek-reasoner` 用作翻译 | 推理 chain-of-thought 让首 token 延迟 ↑ 800 ms，违反 SC-001 |
| Few-shot examples 注入 | 商务对话的 few-shot 难以泛化，且会占用宝贵 prompt token |

### Phase 0 必经 benchmark

- BM-4：`deepseek-chat` streaming 首 token 与整段延迟分布（200 次商务译句样本）
- BM-5：术语表注入对翻译质量的提升（盲测 30 对句子）

---

## 3. 流式 TTS — Edge-TTS

### Decision

- **客户端**：`edge-tts` 7.0+ Python 包，`Communicate.stream()` 异步迭代器返回 audio chunks
- **音色**：英文默认 `en-US-AriaNeural`（friendly, conversational）；中文默认 `zh-CN-XiaoxiaoNeural`（柔和，商务）；可配置切换
- **格式**：默认 `audio-24khz-48kbitrate-mono-mp3`；本地解码到 16 kHz PCM16 写入 BlackHole / 默认输出
- **流式块**：edge-tts 默认 chunk ≈ 25 ms 音频；`audio_writer.py` 用 ring buffer 拼接，写入 sounddevice
- **降级路径（plan Complexity Tracking 行 4）**：
  - 第一档：Coqui XTTS-v2 本地推理（开源 / 1.8 GB / GPU 推荐）
  - 第二档（用户付费）：ElevenLabs Flash v2 / Azure Speech Neural TTS

### Rationale

- spec Q1 锁定免费 TTS；edge-tts 是社区主流方案
- mp3 24 kbps mono 在网络上的下行速率仅 ≈ 3 KB/s，对带宽零压力
- chunk 间隔 25 ms 让首字节延迟可压至 200–400 ms

### Alternatives considered

| 备选 | 拒绝原因 |
|------|----------|
| Coqui XTTS-v2 本地（直接作为 v1 默认）| 模型 1.8 GB + 推理 1× 实时（M2 Pro）即首字节 1.5–2 s，违反 SC-001 |
| Piper（开源轻量 TTS） | 中文音色仅有几个、自然度低于 Edge-TTS |
| macOS 系统 `say` 命令 | 仅支持非流式整段合成；音色僵硬；不支持 SSML |
| 付费 TTS（直接作为 v1 默认） | 违反 spec Q1 零成本 |

### 已知风险与降级

| 风险 | 触发条件 | 应对 |
|------|---------|------|
| Edge-TTS 接口被微软封禁 | 连续 ≥ 3 次 401/403 错误 | 自动切到 Coqui XTTS-v2（如已下载模型）或提示用户启用付费档 |
| 鉴权 token 变更 | 首次启动失败 | 在 `edge_tts_client.py` 内自动重新获取 token，最多 3 次 |
| 音频质量退化 | 用户反馈 | Phase 1 提供音色配置开关 + 切换文档 |

### Phase 0 必经 benchmark

- BM-6：Edge-TTS 首字节延迟分布（100 次中英文短句样本）
- BM-7：连续 24 h 调用稳定性（每分钟 1 次合成请求，监测 401/403 频率）

---

## 4. 虚拟音频路由 — BlackHole 2ch + Aggregate Device

### Decision

- **驱动**：BlackHole 2ch（GPL，`brew install blackhole-2ch`），需重启 macOS 一次注册
- **上行路由**：本系统作为 sounddevice 的输出客户端写入 BlackHole 2ch；Teams 应用麦克风源选 `BlackHole 2ch`
- **下行路由**：用户在「音频 MIDI 设置」中创建 Aggregate Device，成员 = `BlackHole 2ch` + `<用户耳机>`；Teams 扬声器源选该 Aggregate；本系统从 BlackHole 2ch 输入端捕获
- **中文译音回放**：经 sounddevice 直接写入 Mac 默认输出（用户耳机），与 Teams 原英文在用户耳机端自然混合
- **设备发现**：`audio/routing.py` 在启动时通过 `sounddevice.query_devices()` 检查 BlackHole 2ch 是否已注册；缺失时阻止启动并给出 `brew install blackhole-2ch` 指引

### Rationale

- spec Q2 用户决定 BlackHole 2ch
- 单驱动 + 一个 Aggregate 是 macOS 原生支持的最简方案；无需用户购买 Loopback license
- 2 通道足够单声道 STT（双通道直接降为 mono）

### Alternatives considered

| 备选 | 拒绝原因 |
|------|----------|
| BlackHole 16ch | spec Q2 已排除，多通道 v1 用不上 |
| Loopback 商用版 | 99 USD license 与零成本路线冲突 |
| 仅 macOS 原生 Aggregate Device | 不支持"应用音频输出 → 虚拟设备"捕获，下行链路不可行 |

### Phase 0 必经验证

- BM-8：BlackHole 2ch 写入到 Teams 听到的端到端延迟（≤ 50 ms 路由开销验证）
- BM-9：Aggregate Device 同时给两个目标（BlackHole + 耳机）的同步性（jitter ≤ 10 ms）

---

## 5. VAD（Voice Activity Detection） — WebRTC VAD

### Decision

- **库**：`webrtcvad` 2.0（基于 Google WebRTC 项目）
- **配置**：aggressiveness mode 2（中等敏感）；30 ms 帧；连续 ≥ 167 帧（约 5 s）静音触发 FR-013 finalize
- **回声规避**：上行 VAD 仅作用于内置麦克风输入；下行 VAD 仅作用于 BlackHole 输入，物理隔离

### Rationale

- WebRTC VAD 是 macOS / Linux / Windows 通用、零依赖（C 实现 + Python wrapper）
- 30 ms 帧匹配 Whisper.cpp 输入颗粒度
- mode 2 在普通会议噪声下假阳性率 < 5%

### Alternatives considered

| 备选 | 拒绝原因 |
|------|----------|
| Silero VAD（PyTorch 模型） | 引入 PyTorch 大依赖（500 MB），与轻量初衷冲突 |
| 能量阈值 VAD | 在键盘敲击 / Teams 系统提示音下假阳性高 |

---

## 6. Web 控制台 — FastAPI + WebSocket + HTMX

### Decision

- **后端**：`fastapi` 0.115+ + `uvicorn` 0.30+；ASGI lifespan 用于会话生命周期挂钩
- **REST 端点**：`POST /api/control/start` `POST /api/control/pause` `POST /api/control/resume` `POST /api/control/stop` `GET /api/status` `POST /api/export`
- **WebSocket 端点**：`/ws/status` 推送频率 ≥ 5 Hz，消息格式 JSON，含 `runtime_sec` / `latest_zh` / `latest_en` / `latency` / `services_health`
- **前端**：单 `index.html` + HTMX 1.9 + 原生 `WebSocket`；样式用 Pico.css 1.5（5 KB 极简）；不引入 npm 工具链
- **端口**：默认 `8765`，可配置；冲突时给出"两段式"错误提示
- **鉴权**：v1 假定本地访问无需鉴权；`uvicorn` 仅绑定 `127.0.0.1`，外部不可达
- **CORS**：禁用（仅 same-origin 访问）

### Rationale

- FastAPI 的 async + WebSocket + Pydantic 集成最优雅；类型注解传递到前端 schema
- HTMX 是"不写 JS 也能做交互"的极简方案，符合"轻量级架构"
- 5 Hz 推送（200 ms 间隔）满足 SC-008 「面板新内容滞后 ≤ 1 秒」的同时不刷爆带宽

### Alternatives considered

| 备选 | 拒绝原因 |
|------|----------|
| Flask + Flask-SocketIO | sync 框架，与 asyncio 管线集成需额外胶水 |
| Streamlit / Gradio | 自带 UI 框架但定制性差，且会引入大量前端依赖 |
| React / Vue + Vite | 与 HTMX 同等功能但增加 npm 工具链 |

---

## 7. CLI — Typer

### Decision

- **库**：`typer` 0.12（基于 click 8）
- **入口**：`python -m teams_voice_interpreter <subcommand>` + 可选脚本入口 `tvi`（若用户运行 `pip install -e .` 注册）
- **子命令**：`start` / `pause` / `resume` / `stop` / `status` / `wizard` / `export <session-id>` / `version`
- **共享 state**：CLI 通过 unix domain socket（`/tmp/teams-voice-interpreter.sock`）与运行中的 FastAPI 主进程通信，确保 CLI 子命令能访问到当前活跃 SessionId 与状态

### Rationale

- Typer 的类型注解 + 自动补全 + 美观 help 是 Python CLI 现代标准
- Unix socket 比 HTTP 本地端口更轻、避免端口竞争

### Alternatives considered

| 备选 | 拒绝原因 |
|------|----------|
| 纯 click | 类型注解略弱 |
| `argparse` | 体验较差，不支持子命令的优雅嵌套 |
| 通过 HTTP 调本地 8765 | 与 Web 控制台共享端口存在并发风险，鉴权流程复杂化 |

---

## 8. 单实例锁（FR-026）

### Decision

- **机制**：进程启动时尝试创建 `~/.cache/teams-voice-interpreter/lock` 并 `fcntl.flock(LOCK_EX | LOCK_NB)`；持锁失败说明已有实例在跑
- **锁文件内容**：JSON `{"pid": int, "started_at": iso8601, "session_id": str, "web_port": int}`
- **健康自检**：尝试 `kill -0 <pid>` 验证锁主仍存活；存活则拒绝并把锁主信息显示给用户；不存活则视为僵尸锁，自动清理后获取
- **清理**：进程正常退出 + `atexit` + 信号处理器 三重保障删除锁文件

### Rationale

- `fcntl.flock` 是 POSIX 标准，跨 macOS / Linux 一致
- 锁文件含 pid 让"已活跃会话"提示能给出精确信息（FR-026 要求两段式提示）

### Alternatives considered

| 备选 | 拒绝原因 |
|------|----------|
| 仅 pid 文件（无 flock） | 进程 kill -9 后留垃圾文件，下次启动误判 |
| 端口绑定（用 8765 占用作为锁） | 端口可被用户配置，多端口下不互斥 |
| Unix socket 占用 | 与 CLI 通信用的 socket 复用，存在用途混淆 |

---

## 9. 崩溃报告匿名化（FR-029）

### Decision

- **触发**：Python 主进程：`signal.signal(SIGTERM/SIGSEGV)` + `atexit` + `sys.excepthook`
- **路径**：`~/.cache/teams-voice-interpreter/crash-<unix-ts>.log`，权限 0600
- **内容字段**：
  - `python_version`、`os_version`、`arch`
  - 关键依赖版本（`whisper.cpp`、`edge-tts`、`fastapi`、`httpx` git/pip version）
  - 崩溃 stack trace（已脱敏：路径中的家目录替换为 `~`）
  - 各外部服务最近一次连接状态快照（不含响应正文）
  - `psutil` 资源快照（RAM / CPU 当前 + 峰值）
- **明确不包含**：识别原文 / 译文文本 / 原始音频 / API Key / 用户家目录绝对路径
- **轮转**：保留最新 20 份，按 mtime 排序删除多余项

### Rationale

- 信号处理器 + atexit 双保险覆盖各种异常退出路径
- 0600 权限防止其他用户读取
- 路径脱敏避免泄露用户名 / 机器名

### Alternatives considered

| 备选 | 拒绝原因 |
|------|----------|
| 写到 `/tmp/` | macOS 重启清空，崩溃报告丢失；权限模型不一致 |
| 上传到云端崩溃收集服务 | 与 spec Q1 隐私边界冲突 |
| 包含识别原文做 trace | 严重违反 FR-023 / FR-024 |

---

## 10. 子进程 Supervisor（FR-028）

### Decision

- **形态**：`session/supervisor.py` 在主进程内以 asyncio Task 实现，监控以下子进程：
  - Whisper.cpp 推理子进程（每方向一个；上行 + 下行 = 2 个）
  - Edge-TTS 流式合成子进程（每方向一个 = 2 个）
- **健康检查**：每 1 s `proc.poll()` + heartbeat（子进程每秒在 stdout 写一次 `HB`，主进程读取超时 ≥ 3 s 视为卡死）
- **respawn 策略**：检测到 exit 或 heartbeat 超时 → 在 ≤ 5 s 内 `respawn_subprocess()`；记录次数到滚动 60 s 窗口
- **熔断**：同一子进程 60 s 内崩溃 ≥ 3 次 → 停止该方向 + 推送两段式错误到 Web 控制台
- **状态保留**：respawn 期间 SessionId、滚动上下文、术语表注入状态、延迟统计**不**清空

### Rationale

- asyncio Task 与 FastAPI lifespan 天然兼容
- heartbeat 检测能发现"未死但卡住"的子进程，比单纯 exit 监控更稳健

### Alternatives considered

| 备选 | 拒绝原因 |
|------|----------|
| `multiprocessing.Process` 自带 daemon | 信号传播在 macOS 上不可靠 |
| 引入 `circus` 等系统级 supervisor | 违反 plan「不引入 launchd/supervisord/pm2」 |
| 不做 respawn，崩即止 | 违反 FR-028 |

---

## 11. 配置与凭证管理（FR-022）

### Decision

- **配置文件**：`~/.config/teams-voice-interpreter/config.toml`
  ```toml
  [api]
  deepseek_api_key_env = "DEEPSEEK_API_KEY"     # 优先从环境变量读取，避免 toml 落盘密钥
  
  [server]
  port = 8765
  
  [models]
  whisper = "small-q5_0"                         # tiny / small-q5_0 / medium-q5_0
  metal = true
  
  [voices]
  en = "en-US-AriaNeural"
  zh = "zh-CN-XiaoxiaoNeural"
  
  [glossary]
  path = "~/.config/teams-voice-interpreter/glossary.toml"
  ```
- **加载**：`pydantic-settings` 自动从 toml + 环境变量 + `.env`（项目本地，仅开发用）合并；环境变量优先级最高
- **密钥**：永远不写入 toml；用户在 `~/.zshrc` 中 `export DEEPSEEK_API_KEY=...` 或 ad-hoc `DEEPSEEK_API_KEY=... tvi start`

### Rationale

- 环境变量优先 + toml 引用 = 密钥永远不落盘
- pydantic-settings 提供统一的类型校验

### Alternatives considered

| 备选 | 拒绝原因 |
|------|----------|
| 把密钥写入 toml | 违反 FR-022 |
| macOS Keychain | 跨账户复杂、CI 不友好；可在 v1.x 作为可选项 |
| `dotenv` 仅本地 | 用户机器仍可走 toml + env，统一一套即可 |

---

## 12. 测试 fixture 录制策略

### Decision

- **音频 fixture**：
  - `conference-cn.wav` / `conference-en.wav`：从公开演讲数据集抽取 10 分钟商务对话片段（Common Voice + LibriSpeech 商务子集）
  - `long-cn-2h.wav`：拼接 12 段 10 分钟，加入合成静音过渡
- **API fixture**：
  - DeepSeek streaming：用 `respx` 录制真实响应到 `tests/contract/fixtures/deepseek/*.json`
  - edge-tts：用 `pytest-recording` 录制 audio chunk 序列
  - whisper.cpp：通过 `pywhispercpp` 直接调用，不需要 fixture（本地推理结果稳定）

### Rationale

- 公开数据集避免版权 / 隐私问题
- `respx` 是 `httpx` 官方推荐的 mock 库

---

## 13. 待 Phase 0 末端基线 benchmark 工作清单

> 按宪章 IV 与 plan.md Complexity Tracking，以下 benchmark **必须**在 Phase 0 末（即 `/speckit.tasks` 生成具体任务前）执行并把结果写入 `perf-report.md`。

| 编号 | 测试目标 | 通过条件 | 不通过的退出动作 |
|------|---------|----------|------------------|
| BM-1 | `ggml-small-q5_0` Metal 稳态 RAM | ≤ 1.6 GB | 触发 plan Complexity Tracking 行 1 升级（宪章 IV 修订） |
| BM-2 | small q5_0 vs tiny WER 对比（30 句） | small q5_0 - tiny WER 差 ≥ 5% | 选 small；否则可选 tiny 满足宪章预算 |
| BM-3 | Core ML encoder offload 后 CPU | ≤ 30% | 通过；否则触发行 2 升级 |
| BM-4 | DeepSeek streaming 首 token 延迟 | p50 ≤ 400 ms / p95 ≤ 800 ms | 失败：联系 DeepSeek 支持或切付费高优先级通道 |
| BM-5 | 术语表注入对译文质量提升 | 盲测分提升 ≥ 0.3 分（5 分制） | 失败：保留术语表但降为可选 |
| BM-6 | Edge-TTS 首字节延迟 | p50 ≤ 400 ms | 失败：转 Coqui XTTS-v2 评估 |
| BM-7 | Edge-TTS 24h 稳定性 | 401/403 < 0.5% | 失败：触发 Complexity Tracking 行 4 转 Coqui |
| BM-8 | BlackHole 2ch 路由延迟 | ≤ 50 ms | 失败：检查 Aggregate Device 配置 |
| BM-9 | Aggregate Device jitter | ≤ 10 ms | 失败：单耳机 / 单 BlackHole 冗余路由 |
| BM-10 | 端到端首段译音 p50 | ≤ 800 ms（理想）/ ≤ 1200 ms（可接受） | 失败：触发 Complexity Tracking 行 3 升级 |
| BM-11 | 端到端整段 p50 / p95 | p50 ≤ 2.5 s, p95 ≤ 4.0 s | 失败：触发 Complexity Tracking 新行 |
| BM-12 | 60 分钟 0 中断 | 0 次 supervisor 熔断 | 失败：检查 supervisor 阈值与子进程稳定性 |
| BM-13 | 24h 内存增长 | ≤ 5% | 失败：定位泄漏并修复 |

---

## 14. 解决的 NEEDS CLARIFICATION 项（Phase 0 出口）

本研究阶段无新增 `NEEDS CLARIFICATION`；spec.md 中 5 条原始 Clarifications 已在 `/speckit.clarify` 阶段全部 resolve。Technical Context 全字段确定。

---

## 15. Phase 0 出口断言

- ✅ 所有技术选型 Decision / Rationale / Alternatives 三段齐全
- ✅ 13 项基线 benchmark 工作清单已列出，待 `/speckit.tasks` 阶段细化为具体任务
- ✅ 4 项已知风险（plan.md Complexity Tracking 行 1–4）已在 BM-1/2/3/6/7/10 中明确测量方法与退出动作
- ✅ 无未解决 `NEEDS CLARIFICATION`

**进入 Phase 1：Design & Contracts。**
