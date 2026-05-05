---
description: "Teams 实时双向语音同传桥（macOS）任务清单"
---

# 任务清单：Teams 实时双向语音同传桥（macOS）

**输入**：`/specs/001-teams-voice-interpreter/` 下设计文档（spec.md / plan.md / research.md / data-model.md / contracts/ / quickstart.md）
**前置条件**：plan.md（必需） + spec.md（必需，含 4 个用户故事）+ research.md / data-model.md / contracts/

**测试**：依据宪章原则 II（**测试纪律 — 不可妥协**），测试任务**必须**先于实现任务（TDD），且每个用户故事都必须列出其单元 / 集成 / 契约测试。

**性能**：依据宪章原则 IV（**性能要求 — 不可妥协**），凡涉及音频采集 / ASR / MT / TTS 路径的任务，**必须**包含至少一个 perf benchmark 任务，并把基线结果写入 `specs/001-teams-voice-interpreter/perf-report.md`。

**UX 一致性**：依据宪章原则 III，凡涉及用户可见字符串、错误、状态面板的任务，**必须**通过共享术语表 + 错误两段式合规校验。

**组织**：任务按用户故事分组，确保每个故事可独立实现与独立测试。

## 任务格式：`[ID] [P?] [Story] 描述（含文件路径）`

- **[P]**：可并行（不同文件、无未完成依赖）
- **[Story]**：所属用户故事（US1 / US2 / US3 / US4）；准备 / 基础 / 收尾阶段无标签

## 路径约定

- 单工程：`src/teams_voice_interpreter/` + `tests/` 在仓库根（per plan.md）

---

## 阶段 1：准备（共享基础设施）

**目的**：项目初始化、目录骨架、静态质量工具链。

- [ ] T001 创建仓库根目录骨架：`src/teams_voice_interpreter/{audio,stt,mt,tts,session,cli,web/{routes,static},data,glossary/i18n}/`、`tests/{unit,integration,contract,perf/fixtures}/`、`scripts/`（按 plan.md「项目结构」)
- [ ] T002 在仓库根新建 `pyproject.toml`，按 PEP 621 声明 Python 3.11+ + 全部 plan.md 「主要依赖」：`pywhispercpp`、`httpx`、`edge-tts`、`sounddevice`、`numpy`、`webrtcvad`、`fastapi`、`uvicorn[standard]`、`websockets`、`typer`、`pydantic` 2、`pydantic-settings`、`pytest` 8、`pytest-asyncio`、`pytest-mock`、`pytest-benchmark`、`coverage` 7、`ruff` 0.6、`mypy` 1.11、`radon` 6、`respx`
- [ ] T003 [P] 在 `pyproject.toml` 中加入 `[tool.ruff]` 配置（lint + format，启用 E/F/I/B/UP/C90 + 自定义规则禁裸 `raise Exception` + max-complexity 10）
- [ ] T004 [P] 在 `pyproject.toml` 中加入 `[tool.mypy]` 配置（strict、disallow_untyped_defs、warn_return_any、no_implicit_optional）
- [ ] T005 [P] 在 `pyproject.toml` 中加入 `[tool.pytest.ini_options]` + `[tool.coverage.run]` + `[tool.coverage.report]` 配置（`fail_under = 80`、`branch = true`、外部边界类强制 100% 分支）
- [ ] T006 [P] 在仓库根创建 `.gitignore`，至少覆盖：`.venv/`、`__pycache__/`、`*.pyc`、`.pytest_cache/`、`.coverage*`、`.mypy_cache/`、`.ruff_cache/`、`.omc/state/`、`.omc/sessions/`、`*.egg-info/`、`dist/`、`.env`、`~/.cache/teams-voice-interpreter/whisper-models/`（仓库内同名忽略）
- [ ] T007 [P] 在仓库根创建 `README.md` 骨架：项目简介、`quickstart.md` 链接、SC-011 监管严格场景免责声明完整文本占位段
- [ ] T008 [P] 在仓库根创建 `.pre-commit-config.yaml`，启用 `ruff check`、`ruff format --check`、`mypy --strict`、`radon cc -a -nb`
- [ ] T009 在仓库根创建 `Makefile`（`make lint` / `make typecheck` / `make test` / `make benchmark` / `make coverage`）以统一开发命令

**检查点**：`make lint && make typecheck && make test` 全部 0 错误（暂无生产代码、仅空目录 + 占位 `__init__.py`）。

---

## 阶段 2：基础（阻塞前置 — 全部用户故事的共同基础）

**目的**：核心数据模型、共享基础设施、错误规范、UX 术语表、单实例锁、设备发现 — 任一用户故事都依赖这些。

**⚠️ 关键**：本阶段未完成前，任一用户故事任务均**不得**开始。

- [ ] T010 [P] 创建 `tests/unit/errors/test_user_facing_error.py`：先写失败测试，覆盖 `UserFacingError` 两段式字段、异常子类、禁止裸错误透传
- [ ] T011 [P] 创建 `tests/unit/glossary/test_strings.py`：先写失败测试，覆盖共享字符串键完整性、`zh-CN.toml` 每个用户可见错误均含「发生了什么 + 下一步如何做」
- [ ] T012 [P] 创建 `tests/unit/config/test_settings.py`：先写失败测试，覆盖环境变量 / `~/.config/teams-voice-interpreter/config.toml` / `.env` 优先级、DeepSeek key 缺失提示
- [ ] T013 [P] 创建 `tests/unit/data/test_session_state.py`：先写失败测试，覆盖 `Session` 状态机所有合法 / 非法转换
- [ ] T014 [P] 创建 `tests/unit/data/test_models.py`：先写失败测试，覆盖 `TranscriptSegment` / `TranslationSegment` / `AudioStream` / `LatencySnapshot` / `ServiceCredential` / `GlossaryEntry` / `CrashReport` 字段校验、边界值、序列化往返
- [ ] T015 [P] 创建 `tests/unit/session/test_instance_lock.py`：先写失败测试，覆盖正常获取 / 已被持有 / 僵尸锁清理 / 多次释放幂等（FR-026）
- [ ] T016 [P] 创建 `tests/unit/audio/test_routing.py`：先写失败测试，mock `sounddevice.query_devices()` 返回预设设备列表，覆盖 BlackHole 缺失 / Aggregate 缺失 / 正常路径
- [ ] T017 [P] 创建 `tests/unit/perf/test_stopwatch_latency_recorder.py`：先写失败测试，覆盖 `Stopwatch` 单调时间、`LatencyRecorder` 写入样本、空窗口行为
- [ ] T018 [P] 创建 `src/teams_voice_interpreter/errors.py`：`UserFacingError` 基类（强制两段式 `what_happened` + `next_action` 字段），定义 `BlackHoleMissingError` / `AggregateDeviceMissingError` / `DeepSeekError` / `EdgeTTSError` / `WhisperError` 等子类（依赖 T010）
- [ ] T019 [P] 创建 `src/teams_voice_interpreter/glossary/strings.py` 与 `src/teams_voice_interpreter/glossary/i18n/zh-CN.toml`：所有用户可见字符串键与中文文案，遵循两段式（依赖 T011）
- [ ] T020 [P] 创建 `src/teams_voice_interpreter/config.py`：`Settings` Pydantic 模型 + `pydantic-settings` 加载（`~/.config/teams-voice-interpreter/config.toml` + 环境变量优先级最高 + `.env` 兜底）（依赖 T012）
- [ ] T021 [P] 创建 `src/teams_voice_interpreter/data/session.py`：`Session` + `SessionState` 状态机枚举（IDLE/STARTING/ACTIVE/PAUSED/STOPPING/STOPPED/ERRORED）（依赖 T013）
- [ ] T022 [P] 创建 `src/teams_voice_interpreter/data/{transcript,audio_segment,latency,credential,glossary,crash}.py`：识别 / 翻译 / 音频 / 延迟 / 凭证 / 术语表 / 崩溃报告模型（依赖 T014）
- [ ] T023 创建 `src/teams_voice_interpreter/session/instance_lock.py`：基于 `fcntl.flock(LOCK_EX | LOCK_NB)` 的单实例锁（FR-026），写 PID + 启动时间 + SessionId + Web 端口；含僵尸锁检测（`kill -0 <pid>`）+ atexit 清理（依赖 T015）
- [ ] T024 [P] 创建 `src/teams_voice_interpreter/audio/routing.py`：`AudioDeviceProbe` 类含 `find_blackhole_2ch()` / `find_aggregate_with_blackhole()` / `get_default_input()` / `get_default_output()`（contracts/blackhole-coreaudio.md §2，依赖 T016）
- [ ] T025 [P] 创建 `src/teams_voice_interpreter/perf.py` 骨架：`Stopwatch` 上下文管理器 + `LatencyRecorder` 类（向 `LatencySnapshot` 写入样本，依赖 T017）
- [ ] T026 创建 `tests/perf/fixtures/README.md`：说明 `conference-cn.wav` / `conference-en.wav` / `long-cn-2h.wav` 三份 fixture 的来源（Common Voice + LibriSpeech 商务子集）、SHA256 校验、生成脚本路径；为后续 BM 任务提供录制规范
- [ ] T027 创建 `.github/workflows/ci.yml`（或同级 GitLab CI 配置）：在 push / PR 时跑 `make lint && make typecheck && make test && make benchmark`，benchmark 任一回归阻断合并
- [ ] T028 审计阶段 2 TDD 执行证据：确认 T010–T017 均先失败，再执行 T018–T025；无证据时不得进入用户故事阶段
- [ ] T029 运行 `make test` 跑过 T010–T017 对应单元测试，并确认所有数据模型、错误基类、术语表、配置加载、单实例锁、设备发现就绪

**检查点**：T010–T017 先失败、T018–T025 后实现的 TDD 证据齐全；`make test` 跑过阶段 2 单元测试；所有数据模型、错误基类、术语表、配置加载、单实例锁、设备发现就绪。所有用户故事可并行启动。

---

## 阶段 3：用户故事 1 — 上行同传：用户说中文，对方听到英文（优先级 P1） 🎯 MVP

**故事目标**：用户用中文发言，远端 Teams 同事在 ≤ 800 ms 首段译音延迟下听到流式英文译音；端到端整段 p50 ≤ 2.5 s。

**独立测试**：在 Teams 测试通话中切换麦克风为 BlackHole 2ch，开启上行同传，朗读标准商务中文台词；远端录音可听到流式英文译音；首段译音延迟可由外部时钟测得。

### US1 — 测试先（TDD，必须先写失败测试）

- [ ] T030 [P] [US1] 在 `tests/contract/test_deepseek_streaming.py` 中编写契约测试：覆盖 200 OK SSE / 401 / 402 / 429 退避 / 5xx 退避 / [DONE] 终止符 / SSE 格式异常 / 网络中断（覆盖 contracts/deepseek-translate.md §10）
- [ ] T031 [P] [US1] 在 `tests/contract/test_edge_tts.py` 中编写契约测试：覆盖正常流式 chunks / 401-403 token 自动刷新 / 音色枚举校验 / SSML 注入防御（覆盖 contracts/edge-tts.md §10）
- [ ] T032 [P] [US1] 在 `tests/contract/test_whisper_cpp.py` 中编写契约测试：覆盖模型加载 / partial-final 顺序 / VAD close_segment / 子进程崩溃 / heartbeat 卡死 / 模型降档（覆盖 contracts/whisper-cpp.md §9）
- [ ] T033 [P] [US1] 在 `tests/integration/test_uplink_pipeline.py` 中编写端到端集成测试：fixture `conference-cn.wav` 输入 → BlackHole 2ch 输出字节流；断言首段译音 ≤ 800 ms、整段 ≤ 2.5 s（覆盖 spec US1 验收场景 1–3）

### US1 — 实现前性能基线 benchmark（按 plan.md 复杂度追踪门禁）

**门禁**：T052–T059 必须在 T034–T051 生产实现前完成；若命中宪章 IV 预算违例，暂停进入 US1 实现，直到服务栈替换 / 模型降档 / 宪章修订路径明确。

- [ ] T052 [P] [US1] 在 `tests/perf/test_whisper_resources.py` 中实现 BM-1（small q5_0 稳态 RAM）+ BM-3（Core ML offload CPU），结果写入 `specs/001-teams-voice-interpreter/perf-report.md`「Whisper.cpp 资源」段
- [ ] T053 [P] [US1] 在 `tests/perf/test_whisper_accuracy.py` 中实现 BM-2（small q5_0 vs tiny WER 对比，普通话 30 句测试集），结果写入 perf-report.md「准确率」段
- [ ] T054 [P] [US1] 在 `tests/perf/test_deepseek_latency.py` 中实现 BM-4（DeepSeek streaming 首 token 延迟 200 次商务译句样本），结果写入 perf-report.md「翻译延迟」段
- [ ] T055 [P] [US1] 在 `tests/perf/test_term_numeric_fidelity.py` 中实现 BM-5（术语表注入 + 专有名词 / 数字 / 日期 / 金额 / 人名保留正确率，样本不少于 30 句，覆盖 IT / 法务 / 财务 / 销售场景），结果写入 perf-report.md「术语与数值保真」段，保留正确率目标 ≥ 95%
- [ ] T056 [P] [US1] 在 `tests/perf/test_edge_tts_latency.py` 中实现 BM-6（Edge-TTS 首字节延迟 100 次中英文短句），结果写入 perf-report.md「TTS 延迟」段
- [ ] T057 [P] [US1] 在 `tests/perf/test_blackhole_routing.py` 中实现 BM-8（BlackHole 2ch 路由开销），结果写入 perf-report.md「路由开销」段
- [ ] T058 [US1] 在 `tests/perf/test_first_segment_latency.py` 中实现 BM-10（端到端首段译音 p50 / p95 的最小 benchmark harness：Mic fixture → Whisper streaming → DeepSeek streaming → Edge-TTS → BlackHoleWriter），结果写入 perf-report.md「端到端首段」段（依赖 T052..T057）
- [ ] T059 [US1] 评审 perf-report.md 行 1 / 2 / 3：若任一 BM 超过宪章 IV 当前预算，发布与后续实现必须阻断，按 plan.md 出口动作选择服务栈替换、模型降档或宪章修订 PR

### US1 — 实现

- [ ] T034 [P] [US1] 创建 `src/teams_voice_interpreter/audio/capture.py`：`MicrophoneCapture` 类（基于 `sounddevice.InputStream`，30 ms 帧 @ 16 kHz mono）
- [ ] T035 [P] [US1] 创建 `src/teams_voice_interpreter/audio/playback.py` 中的 `BlackHoleWriter` 类：双通道复制写入 BlackHole 2ch（contracts/blackhole-coreaudio.md §3.1）
- [ ] T036 [P] [US1] 创建 `src/teams_voice_interpreter/stt/vad.py`：基于 `webrtcvad` 的 VAD（mode 2、30 ms 帧、≥ 167 帧静音触发 close_segment）
- [ ] T037 [P] [US1] 创建 `src/teams_voice_interpreter/stt/whisper_streaming.py`：流式 wrapper（支持 partial / final、滑窗 step 300 ms、context 5 s、Metal + Core ML offload）
- [ ] T038 [US1] 创建 `src/teams_voice_interpreter/stt/client.py`：通过 stdin/stdout JSON Lines 与 Whisper 子进程通信（contracts/whisper-cpp.md §5），含 heartbeat（依赖 T037）
- [ ] T039 [P] [US1] 创建 `src/teams_voice_interpreter/mt/prompt.py`：`build_system_prompt(direction, glossary)` 实现术语表注入（contracts/deepseek-translate.md §5）
- [ ] T040 [P] [US1] 创建 `src/teams_voice_interpreter/mt/context_window.py`：滚动 8 句双语对照管理（FR-012）
- [ ] T041 [US1] 创建 `src/teams_voice_interpreter/mt/deepseek_client.py`：`stream_translate()` SSE streaming 客户端（含基础错误分类，重试退避先简化为 1 次、扩展延至 US4 T088）
- [ ] T042 [P] [US1] 创建 `src/teams_voice_interpreter/tts/edge_tts_client.py`：`stream_synthesize()` 客户端（默认 `en-US-AriaNeural`；token 刷新先简化、扩展延至 US4 T089）
- [ ] T043 [US1] 创建 `src/teams_voice_interpreter/tts/audio_writer.py` 中上行专属：流式 mp3 chunk → 16 kHz PCM16 → 写入 BlackHole 2ch；ring buffer 拼接（依赖 T035 + T042）
- [ ] T044 [US1] 创建 `src/teams_voice_interpreter/session/manager.py`：`SessionManager`（仅上行方向首版），含 SessionId 生成（uuid7）+ 单实例锁集成 + 上行管线编排（依赖 T023 + T034 + T038 + T041 + T043）
- [ ] T045 [US1] 创建 `src/teams_voice_interpreter/session/supervisor.py`：仅上行子进程 supervisor（监控 Whisper + Edge-TTS 子进程；先实现 respawn，60s/3 次熔断扩展延至 US4 T091）（依赖 T038）
- [ ] T046 [P] [US1] 创建 `src/teams_voice_interpreter/cli/app.py` 中的 `start` / `stop` / `status` 子命令（基于 Typer，仅上行方向）
- [ ] T047 [P] [US1] 创建 `src/teams_voice_interpreter/web/server.py`：FastAPI ASGI app + lifespan（仅 127.0.0.1 绑定、CORS 禁用）
- [ ] T048 [US1] 创建 `src/teams_voice_interpreter/web/routes/control.py`：`POST /api/control/start` / `/stop`（含 FR-026 单实例 409、FR-006 wizard 未完成 428）（依赖 T044 + T047）
- [ ] T049 [US1] 创建 `src/teams_voice_interpreter/web/routes/status.py`：`GET /api/status` + WebSocket `/ws/status`（仅上行字段、推送频率先 1 Hz、≥ 5 Hz 扩展延至 US3 T077）（依赖 T044）
- [ ] T050 [P] [US1] 创建 `src/teams_voice_interpreter/web/static/index.html` 上行视图骨架（开始 / 停止按钮 + 上行最近识别 / 译文 + DeepSeek 健康徽章）
- [ ] T051 [P] [US1] 创建 `src/teams_voice_interpreter/web/static/app.js` + `src/teams_voice_interpreter/web/static/style.css`（HTMX 基础集成 + 简单 Pico.css 样式 + 原生 WebSocket 客户端）

### US1 — 合规审计

- [ ] T060 [US1] 审计 US1 全部用户可见字符串：100% 引用 `glossary/strings.py` 共享键 + 100% 通过 `errors.UserFacingError` 两段式（宪章 III 门禁）

**检查点**：US1 端到端可用；perf-report.md 已含 BM-1..6 + BM-8 + BM-10 基线；故事 1 全部 3 个验收场景在 T033 集成测试中通过。可作为独立 MVP 上线。

---

## 阶段 4：用户故事 2 — 下行同传：远端说英文，用户听到中文（优先级 P1）

**故事目标**：远端用英文发言，用户在 ≤ 800 ms 首段延迟下从 Mac 默认输出听到流式中文译音；与 US1 共同构成完整双向闭环。

**独立测试**：Teams 测试通话中由对端播放标准商务英文台词；本系统从 BlackHole 2ch 捕获 → 下行管线 → 用户耳机听到流式中文译音；首段延迟可外部测得。

### US2 — 测试先

- [ ] T061 [P] [US2] 在 `tests/integration/test_downlink_pipeline.py` 中编写端到端集成测试：fixture `conference-en.wav` 经 BlackHole 输入 → 默认输出字节流；断言首段译音 ≤ 800 ms（覆盖 spec US2 验收场景 1–3）
- [ ] T062 [P] [US2] 在 `tests/contract/test_audio_routing.py` 中编写契约测试：BlackHole 双通道 → mono 转换、Aggregate Device 检测、设备运行时消失分支（覆盖 contracts/blackhole-coreaudio.md §9）

### US2 — 实现（多数为扩展现有模块支持反向）

- [ ] T063 [P] [US2] 在 `src/teams_voice_interpreter/audio/capture.py` 中追加 `BlackHoleReader` 类：从 BlackHole 2ch 输入端 30 ms 帧捕获、双通道平均到 mono（contracts/blackhole-coreaudio.md §4.3）
- [ ] T064 [P] [US2] 在 `src/teams_voice_interpreter/audio/playback.py` 中追加 `DefaultOutputWriter` 类：写入 Mac 默认输出设备（中文译音回放）
- [ ] T065 [US2] 扩展 `src/teams_voice_interpreter/stt/client.py` 支持下行第二个 Whisper 子进程实例（语言固定 `en`）（依赖 T063）
- [ ] T066 [US2] 扩展 `src/teams_voice_interpreter/mt/prompt.py` + `src/teams_voice_interpreter/mt/deepseek_client.py` 支持英 → 中方向（独立流式连接）
- [ ] T067 [US2] 扩展 `src/teams_voice_interpreter/tts/edge_tts_client.py` 支持中文音色 `zh-CN-XiaoxiaoNeural`
- [ ] T068 [US2] 扩展 `src/teams_voice_interpreter/tts/audio_writer.py` 支持下行（默认输出写入路径，依赖 T064 + T067）
- [ ] T069 [US2] 扩展 `src/teams_voice_interpreter/session/manager.py` 支持双向编排：上 / 下两条管线并行运行；共用 SessionId、共用滚动上下文窗口（独立两条 8 句历史）
- [ ] T070 [US2] 扩展 `src/teams_voice_interpreter/session/supervisor.py` 监控下行子进程（Whisper-en + Edge-TTS-zh）
- [ ] T071 [US2] 扩展 `src/teams_voice_interpreter/web/routes/status.py` 在 `/api/status` 与 WebSocket 中追加下行字段（`latest_downlink`、下行延迟统计）
- [ ] T072 [US2] 扩展 `src/teams_voice_interpreter/web/static/index.html` 增加下行视图：「下行（英 → 中）」面板、下行最近识别 / 译文区
- [ ] T073 [US2] 在 `tests/perf/test_downlink_first_segment_latency.py` 中实现 BM-9（Aggregate Device 同时给两个目标的 jitter ≤ 10 ms）+ BM-10D（下行端到端首段译音 p50 / p95：BlackHoleReader 捕获首个非静音字节 → DefaultOutputWriter 写入默认输出设备首个非静音字节），结果写入 perf-report.md「Aggregate jitter」与「下行端到端首段」段，验证 SC-002
- [ ] T074 [US2] 端到端跑通 T061：US1 + US2 并行运行 60 秒不串扰（spec US2 验收场景 3）

**检查点**：US1 + US2 共同构成完整双向同传闭环；perf-report.md 已含 BM-9 + BM-10D。

---

## 阶段 5：用户故事 3 — 实时状态面板（优先级 P2）

**故事目标**：用户在会议进行时通过 `http://localhost:8765` 看到运行时长、双向最近识别 / 译文、滚动 p50/p95 延迟、三服务连接健康状态；面板从事件发生到 DOM 更新 ≤ 1 秒；推送 ≥ 5 Hz。

**独立测试**：双向同传运行中打开本地 Web 控制台；任意时刻面板显示的延迟数字与外部探针测得的延迟差异 ≤ 200 ms；服务异常时 ≤ 5 秒在面板显示「非健康」+ 两段式提示；浏览器标题 / favicon 徽标可提示异常，但不得调用浏览器原生 `Notification` API 或产生 macOS 系统通知。

### US3 — 测试先

- [ ] T075 [P] [US3] 在 `tests/unit/data/test_latency_snapshot.py` 中编写单元测试：覆盖滚动 60 s 窗口入队 / 过期剔除、p50 / p95 / avg / max 计算正确性（数据集对比 numpy.percentile）
- [ ] T076 [P] [US3] 在 `tests/integration/test_status_panel.py` 中编写集成测试：模拟一连串 transcript / translation 事件 → 验证 WebSocket 推送 ≥ 5 Hz、面板「最近识别」≤ 1 秒更新（覆盖 spec US3 验收场景 1–3）

### US3 — 实现

- [ ] T077 [US3] 完整实现 `src/teams_voice_interpreter/perf.py` 中的 `LatencyRecorder`：滚动 60 s 窗口、各 stage p50/p95/avg/max 实时计算（依赖 T025 骨架）
- [ ] T078 [US3] 升级 `src/teams_voice_interpreter/web/routes/status.py` 的 WebSocket 推送频率至 ≥ 5 Hz（200 ms 间隔），并加入 `transcript_partial` / `transcript_final` / `translation_first_token` / `translation_completed` / `service_error` 事件流（contracts/web-control-api.md §4.1）
- [ ] T079 [P] [US3] 升级 `src/teams_voice_interpreter/web/static/index.html` 加入完整状态面板：latency-panel（首段 / 整段 p50/p95）、services-health 三徽章、Toast 容器
- [ ] T080 [P] [US3] 升级 `src/teams_voice_interpreter/web/static/app.js`：`service_error` / `subprocess_circuit_break` 事件触发页内 Toast + 浏览器标题 / favicon 徽标提示；显式禁止 `Notification.requestPermission()` 与任何浏览器原生通知调用（contracts/web-control-api.md §5.3）
- [ ] T081 [US3] 审计面板字符串：100% 引用 `glossary/strings.py`；服务健康状态徽章用统一 enum（healthy / degraded / unavailable）+ 中文文案（宪章 III 门禁）

**检查点**：状态面板满足 SC-008 ≤ 1 s 滞后 + FR-016 全字段；US1+US2+US3 三故事独立可用。

---

## 阶段 6：用户故事 4 — 启停、暂停、设备切换、故障自愈（优先级 P3）

**故事目标**：单次操作启停整个双向会话；网络瞬断 ≤ 30 s 自动重连显式告知；设备切换不丢上下文；进程内 supervisor 在子进程崩溃时 ≤ 5 s 自动 respawn 且 60 s/3 次熔断；崩溃时写匿名报告。

**独立测试**：模拟网络断开 5/15/30 s、模拟切换默认输入设备、模拟点击「暂停」再「继续」、模拟 `kill -9 <whisper-pid>`；每种动作后系统在 ≤ 30 s 内恢复，状态面板与音频侧均无残留。

### US4 — 测试先

- [ ] T082 [P] [US4] 在 `tests/integration/test_supervisor.py` 中编写集成测试：覆盖 spec US4 验收场景 1（设备切换 5 s 接管）/ 2（暂停继续 2 s 恢复）/ 3（DeepSeek / Whisper STT / Edge-TTS 三类服务分别网络瞬断 5 / 15 / 30 s 重连 + ≤ 5 s 状态提示 + 「已恢复」提示）/ 4（Whisper 子进程 kill -9 → 5 s 内 respawn + 上下文不丢）
- [ ] T083 [P] [US4] 在 `tests/unit/session/test_supervisor.py` 中编写单元测试：heartbeat 超时 3 s 视为卡死、60 s 滚动窗口 ≥ 3 次崩溃熔断、respawn 不清空 SessionId / 滚动上下文 / 术语表；断言 FR-018 对 STT / MT / TTS 的 retry 状态事件都先于最终失败事件推送
- [ ] T084 [P] [US4] 在 `tests/unit/session/test_crash_reporter.py` 中编写单元测试：脱敏（家目录 → ~）、禁字段断言（音频 / 文本 / Key 必缺失）、轮转策略（保留最新 20 份）

### US4 — 实现

- [ ] T085 [P] [US4] 在 `src/teams_voice_interpreter/cli/app.py` 中追加 `pause` / `resume` 子命令；CLI 通过 Unix socket（`/tmp/teams-voice-interpreter.sock`）与运行中的主进程通信
- [ ] T086 [P] [US4] 在 `src/teams_voice_interpreter/web/routes/control.py` 中追加 `POST /api/control/pause` / `/resume`，状态机校验（仅 ACTIVE → PAUSED → ACTIVE）
- [ ] T087 [US4] 扩展 `src/teams_voice_interpreter/session/manager.py` 加入完整状态机转换 + `async migrate_session()`（设备切换、≤ 5 s 自动接管新设备）
- [ ] T088 [US4] 扩展 `src/teams_voice_interpreter/mt/deepseek_client.py` 加入完整指数退避（250/500/1000/2000/4000 ms）+ FR-018 状态推送 + FR-019 30 s 不可恢复降级
- [ ] T089 [US4] 扩展 `src/teams_voice_interpreter/tts/edge_tts_client.py` 加入完整瞬时失败 retry、401/403 token 自动刷新（最多 3 次）、FR-018 状态推送 + 连续 3 次失败触发 plan 复杂度追踪行 4 降级
- [ ] T090 [US4] 扩展 `src/teams_voice_interpreter/stt/client.py` 加入 Whisper streaming 读流中断 / 子进程短暂不可用的 FR-018 retry 状态推送 + 30 s 不可恢复降级（FR-019）
- [ ] T091 [US4] 完整实现 `src/teams_voice_interpreter/session/supervisor.py`：60 s 滚动窗口熔断、heartbeat 卡死检测、respawn 不丢上下文（FR-028）
- [ ] T092 [US4] 完整实现 `src/teams_voice_interpreter/session/crash_reporter.py`：信号处理器（SIGTERM/SIGSEGV）+ atexit + sys.excepthook 三重写出 + 字段过滤 + 0600 权限 + 20 份轮转（FR-029）
- [ ] T093 [US4] 扩展 `src/teams_voice_interpreter/audio/routing.py` 加入运行期设备消失监控 + FR-020 立即停止该方向 + 两段式提示
- [ ] T094 [US4] 端到端跑通 T082 全部 4 个验收场景

**检查点**：US1+US2+US3+US4 全部独立可用；进程级故障自愈 + 匿名崩溃报告就绪。

---

## 阶段 7：收尾与横切关注点（首次运行向导 + 导出 + 长时长稳定性 + 文档收尾）

**目的**：跨故事的横切能力（向导 / 导出）+ 长时长 perf benchmark + 最终文档审阅。

- [ ] T095 创建 `src/teams_voice_interpreter/cli/wizard.py`：FR-006 首次运行向导 7 步骤（BlackHole 安装 → Aggregate Device → Teams 路由 → 麦克风权限 → API 凭证 → 术语表配置 → SC-011 免责勾选）+ 每步 RT-1..RT-6 自检
- [ ] T096 在 `tests/integration/test_wizard.py` 中编写集成测试：覆盖每步成功路径 + 每步失败的两段式提示（mock CoreAudio 设备列表、mock DeepSeek ping、mock fcntl）
- [ ] T097 创建 `src/teams_voice_interpreter/session/exporter.py`：FR-027 Markdown 导出器（含会话头部元数据、双向时间戳交错、禁延迟元数据 / 置信度 / 音频字节）
- [ ] T098 创建 `src/teams_voice_interpreter/web/routes/export.py`：`POST /api/export` 端点 + 30 秒导出窗口逻辑 + 410 Gone 处理（contracts/web-control-api.md §3.6）
- [ ] T099 [P] 在 `tests/integration/test_export.py` 中编写集成测试：覆盖会话进行中导出 / 会话停止后窗口内导出 / 窗口过期 410 / 导出文件 schema（FR-027）
- [ ] T100 在 `tests/perf/test_edge_tts_24h.py` 中实现 BM-7（每分钟一次合成请求、24 h 监测 401/403 失败率），结果写入 perf-report.md「Edge-TTS 24h」段
- [ ] T101 在 `tests/perf/test_end_to_end_latency.py` 中实现 BM-11（端到端整段 p50 / p95），结果写入 perf-report.md「端到端整段」段
- [ ] T102 在 `tests/perf/test_long_session_stability.py` 中实现 BM-12（60 分钟双向同传 0 中断），结果写入 perf-report.md「长会话稳定性」段
- [ ] T103 在 `tests/perf/test_memory_leak.py` 中实现 BM-13（24 h 持续运行内存增长 ≤ 5%），结果写入 perf-report.md「内存增长」段
- [ ] T104 [P] 创建 `scripts/install-blackhole.sh`：`brew install blackhole-2ch` 包装脚本 + 重启提示
- [ ] T105 完整撰写 `README.md`：项目简介、快速开始链接、SC-011 监管严格场景免责声明完整正文、贡献指南骨架、License、宪章合规性说明
- [ ] T106 撰写并审定 `specs/001-teams-voice-interpreter/perf-report.md` 总体结论段：14 项 BM 全部通过 / 部分命中宪章 IV 违例时附服务栈替换、模型降档或宪章修订 PR 处置链接
- [ ] T107 跑通 `quickstart.md` 全流程（在新 Mac 或干净虚拟环境）：从 `pip install -e .` 到首次成功译音 ≤ 15 分钟（验证 SC-007）；同步执行分发产物清单审计，确认 `.app` / Teams 插件或 Add-in / Office Add-in / 本项目分发的内核扩展数量均为 0，并把结果写入 `perf-report.md`「分发形态合规」段（验证 SC-009）
- [ ] T108 [P] 全量代码审阅：`radon cc -a -nb` 验证圈复杂度 ≤ 10、单文件 ≤ 300 行；超阈值在 plan.md 复杂度追踪登记
- [ ] T109 [P] 全量 docstring + 类型注解审阅：每个模块顶端 docstring、每个公共符号 type hint
- [ ] T110 死代码 / 未使用 import / 注释代码 / 无 owner TODO 清理；`ruff check --select F401,F841,T201` 零警告
- [ ] T111 与 git-master 联动：在合适时机做 initial commit + 完善 `.gitignore`（含 `.omc/state/` `.omc/sessions/`）+ 关联 `001-teams-voice-interpreter` 分支至 `main` 的合并预演

---

## 依赖与执行顺序

### 阶段依赖

- **阶段 1（准备）**：无依赖，立即开始
- **阶段 2（基础）**：依赖阶段 1 完成 — **阻塞**全部用户故事
- **阶段 3+（用户故事）**：均依赖阶段 2 完成
  - US1（P1）即 MVP，建议先单独完成
  - US2（P1）可在 US1 共享管线接口稳定后开始；多人团队可并行准备测试与路由验证，但不得抢先改动未稳定的 US1 共享模块
  - US3（P2）可在阶段 2 后并行开发面板骨架与 mock 数据流；真实数据流验收依赖 US1 + US2 完成
  - US4（P3）可在 US3 完成后开始；部分扩展现有 US1 / US2 模块
- **阶段 7（收尾）**：依赖所选用户故事全部完成

### 用户故事依赖

- **US1（P1 / MVP）**：仅依赖阶段 2；交付完整上行同传，可独立上线
- **US2（P1）**：依赖阶段 2 + US1 的共享管线接口稳定；多数任务为扩展 US1 已有模块支持反向，可在 US1 的 `audio` / `stt` / `mt` / `tts` / `session` 接口稳定后开始
- **US3（P2）**：面板骨架与 mock 数据流仅依赖阶段 2；真实数据流验收依赖 US1/US2 完成
- **US4（P3）**：扩展 US1/US2 已有模块的容错与恢复逻辑；建议在 US1/US2 稳定后再开始

### 用户故事内部

- 测试任务（T010–T017 / T030–T033 / T061–T062 / T075–T076 / T082–T084）**必须**先于对应实现任务编写并失败
- 数据模型（阶段 2）→ 服务（管线模块）→ 端点（CLI / Web）→ 集成测试通过
- 实现前 perf benchmark（T052–T059）必须在 T034–T051 生产实现前完成；其余 perf benchmark（T073 / T100–T103）必须在对应故事检查点前完成

### 并行机会

- 阶段 1 中 T003 / T004 / T005 / T006 / T007 / T008 可并行
- 阶段 2 中 T010..T017（测试）可并行；测试先失败后，T018..T025（实现）可并行
- 阶段 3 US1 中 T030..T033（测试）可并行；T052..T057（实现前 perf）可并行；T034..T037 + T039..T040 + T042（实现）可并行；T046..T047 + T050..T051 可并行
- 阶段 4 US2 中 T061..T062 可并行；T063..T064 可并行
- 阶段 5 US3 中 T075..T076 可并行；T079..T080 可并行
- 阶段 6 US4 中 T082..T084 可并行；T085..T086 可并行
- 多人团队：阶段 2 完成后可并行准备各故事的测试、mock、非共享文件与验证脚本；进入共享模块实现或真实验收时，必须遵守上方用户故事依赖

---

## 并行执行示例

### 阶段 2 基础并行启动（单人单 IDE）

```bash
# 先同时编写以下失败测试（互不干扰）：
$EDITOR tests/unit/errors/test_user_facing_error.py        # T010
$EDITOR tests/unit/glossary/test_strings.py               # T011
$EDITOR tests/unit/config/test_settings.py                 # T012
$EDITOR tests/unit/data/test_session_state.py              # T013
$EDITOR tests/unit/data/test_models.py                     # T014
$EDITOR tests/unit/session/test_instance_lock.py           # T015
$EDITOR tests/unit/audio/test_routing.py                   # T016
$EDITOR tests/unit/perf/test_stopwatch_latency_recorder.py # T017
```

### 阶段 3 US1 测试先并行启动

```bash
# 同时编写以下契约 / 集成测试（互不干扰，且全部应当 FAIL 直到对应实现就位）：
$EDITOR tests/contract/test_deepseek_streaming.py   # T030
$EDITOR tests/contract/test_edge_tts.py             # T031
$EDITOR tests/contract/test_whisper_cpp.py          # T032
$EDITOR tests/integration/test_uplink_pipeline.py   # T033
```

### 阶段 3 US1 实现并行启动

```bash
# T030–T033 测试和 T052–T059 实现前性能门禁就位后，并行实现这些独立模块：
$EDITOR src/teams_voice_interpreter/audio/capture.py             # T034
$EDITOR src/teams_voice_interpreter/audio/playback.py            # T035
$EDITOR src/teams_voice_interpreter/stt/vad.py                   # T036
$EDITOR src/teams_voice_interpreter/stt/whisper_streaming.py     # T037
$EDITOR src/teams_voice_interpreter/mt/prompt.py                 # T039
$EDITOR src/teams_voice_interpreter/mt/context_window.py         # T040
$EDITOR src/teams_voice_interpreter/tts/edge_tts_client.py       # T042
```

---

## 实施策略

### MVP 优先（仅 US1）

1. 完成阶段 1（准备）
2. 完成阶段 2（基础 — 阻塞全部故事）
3. 完成阶段 3 的 T030–T033 测试先行任务与 T052–T059 实现前性能门禁
4. 若 BM-10 命中宪章 IV 预算违例：暂停 US1 实现，选择服务栈替换、模型降档或宪章修订 PR；否则继续完成 T034–T051 实现
5. **暂停并验证**：T033 集成测试通过；T058 BM-10 实测仍达标 + T059 评审 perf-report.md，才可作为 MVP 上线

### 增量交付（推荐节奏）

1. **Sprint 1**：准备 + 基础完成 → 基础就绪
2. **Sprint 2**：US1 完成 → 单向上行同传 MVP 上线
3. **Sprint 3**：US2 完成 → 双向同传完整闭环
4. **Sprint 4**：US3 完成 → 完整状态面板可观测
5. **Sprint 5**：US4 完成 → 故障自愈生产级
6. **Sprint 6**：收尾（向导 + 导出 + 长时长 perf + README + 全量审阅）→ v1.0 RC

### 并行团队策略

3 人团队推荐分工：

1. 全员合力完成准备 + 基础（约 1.5 个 Sprint）
2. 基础完成后：
   - **Dev A**：US1 完整管线 + perf benchmark（约 2 Sprint）
   - **Dev B**：US2 反向扩展（在 US1 模块上扩展，需 US1 接口稳定后开始）+ 收尾中的导出（FR-027）
   - **Dev C**：US3 + US4（容错与可观测，相对独立于上下行管线本身）
3. 收尾阶段全员合力完成长时长 perf + 文档审阅 + initial commit

---

## 备注

- `[P]` 任务 = 不同文件、无未完成依赖
- `[Story]` 标签把任务关联到具体用户故事，便于追溯与独立验证
- 每个用户故事必须独立可完成、独立可测试
- TDD 强制：测试**必须**先写并失败再实现（宪章 II）
- perf benchmark 强制：每个故事的检查点前必须有对应 BM 任务（宪章 IV）
- 共享术语表 + 错误两段式合规校验是每个故事检查点的硬门禁（宪章 III）
- 提交节奏：建议每完成一个任务或一个逻辑组就 commit 一次
- 每个检查点都可暂停验证、独立 demo
- 避免：模糊任务、跨故事文件冲突、破坏故事独立性的依赖
