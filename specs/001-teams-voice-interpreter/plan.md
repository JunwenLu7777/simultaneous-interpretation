# 实现计划：Teams 实时双向语音同传桥（macOS）

**分支**：`001-teams-voice-interpreter`
**日期**：2026-05-05
**规约**：[spec.md](spec.md)
**输入**：从 `specs/001-teams-voice-interpreter/spec.md` 读取的功能规约（226 行；FR-001..029；SC-001..011；4 个用户故事 + 11 项边界异常；5 条 Clarifications；3 项 v1 锁定决定；3 项 v1 边界声明）

---

## 摘要

本 feature 在 macOS 上交付一套**双向中英实时语音同传桥**，与 Microsoft Teams 桌面端通过**虚拟音频设备 BlackHole 2ch + 系统原生 Aggregate Device** 完成音频路由；以**本地 Whisper.cpp Streaming** 做流式 STT、**DeepSeek API SSE streaming** 做流式翻译、**Edge-TTS** 做流式 TTS，构成"几乎零成本"的服务栈（月度运行成本 < ¥10）。技术形态严格规避原生 macOS App Bundle 与 Teams 插件，采用 **Python 3.11+ 单工程 + 本地 FastAPI 后端 + 单页 HTMX 前端**；用户表面为 CLI 子命令 + `http://localhost:8765` Web 控制台。性能上以"首段译音 ≤ 800 ms 中位、整段端到端 ≤ 2.5 s p50"为目标；本计划在 Complexity Tracking 中显式登记 Whisper.cpp small 与宪章 IV 资源预算的已知冲突，并在 Phase 0 通过基线 benchmark 决定是降档到量化模型还是触发宪章 IV 预算修订流程。

---

## 技术上下文（Technical Context）

**语言 / 版本**：Python 3.11+（与"优先 Python / Go 脚本语言"用户偏好一致；选 Python 而非 Go 的理由：`whisper.cpp` Python binding、`edge-tts`、`fastapi`、`pydantic` 全在 Python 生态成熟，Go 端等价工具链需自行封装）

**主要依赖**：

- **流式 STT**：`pywhispercpp` 或 `whisper-streaming` 包装层（基于 `whisper.cpp`），首选 4-bit 量化模型 `ggml-small-q5_0`；Apple Silicon 启用 Metal 后端 + Core ML encoder offload
- **流式翻译**：`httpx` 0.27+（DeepSeek `/v1/chat/completions` SSE streaming 客户端，`stream=true`）
- **流式 TTS**：`edge-tts` 7.0+（社区维护的 Microsoft Edge 浏览器免费 TTS 客户端）
- **音频 I/O**：`sounddevice` 0.4+（PortAudio 跨平台 CoreAudio 客户端，含设备枚举）+ `numpy` 1.26+（ring buffer / 重采样）
- **VAD**：`webrtcvad` 2.0+（对应 FR-013 的 5 秒静音闭合）
- **Web 后端**：`fastapi` 0.115+ + `uvicorn` 0.30+ + `websockets` 12+（用于状态面板的 ≥ 5 Hz 推送）
- **前端**：单页 `index.html` + HTMX 1.9+ + 原生 `fetch` + 原生 `WebSocket`（不引入 React/Vue 等重型框架）
- **CLI**：`typer` 0.12+（基于 `click`，提供更好的 Python 类型注解集成）
- **配置 / 数据**：Python 3.11 自带 `tomllib` + `pydantic` 2+ + `pydantic-settings` 2+
- **测试**：`pytest` 8+ + `pytest-asyncio` 0.23+ + `pytest-mock` 3+ + `pytest-benchmark` 4+ + `coverage` 7+
- **静态质量**：`ruff` 0.6+（lint + format）+ `mypy` 1.11+ strict + `radon` 6+（圈复杂度）

**存储**（无 RDBMS / 无云持久化）：

- 内存：会话期完整双语对照（FR-024）+ 滚动延迟统计（FR-016）
- `~/.config/teams-voice-interpreter/config.toml`：API 凭证引用、端口、模型档位
- `~/.config/teams-voice-interpreter/glossary.toml`：用户术语表（FR-012 / GlossaryEntry）
- `~/.cache/teams-voice-interpreter/whisper-models/`：Whisper.cpp 量化模型缓存
- `~/.cache/teams-voice-interpreter/crash-<unix-ts>.log`：匿名崩溃报告（FR-029，最多 20 份轮转）
- 用户主动导出：`teams-session-<开始时间戳>.md`（用户在 Web 控制台选择下载位置；FR-027）

**测试**：`pytest` + `pytest-asyncio` 异步管线测试；`pytest-benchmark` perf 回归（宪章 II 强制）；行覆盖 ≥ 80%、外部边界类（STT / MT / TTS / 音频客户端）100% 分支覆盖

**目标平台**：macOS 13 (Ventura) 及以上；Apple Silicon (arm64) 一等公民、Intel x86_64 兼容；先决条件：BlackHole 2ch 已 `brew install` 并完成 Aggregate Device 配置（首次运行向导 FR-006 引导）

**项目类型**：CLI 启停命令 + 同进程内嵌 FastAPI 本地 Web 服务（混合形态）；与 FR-021 / SC-009 约束一致——不产生 `.app` Bundle / Teams 插件 / launchd plist

**性能目标**（来自 spec SC-001..011 + 宪章 IV）：

| 指标 | 预算 | 来源 |
|---|---|---|
| 首段译音延迟 p50 | ≤ 800 ms | SC-001 / 宪章 IV 端到端化 |
| 首段译音延迟 p95 | ≤ 1200 ms | SC-001 |
| 端到端整段延迟 p50 | ≤ 2.5 s | SC-003 / 宪章 IV |
| 端到端整段延迟 p95 | ≤ 4.0 s | SC-003 / 宪章 IV |
| LLM 翻译首 token | ≤ 800 ms | 宪章 IV |
| LLM 翻译整段 | ≤ 1.5 s | 宪章 IV |
| 24h 内存增长 | ≤ 5% | SC-004 / 宪章 IV |
| 稳态 CPU（Apple Silicon） | ≤ 30% | SC-010 / 宪章 IV |
| 稳态 RAM | ≤ 500 MB | SC-010 / 宪章 IV |

**约束**：

- **不得**开发原生 macOS App Bundle（FR-021 / SC-009）
- **不得**开发 Teams 插件 / Office 365 Add-in
- **不得**在源码或仓库中硬编码 API 凭证（FR-022）
- **不得**默认在磁盘 / 远端持久化原始音频或对话文本（FR-023 / FR-024）
- **不得**整段录制后处理（FR-025）
- **不得**引入 launchd / supervisord / pm2 等系统级守护（FR-028）
- 服务栈锁定：DeepSeek（翻译）+ Whisper.cpp small q5_0（STT）+ Edge-TTS（TTS）+ BlackHole 2ch（虚拟音频）
- 单实例单会话（FR-026）；多会话并行延至 v2

**规模 / 范围**：

- 个人单机单会话；多账号 / 多会议并行延至 v2
- 单场会话长度上限 2 小时（spec edge case 已声明 ≥ 2 小时仍能稳定）
- 仅中→英 + 英→中 双向；其他语言对延至 v2
- v1 仅普通商务交流场景；监管严格场景（医疗 / 律师 / 政府 / 金融 / HR）延至 v2（spec Q1 D 决定）

---

## 宪章合规检查（Constitution Check）

> *门禁：必须在 Phase 0 研究开始前通过；Phase 1 设计后再次复检。*

按 `.specify/memory/constitution.md` 四大原则逐项声明：

### I. 代码质量

**OK**。

- 模块边界与单一职责清晰（详见「项目结构」八层模块：`audio` / `stt` / `mt` / `tts` / `session` / `cli` / `web` / `data`）
- 每个模块对外暴露窄接口（不超过 5 个公开符号），均带 Python 类型注解 + 模块顶端 docstring（≤ 一段话）
- `ruff` lint + format + `mypy --strict` + `radon cc -a` 通过 CI 零警告门禁
- 圈复杂度 ≤ 10、单文件 ≤ 300 行由 `radon` 与 `flake8-mccabe` 自动检测；超阈值在本表升级登记
- 无 dead code / 注释代码 / 未使用 import；TODO 必须含 owner + issue 链接（lint 规则强制）
- 所有公共函数 / 对外 CLI 子命令 / Web 端点必须有至少一个测试中的使用示例

### II. 测试纪律（不可妥协）

**OK**。

- TDD 强制：先写失败测试 → 用户/评审确认契合需求 → 再实现 → 再重构。
- 计划测试结构：

| 层 | 路径 | 覆盖目标 | 关键样本 |
|---|---|---|---|
| 单元 | `tests/unit/<module>/` | 行覆盖 ≥ 80% | 每个数据模型、VAD 阈值、重连退避曲线、术语表 prompt 拼装、单实例锁、SessionId 生成 |
| 集成 | `tests/integration/` | 端到端用户故事 | `test_uplink_pipeline`（US1）、`test_downlink_pipeline`（US2）、`test_status_panel`（US3）、`test_supervisor`（US4 + FR-028）、`test_export`（FR-027） |
| 契约 | `tests/contract/` | 第三方边界 100% 分支 | DeepSeek streaming、edge-tts、whisper.cpp Python binding（含网络瞬断、配额耗尽、模型加载失败、partial token 顺序错乱等分支） |
| Perf | `tests/perf/` | 宪章 IV 全部预算 | first-segment latency、end-to-end latency、long-session-stability、memory-leak、cpu-usage |

- 第三方边界（DeepSeek / Whisper.cpp / edge-tts / sounddevice CoreAudio）必须有契约测试 + 录制 fixture 集成测试；fixture 路径 `tests/<contract|integration>/fixtures/`
- CI 强制运行 perf 回归测试（`pytest tests/perf/ --benchmark-fail-on-regression`）；任一预算违例自动阻断合并
- skip / xfail 必须关联 GitHub issue；无 issue 的 skip 视为缺陷

### III. UX 一致性

**OK**。

- 共享术语表：`src/teams_voice_interpreter/glossary/strings.py` + `src/.../glossary/i18n/zh-CN.toml`；CLI / Web Toast / 日志 / 错误响应统一引用同一份键
- 错误两段式（"发生了什么 + 下一步如何做"）由 `errors.UserFacingError` 基类强制；`ruff` 自定义规则禁止任何裸 `raise Exception`
- 1 秒内反馈：FastAPI WebSocket + 前端事件循环；首字节响应实测 ≤ 200 ms（SC-008 子集）
- 状态可观测：Web 控制台同步显示 FR-016 全部要素（运行时长、双向最近识别 / 译文、首段 / 端到端延迟 p50/p95、三服务连接健康状态）；前端 WebSocket 推送 ≥ 5 Hz
- 设备 / 服务切换不丢上下文：`SessionStore` 内存检查点；切换事件触发 `async migrate_session()`，在 ≤ 5 秒（FR-018）内恢复
- 默认安全可逆：CLI `start` 幂等（已活跃则按 FR-026 拒绝）；Web 控制台「停止」前显式弹确认；FR-027 导出操作不中断会话

### IV. 性能要求（不可妥协）

**PARTIAL → 见下方 Complexity Tracking 行 1 / 2 / 3 / 4**。

- **首段译音 ≤ 800 ms（p50）**：宪章预算与 SC-001 一致；**实测期望 800–1200 ms**（已知风险），见 Complexity Tracking 行 3
- 端到端 p50 ≤ 2.5 s / p95 ≤ 4.0 s：可达成
- LLM 首 token ≤ 800 ms：DeepSeek streaming 实测中位 200–400 ms，可达成
- LLM 整段 ≤ 1.5 s：可达成
- 24h 内存增长 ≤ 5%：可达成（Whisper.cpp 子进程隔离 + supervisor respawn 限制累积泄漏）
- **稳态 CPU ≤ 30%**：Whisper.cpp small 单核 25–40%，**临近违例**，见 Complexity Tracking 行 2
- **稳态 RAM ≤ 500 MB**：Whisper.cpp small 占 1.0–1.5 GB，**严重超限**，见 Complexity Tracking 行 1

**基准测试位置**：`tests/perf/`

**基准工作负载**：

- `tests/perf/fixtures/conference-cn.wav`（10 分钟标准商务普通话录音，含数字 / 专有名词 / 自然停顿）
- `tests/perf/fixtures/conference-en.wav`（同等长度英文录音）
- 双向并行场景：同时播放上述两条音频模拟真实会议
- 长会话场景：`tests/perf/fixtures/long-cn-2h.wav`（2 小时拼接录音，模拟长会议）

**基线结果**：`specs/001-teams-voice-interpreter/perf-report.md`（Phase 0 末由 benchmark 任务产出）

---

## 项目结构（Project Structure）

### 文档（本 feature）

```text
specs/001-teams-voice-interpreter/
├── spec.md              # 已完成（226 行）
├── plan.md              # 本文件（/speckit.plan 输出）
├── research.md          # Phase 0 输出（/speckit.plan 产出）
├── data-model.md        # Phase 1 输出（/speckit.plan 产出）
├── quickstart.md        # Phase 1 输出（/speckit.plan 产出）
├── perf-report.md       # Phase 0 末由 benchmark 任务产出（基线结果）
├── contracts/           # Phase 1 输出（外部接口契约）
│   ├── deepseek-translate.md
│   ├── edge-tts.md
│   ├── whisper-cpp.md
│   ├── blackhole-coreaudio.md
│   └── web-control-api.md
├── checklists/
│   └── requirements.md  # 已完成（20/20 通过）
└── tasks.md             # /speckit.tasks 输出，本命令不创建
```

### 源代码（仓库根）

```text
pyproject.toml                            # PEP 621 项目配置 + ruff/mypy/pytest/coverage 配置
src/teams_voice_interpreter/
├── __init__.py
├── __main__.py                          # python -m teams_voice_interpreter 入口
├── audio/                               # macOS CoreAudio 客户端
│   ├── capture.py                       # 麦克风 / BlackHole 输入捕获
│   ├── playback.py                      # 默认输出 / 虚拟设备写入
│   └── routing.py                       # 设备发现 + Aggregate Device 检测
├── stt/                                 # 流式 Whisper.cpp
│   ├── whisper_streaming.py             # 流式 wrapper
│   ├── vad.py                           # WebRTC VAD（FR-013）
│   └── client.py                        # 子进程管理（接受 supervisor 监控）
├── mt/                                  # 流式翻译（DeepSeek）
│   ├── deepseek_client.py               # SSE streaming
│   ├── prompt.py                        # system prompt + GlossaryEntry 注入
│   └── context_window.py                # FR-012 滚动 8 句上下文
├── tts/                                 # 流式 TTS（Edge-TTS）
│   ├── edge_tts_client.py
│   └── audio_writer.py                  # 流式块写入虚拟麦克风 / 默认扬声器
├── session/                             # 会话生命周期 + supervisor
│   ├── manager.py                       # SessionId、单实例锁（FR-026）
│   ├── supervisor.py                    # 子进程 respawn + 60s/3 次熔断（FR-028）
│   ├── crash_reporter.py                # 匿名崩溃报告（FR-029）
│   └── exporter.py                      # Markdown 导出（FR-027）
├── cli/                                 # Typer CLI
│   ├── app.py                           # start / pause / resume / stop / status
│   └── wizard.py                        # 首次运行向导（FR-006）
├── web/                                 # FastAPI 后端 + 单页前端
│   ├── server.py                        # ASGI app + lifespan
│   ├── routes/
│   │   ├── control.py                   # 启停 REST
│   │   ├── status.py                    # WebSocket 推送（≥ 5 Hz）
│   │   └── export.py                    # 导出 Markdown
│   └── static/
│       ├── index.html                   # 单页 HTMX
│       ├── app.js                       # 原生 fetch + WebSocket
│       └── style.css
├── data/                                # 数据模型（Pydantic 2）
│   ├── session.py                       # Session
│   ├── transcript.py                    # TranscriptSegment / TranslationSegment
│   ├── audio_segment.py                 # SynthesizedAudioSegment
│   ├── latency.py                       # LatencySnapshot
│   ├── credential.py                    # ServiceCredential
│   └── glossary.py                      # GlossaryEntry
├── glossary/                            # UX 一致性术语表（宪章 III）
│   ├── strings.py                       # 共享字符串键
│   └── i18n/
│       └── zh-CN.toml                   # 用户可见文案
├── errors.py                            # UserFacingError 基类（两段式强制）
├── config.py                            # pydantic-settings + ~/.config 加载
└── perf.py                              # 延迟测量工具

tests/
├── unit/                                # 单元（≥ 80% 行覆盖）
│   ├── audio/
│   ├── stt/
│   ├── mt/
│   ├── tts/
│   ├── session/
│   ├── cli/
│   └── web/
├── integration/                         # 端到端用户故事
│   ├── test_uplink_pipeline.py          # US1
│   ├── test_downlink_pipeline.py        # US2
│   ├── test_status_panel.py             # US3
│   ├── test_supervisor.py               # US4 + FR-028
│   ├── test_export.py                   # FR-027
│   └── fixtures/                        # 录制的音频 / API 响应
├── contract/                            # 第三方边界 100% 分支覆盖
│   ├── test_deepseek_streaming.py
│   ├── test_edge_tts.py
│   └── test_whisper_cpp.py
└── perf/                                # 宪章 IV 全部预算
    ├── test_first_segment_latency.py
    ├── test_end_to_end_latency.py
    ├── test_long_session_stability.py
    ├── test_memory_leak.py
    ├── test_cpu_usage.py
    └── fixtures/
        ├── conference-cn.wav
        ├── conference-en.wav
        └── long-cn-2h.wav

scripts/
└── install-blackhole.sh                  # 首次安装辅助（包装 brew install）

README.md                                  # 含 SC-011 监管严格场景免责声明
```

**结构决定**：单工程（Option 1）；CLI 与 Web 共享 `session/` 核心；前端是 `src/teams_voice_interpreter/web/static/` 下的纯静态资源（HTMX + 原生 fetch + WebSocket，无 npm 工具链），与「轻量级」初衷一致。

---

## Complexity Tracking

> 本表登记所有违反或临近违反宪章四大原则的取舍，并附理由与退出计划。
> 任一行从「风险 / 待验证」升级为「实测命中」时，必须发起宪章 PR（按宪章治理流程升 MAJOR / MINOR）或在本表把该行从风险升级为已批准的例外。

| 违例 | 为何必要 | 拒绝的更简单方案的原因 | 状态 |
|------|---------|-----------------------|------|
| **行 1**：Whisper.cpp small 持续 RAM ≈ 1.0–1.5 GB（违反宪章 IV「稳态 RAM ≤ 500 MB」）| spec Q1 用户决定使用本地免费 STT。Whisper tiny（≤ 75 MB 模型 / ~ 200 MB 运行 RAM）准确率明显下降，普通话识别错误率高 ≈ 15%，会损害 SC-005「翻译可懂度 ≥ 4/5」。Phase 0 将基线 small 与 tiny 双语方案；若 small q5_0 量化版无法降到预算，**触发**宪章 IV 修订把 RAM 预算放宽到 ≤ 1.6 GB（Whisper small 的合理上限）。 | tiny 准确率不达标；medium / large-v3 RAM 直接 3–6 GB，更不可行；切换到云 STT 违反 spec Q1 用户决定。 | 风险，待 Phase 0 基线 |
| **行 2**：Whisper.cpp small 单核 CPU 25–40%（临近违反宪章 IV「稳态 CPU ≤ 30%」）| 同上 spec Q1 决定。Phase 0 将基线 q5_0 / q4_0 量化 + Apple Silicon Metal 后端 + Core ML encoder offload；预期降至 ≤ 25%。若量化方案不达标且模型档位不能再降，**触发**宪章 IV 修订把 CPU 预算放宽到 ≤ 40%。 | 不启用 Metal 后端 CPU 占用更高；切到云 STT 违反 spec Q1。 | 风险，待 Phase 0 基线 |
| **行 3**：首段译音延迟期望 p50 800–1200 ms（违反 SC-001「中位 ≤ 800 ms」）| spec Q1 本地 Whisper.cpp 流式 partial 输出 hop 长度物理下限约 600 ms，无法压到云 STT 的 200 ms 级别。Phase 0 实测后若仍不达，**触发**：(a) 调整 SC-001 到 p50 ≤ 1000 ms（仍优于行业大多数同传产品）；或 (b) 引入"云 STT 可选 fallback"作为高级用户付费切换。 | 切到云 STT 违反 spec Q1 零成本；用 small 以下模型准确率不达标；硬压 800 ms 会牺牲 partial 稳定性导致幻听激增。 | 风险，待 Phase 0 基线 |
| **行 4**：依赖非官方 Edge-TTS 接口（与「成熟云服务」原则有距离）| spec Q1 用户决定使用免费 TTS。Phase 0 将设计 Coqui XTTS-v2 本地降级路径 + 用户付费切 ElevenLabs / Azure 通道作为备选；首版仍以 Edge-TTS 为默认。 | 切到付费 TTS 违反 spec Q1 零成本；本地 Coqui 模型 1.8 GB + 推理慢，第一档不优先。 | 风险，已有 Phase 0 退出计划 |

**实施前必经门禁**：

1. Phase 0 末尾产出 `perf-report.md`，对行 1 / 2 / 3 给出实测数据
2. 若实测命中违例，PR 描述中必须显式声明"宪章修订流程触发"或"在 plan.md 新增已批准例外"
3. 行 4 的 Edge-TTS 接口可用性在每周 CI 中通过一次"金丝雀"调用验证，连续失败 ≥ 3 次自动开 issue

---

## 阶段产出索引

| 阶段 | 产出 | 状态 |
|---|---|---|
| Phase 0 — Outline & Research | `research.md` | 待 `/speckit.plan` 产出 |
| Phase 0 末 | `perf-report.md` 基线 | 待 benchmark 任务执行后产出 |
| Phase 1 — Design & Contracts | `data-model.md` | 待 `/speckit.plan` 产出 |
| Phase 1 — Design & Contracts | `contracts/*.md` | 待 `/speckit.plan` 产出 |
| Phase 1 — Design & Contracts | `quickstart.md` | 待 `/speckit.plan` 产出 |
| Phase 1 末 | Agent 上下文文件更新 | 待 `update-agent-context.sh` 执行 |
| Phase 2 — Tasks | `tasks.md` | 由 `/speckit.tasks` 产出，**本命令不产出** |
