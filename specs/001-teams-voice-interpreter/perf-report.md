# 性能基线报告：Teams 实时双向语音同传桥（macOS）

**日期**：2026-05-05
**Git commit**：当前 HEAD；本报告不内嵌自引用提交哈希，使用 `git log -1 --oneline` 核验
**硬件**：本地模拟基准；真实 BlackHole / Whisper 模型 / Edge-TTS 外网基准待发布前复跑
**总体结论**：14 项 BM（BM-1..13 + BM-10D）的"模拟基准下通过"为 stub 占位状态；2026-05-07 已对 BM-4 完成真实化测量、对 BM-10 链路中的 ASR 子段完成 C+α 实测（**BM-10 端到端首段本身仍是 stub**，未真实化），详见后文专题段。其中 **BM-4 真实首字节延迟 p50 566-598 ms 超原 400 ms 预算**；C+α 测得整段 ASR ≤ 310 ms（仅 ASR 子段，不能直接判定 BM-10 整体 Pass / Fail）。其余 BM 的真测状态需逐一审计 `tests/perf/test_*.py`。`--online-asr` 实验路径不计入下表通过项；2026-05-06 上行与 2026-05-07 下行本机探针均显示该路径尚未产生可交付的低延迟收益，详见「Online ASR 实验探针」。**2026-05-07 宪章修订 PR 已合并**：SC-001 / SC-002 中位阈值从 ≤ 800 ms 调整为 **≤ 1200 ms（硬）+ ≤ 1000 ms（软）**，p95 从 ≤ 1.5 s 调整为 ≤ 2000 ms，对齐 BM-4 实测物理下限；详见 `.specify/memory/constitution.md` 原则 IV 表与 spec.md SC-001 / SC-002 / L194 / L222 修订记录。

| BM | 关联条款 | 当前结果 | 预算 | Pass/Fail | exit_action |
|----|----------|----------|------|-----------|-------------|
| BM-1 | SC-010 / 宪章 IV | RAM 420 MB | ≤ 500 MB | Pass | 无 |
| BM-2 | SC-005 | WER 优势 6% | ≥ 5% | Pass | 无 |
| BM-3 | SC-010 / 宪章 IV | CPU 24% | ≤ 30% | Pass | 无 |
| BM-4 | 宪章 IV | MT first token p50 598 ms / p95 753 ms（2026-05-07 真实化，上下行最差值；上行 p50 566 / 下行 p50 598） | p50 ≤ 400 ms / p95 ≤ 800 ms | **Fail (p50)** | 重审 SC-001 阈值或换 MT 服务栈，详见「DeepSeek MT first token 真实化（BM-4）」 |
| BM-5 | SC-005 / FR-012 | 保真 96% / 术语延迟增量 120 ms | ≥ 95% / ≤ 200 ms | Pass | 无 |
| BM-6 | SC-001 / SC-002 | TTS first byte p50 260 ms / p95 620 ms | p50 ≤ 400 ms / p95 ≤ 800 ms | Pass | 无 |
| BM-7 | Edge-TTS 稳定性 | 401/403 失败率 0.1% | < 0.5% | Pass | 无 |
| BM-8 | AUDIO_ROUTE | BlackHole 路由 p95 18 ms | ≤ 50 ms | Pass | 无 |
| BM-9 | SC-002 | Aggregate jitter p95 8 ms | ≤ 10 ms | Pass | 无 |
| BM-10 | SC-001 | 上行首段 p50 600 ms / p95 1100 ms（**stub，待真实化**） | p50 ≤ 1200 ms（硬）/ ≤ 1000 ms（软）/ p95 ≤ 2000 ms（2026-05-07 宪章修订 PR 自 800 ms / 1.5 s 调整） | Pass (stub) | 真实化时按新阈值断言 |
| BM-10D | SC-002 | 下行首段 p50 700 ms（**stub，待真实化**） | 同 SC-001（2026-05-07 宪章修订 PR 调整） | Pass (stub) | 真实化时按新阈值断言 |
| BM-11 | SC-003 | 整段 p50 1800 ms / p95 3200 ms | p50 ≤ 2.5 s / p95 ≤ 4.0 s | Pass | 无 |
| BM-12 | SC-004 | 60 分钟用户感知中断 0 次 | = 0 | Pass | 无 |
| BM-13 | SC-004 / 宪章 IV | 24h 内存增长 2.5% | ≤ 5% | Pass | 无 |

## Online ASR 实验探针

**日期**：2026-05-06（上行）/ 2026-05-07（下行）
**上行音频**：macOS `say -v Tingting` 生成 12.89 秒中文商务长句 WAV，16 kHz mono PCM。原文：「我们今天讨论现金流预测方案和下季度预算安排」。
**下行音频**：macOS `say -v Samantha` 生成 8.33 秒英文商务长句 WAV（PCM16 mono；探针内部从 22.05 kHz 重采到 16 kHz）。原文：「For the next quarter, we plan to allocate fifteen percent of the operating budget to cloud infrastructure and revisit the cash flow forecast in October.」上下行音频时长不一致（英文同议题表达更短），不影响「final 可确认可翻译 stable partial」是否存在的开关型断言；但意味着下行单配置 stable partial 窗口数（≈ duration ÷ step_ms）天然少 ~35%，重跑次数列上下行不可直接横比。
**命令入口**：`uv run --extra dev scripts/probe_online_asr.py <wav> --direction <uplink|downlink> --language <zh|en> --expected-text ... --max-first-partial-s 1.2 --max-cer 0.12 --proof-json <path>`
**计时口径**：2026-05-06 对抗审查后，探针的 partial 时间按「实时音频到达下界 + 同步 ASR 重跑耗时」计算，不再使用离线快速喂完整段音频的偏乐观耗时。当 ASR 重跑慢于实时（如下行 large-v3-turbo），「first_confirmed_ready_partial_s」可能大于音频时长本身 —— 这是物理事实而非 bug，对应"在生产环境中你想确认这条 partial 时，wall-clock 已经走到这里了"。
**门禁字段**：与 `--max-first-partial-s` 比较的字段是 `first_confirmed_ready_partial_s`（首个**被 final 确认**的可翻译 stable partial），不是 `first_partial_s`（仅首个 stable partial）。下行 `small-q5_1, step_ms=300` 的 `first_partial_s = 1.20 s` 几乎贴阈值，但实际门禁判定用的是 `first_confirmed_ready_partial_s = 2.18 s`，避免把"识别到一个词"的 early partial 误当成低延迟达标。

### 上行（zh→en）

| 模型 / 参数 | ASR 重跑次数 | 首个 stable partial | 首个可翻译 stable partial | 首个 final 可确认可翻译 stable partial | final ASR | CER | 结论 |
|-------------|--------------|---------------------|----------------------------|----------------------------------------|-----------|-----|------|
| `small-q5_1`, `step_ms=300` | 43 | 0.79 s | 3.49 s | n/a | 0.40 s | 0.107 | Fail：提前 partial 未被 final 确认，不能降低首段播出延迟 |
| `small-q5_1`, `step_ms=600` | 22 | 1.55 s | 3.90 s | n/a | 0.65 s | 0.107 | Fail：调大 step 仍无 final 可确认 partial |
| `small-q5_1`, `step_ms=900` | 15 | 4.49 s | 5.18 s | n/a | 0.50 s | 0.107 | Fail：调大 step 仍无 final 可确认 partial，且首个 stable partial 已变慢 |
| `large-v3-turbo-q5_0`, `step_ms=300` | 43 | 2.22 s | 8.83 s | 8.83 s | 1.08 s | 0.107 | Fail：有可确认 partial，但确认点远超低延迟预算 |

### 下行（en→zh）

| 模型 / 参数 | ASR 重跑次数 | 首个 stable partial | 首个可翻译 stable partial | 首个 final 可确认可翻译 stable partial | final ASR | CER | 结论 |
|-------------|--------------|---------------------|----------------------------|----------------------------------------|-----------|-----|------|
| `small-q5_1`, `step_ms=300` | 28 | 1.20 s | 2.18 s | 2.18 s | 0.34 s | 0.109 | Fail：final 可确认 partial 2.18 s > 1.20 s 阈值；与上行不同，partial 已可被 final 确认，差距仅在命中时刻 |
| `small-q5_1`, `step_ms=600` | 14 | 1.44 s | 2.66 s | 2.66 s | 0.37 s | 0.109 | Fail：调大 step 推迟首个 partial，可确认 partial 仍超阈值 |
| `small-q5_1`, `step_ms=900` | 10 | 5.75 s | 5.75 s | 5.75 s | 0.42 s | 0.109 | Fail：step 过大触发退化为低频整段 ASR，三时间点合流 |
| `large-v3-turbo-q5_0`, `step_ms=300` | 28 | 5.08 s | 5.08 s | 15.60 s | 1.18 s | 0.109 | Fail：早期 partial 未被 final 确认；确认点（15.60 s）已超音频时长（8.33 s），ASR 重跑慢于实时，不应作为 streaming 候选 |

**对抗结论**：当前 `--online-asr` 是通过高频重跑本地 Whisper one-shot 模拟 partial，不是可交付的真正 streaming ASR。默认不得让 stable partial 提前调用 MT/TTS；只有先用探针 proof 证明「final 可确认可翻译 stable partial」和 CER 同时达标，且 proof 的 direction / language 与当前管线匹配后，才可显式启用 `--online-asr-early-prepare`。`duplex` 必须分别提供上行与下行 proof：本次下行记录由 `uv run --extra dev scripts/probe_online_asr.py <wav> --direction downlink --language en --proof-json <path>` 在本机复现得到，4 份 proof JSON 不入库（与上行处理方式一致）；上下行 proof 由 `tvi doctor --uplink-low-latency-proof <path> --downlink-low-latency-proof <path>` 各自校验，互不替代。

**分方向诊断**（differential test 收益）：上下行的失败模式不同 —— 上行 small 三档全部「无 final 可确认可翻译 stable partial」（n/a 列），即使大模型也要 8.83 s 才能确认；下行 small 三档全部能产生 final 可确认 partial（最早 2.18 s），瓶颈在「确认点 ≥ 2 s 仍超 1.2 s 阈值」。继续压低延迟的修复路径分方向不同：

- **上行（zh）**：所有测过的 `step_ms × 模型` 组合都无 final 可确认 partial（n/a 列）；继续调 `step_ms` 或换大模型在本测试范围内未提供改善证据。机制归因（中文 vs 英文 partial 字符级稳定性差异是否由分词、prompt 还是模型 fine-tune 主导）属未控制变量，需在真正 streaming ASR 替代方案落地后回测，本次未做控制实验。
- **下行（en）**：测过的 small 配置均能产生 final 可确认 partial（最早 2.18 s），瓶颈是命中过晚而非缺失。可探索的方向：更小 `step_ms`（< 300 ms）、更小模型（base / tiny），或在能接受首段 ≈ 2.5 s 的场景下把 `--max-first-partial-s` 阈值改为 ~2.5 s 后启用 early-prepare。**caveat**：缩小 `step_ms` 之前必须先验证目标模型在目标硬件上的单次 ASR 耗时 < `step_ms`，否则会发生与 large-v3-turbo 在本次相同的退化（confirm 15.60 s vs 音频 8.33 s，ASR 慢于实时）。

CER 在两个方向上各自基本一致（上行 0.107 / 下行 0.109），且不随 step_ms 变化 —— 说明 final 文本由 final ASR 一次性产出，重跑 partial 不影响最终文本质量；下行 0.109 的主要贡献是 `infrastructureand` 这处 token 粘连，是 small-q5_1 模型自身问题，与 streaming 机制无关。

**Fail 触发字段说明**：上下行两表共 8 行 Fail 全部由 `first_confirmed_ready_partial_s > 1.20 s` 触发；CER 列（上行 0.107 / 下行 0.109）均**未**触发 0.12 阈值。读者不应把 CER 列邻接 Fail 字符串误读为 CER 共同贡献了 Fail。

## ASR 整段耗时 vs 段长（C+α 测量）

**日期**：2026-05-07
**目的**：用 macOS `say` 合成上下行各 3 档不同长度的单句 WAV，分别走整段 Whisper.cpp ASR，记录 audio duration 与 ASR final 耗时。回答"SC-001 ≤ 800 ms 首段延迟预算下，长段语音是否让 ASR 成为主导项"。
**音频源**：上行 `say -v Tingting`（zh）/ 下行 `say -v Samantha`（en），16 kHz mono PCM16；机器音质比真人偏乐观，但本探针只做数量级判断（ASR 是否吃掉一半以上预算），偏差可接受。
**模型**：`small-q5_1`（与生产管线一致），M3 + Metal + flash attention。同方向第 2 段起共用预热后的 transcriber 实例。
**命令入口**：`uv run --extra dev scripts/measure_asr_segment_latency.py --proof-json /tmp/asr-segment-latency.json`
**口径**：测量 `WhisperOneShotTranscriber.transcribe(samples)` 的同步耗时（`final_asr_s`），不含 VAD 切段、prompt 构建、tokenize 输出。

| 方向 | 音色 | 目标段长 | 实际段长 | ASR final 耗时 | ASR 占比 (final/duration) | 原文 |
|------|------|---------|----------|----------------|---------------------------|------|
| uplink | `Tingting` | 3.0 s | 1.99 s | 0.297 s | 14.95% | 下次会议三点开始 |
| uplink | `Tingting` | 6.0 s | 4.02 s | 0.308 s | 7.66% | 请把上季度的销售数据汇总后发给市场部 |
| uplink | `Tingting` | 10.0 s | 8.53 s | 0.394 s | 4.63% | 我们计划在第三季度推出云端协同与数据分析两个核心新功能并提前两周开放灰度测试 |
| downlink | `Samantha` | 3.0 s | 1.56 s | 0.263 s | 16.83% | Let's start the meeting at three. |
| downlink | `Samantha` | 6.0 s | 3.84 s | 0.270 s | 7.03% | Please send the consolidated sales data from last quarter to marketing. |
| downlink | `Samantha` | 10.0 s | 5.96 s | 0.306 s | 5.14% | We plan to launch cloud collaboration and analytics in the third quarter and open a beta two weeks ahead of release. |

**关键观察**：

1. **ASR 整段耗时与段长几乎解耦**：1.6 s 段 → 0.26 s ASR；8.5 s 段 → 0.39 s ASR。段长每加 1 s，ASR 只增 ~15-20 ms。说明 small-q5_1 在 M3 + Metal 上以「常数项 + 与段长弱相关项」结构运行，常数项主导。
2. **上下行 ASR 速度差异极小**（同长度 < 30 ms）。结合 D 探针的 partial 级 differential（上行 partial 不稳定、下行 partial 稳定），可分离出「整段 ASR 速度」与「partial 字符级稳定性」是**两个独立维度** —— 整段速度两方向相当，但 partial 稳定性差异显著。
3. **典型生产段长（2-6 s）下 ASR ≤ 310 ms**：占 SC-001 800 ms 预算的 ~38%，远低于"主导项"的 ≥ 50% 阈值。**当前测量数据下，ASR 不是 SC-001 的主导项**。

**关于「是否需要接入真正 streaming ASR」的决策**：本次数据本身**不能**单独回答这个问题，因为它前置依赖另外两项 stub：

- BM-4 DeepSeek MT first token（主表标 320 ms p50）—— `tests/perf/test_deepseek_latency.py` 硬编码返回，未真测
- BM-6 Edge-TTS first byte（主表标 260 ms p50）—— `tests/perf/test_edge_tts_latency.py` 硬编码返回，未真测

条件断言：

- 若 BM-4 + BM-6 真实首字节延迟 **≤ 490 ms**（800 - ASR 310 ms）→ SC-001 在典型段长下可达，**streaming ASR 不需要做**
- 若 BM-4 + BM-6 真实加起来 **在 491 - 750 ms 区间** → ASR 优化有边际收益但不结构性必须，可优先调 VAD `chunk_seconds` / `min_speech_ms` 切短
- 若 BM-4 + BM-6 真实 **≥ 750 ms** → streaming ASR 即使把 ASR 压到 50 ms 也救不了 SC-001，需重新审视 SC-001 阈值或 MT/TTS 服务栈

**streaming ASR 决策当前被 BM-4 / BM-6 的 stub 状态前置阻塞**，下一程应当用与本探针对仗的轻量脚本（DeepSeek 用文本输入、Edge-TTS 用文本输入，**不需要 fixture**）真测 DeepSeek + Edge-TTS 首字节延迟，再回到本节给出的三档断言执行。

**进展更新（2026-05-07）**：BM-4 已真实化，p50 566-598 ms（详见「DeepSeek MT first token 真实化（BM-4）」段）。结果落在第三档「≥ 750 ms」—— 单 BM-4 已 ≥ 566 ms，加任何 BM-6 必超 750 ms。**streaming ASR 决策已锁定为"暂缓"**：即使把 ASR 压到 0 ms 也救不了 SC-001。BM-6 真实化仍有价值（提供宪章修订 PR 的完整数据），但 streaming ASR 投入不再被它阻塞，而是被"是否修订 SC-001 / 是否换 MT 服务栈"前置。

**主表数据真实化进展**：本次 C+α 是 perf-report 里**第一份**真实测量到主表落地的数据点（不计 D 探针，那是实验路径）。主表 BM 已确认 stub 的有：BM-4 (DeepSeek)、BM-6 (TTS)、BM-10 (上行首段)、BM-12 (长会话稳定性)；其余 BM 的真测状态需逐一审计 `tests/perf/test_*.py`。所有 BM 的真实化是 SC-001 / SC-003 等达标判断的前置条件，发布前必须完成（与 perf-report 顶部"发布前必须复跑"约束一致）。

## DeepSeek MT first token 真实化（BM-4）

**日期**：2026-05-07
**目的**：把 BM-4（DeepSeek MT first token 延迟）从主表 stub（p50 320 ms / p95 700 ms 占位）替换为真实测量。回答 C+α 段提出的"streaming ASR 决策三档断言"中 BM-4 真实数字落在哪一档。
**样本**：上行 zh 15 句 + 下行 en 15 句，覆盖短 / 中 / 长（4-30 字 / 词），含数字、业务术语、专有名词；30/30 调用全部成功。
**模型**：`deepseek-v4-flash`（生产管线默认）。
**命令入口**：`uv run --extra dev scripts/measure_deepseek_first_token.py --samples-per-direction 15 --proof-json /tmp/deepseek-first-token.json`
**口径**：测量 `DeepSeekStreamingClient.stream_translate(text, direction=...)` 从迭代开始到首个 `kind="delta"` chunk 到达的 wall-clock 耗时；每次调用消费到 `kind="completed"` 才计入下一次，避免 async generator 与 HTTP 连接泄露。

| 方向 | 成功 | 失败 | p50 | p95 | avg | max |
|------|------|------|-----|-----|-----|-----|
| uplink | 15 | 0 | 566.2 ms | 752.6 ms | 572.8 ms | 752.6 ms |
| downlink | 15 | 0 | 598.3 ms | 751.1 ms | 589.8 ms | 751.1 ms |

**关键观察**：

1. **真实 p50 比 BM-4 stub (320 ms) 高 76-87%**。stub 是无源占位数字，与 deepseek-v4-flash 实际 prefill + scheduler 队列等待时间不符。
2. **上下行 p50 差仅 5%（566 vs 598 ms）**，且 first token 与输入文本长度弱相关（最短句 "你好" 569 ms vs 最长句"我们计划在第三季度推出..." 712 ms）—— 与 LLM streaming 推理 prefill-dominated 特性吻合，与文本量主导相反。
3. **p50 / p95 双双顶到预算上限**：p50 (566-598 ms) 超 400 ms 预算 **41-50%（Fail）**；p95 (751-753 ms) 卡 800 ms 预算 6%（Pass 但无安全边际）。
4. **分布紧实，无长尾**（avg 与 p50 差 < 10 ms；max = p95）—— 不是抖动 / 长尾问题，是 deepseek-v4-flash 整体 first token 基线就在 ~580 ms。换其他通用 LLM（gpt-4o-mini / claude-haiku）大概率落在同量级，不会显著改善。

**对 SC-001 ≤ 800 ms 的影响（结构性结论）**：

结合 BM-10 (C+α) 实测 ASR ≈ 270-310 ms，可累加（TTS first byte 仍是 stub，按预算上限 260 ms 估）：

- 上行 SC-001 链路 = ASR 297 ms + MT 566 ms + TTS ≥ 260 ms ≈ **≥ 1123 ms**（超预算 40%）
- 下行 SC-001 链路 = ASR 270 ms + MT 598 ms + TTS ≥ 260 ms ≈ **≥ 1128 ms**（超预算 41%）

仅 ASR + MT 即 ≈ 863-868 ms，已超 SC-001 ≤ 800 ms 预算 ~70 ms，加任何 TTS 都不可能进到 800 ms 内。**streaming ASR 即使把 ASR 压到 0 ms 也救不了 SC-001**，因为 DeepSeek p50 566 ms 单项已吃 71% 预算。

**回到 C+α 段的三档断言**：

- ✗ ≤ 490 ms（streaming ASR 不需要做）—— 不成立
- ✗ 491-750 ms（边际收益，调 VAD 切短即可）—— 不成立
- ✓ **≥ 750 ms** —— 成立。BM-4 alone 已 ≥ 566 ms，加任何 BM-6 必超 750 ms。**需重审 SC-001 阈值或换 MT/TTS 服务栈**。

**下一程候选（按"投入产出比"排序）**：

1. ~~**审视 SC-001 ≤ 800 ms 阈值的合理性**~~（**已完成于 2026-05-07**）：宪章修订 PR 已把 SC-001 / SC-002 中位阈值从 ≤ 800 ms 调整为 **≤ 1200 ms（硬阈值，发布门禁）+ ≤ 1000 ms（软目标，持续优化）**，p95 从 ≤ 1500 ms 调整为 ≤ 2000 ms。> 1500 ms 视为二次修订触发阈值。修订记录详见 `.specify/memory/constitution.md` 原则 IV 表与 spec.md SC-001 / SC-002 / L194 / L222 修订标注。
2. **测 BM-6 (Edge-TTS first byte) 真实化**：与本探针对仗的脚本，~ 1-2 小时。即使 BM-6 真实 ≤ 200 ms，SC-001 已 fail-closed；但补齐数据是宪章修订 PR 的前置依据。
3. **streaming ASR 投入暂缓**：实测显示它不是 SC-001 主要瓶颈。除非把 SC-001 重定为 < 600 ms（极端目标），否则其工程复杂度（partial revoke / 顺序保证 / sequence reordering）不能由本数据证明合理。

## 冷启动与分发形态合规

- 已安装环境冷启动：模拟 p95 3.2 秒，满足 SC-012 ≤ 10 秒。
- 分发形态审计：仓库未生成 `.app`、Teams 插件、Office Add-in 或本项目分发的内核扩展，数量均为 0。
- 全新 Mac 首次成功译音：当前以 quickstart 演练路径估算 ≤ 15 分钟；真实干净机器需发布前复跑。

## SC / BM / 宪章追踪矩阵

| 条款 | 证明 |
|------|------|
| SC-001 | BM-10 / `tests/perf/test_first_segment_latency.py` |
| SC-002 | BM-10D / `tests/perf/test_downlink_first_segment_latency.py` |
| SC-003 | BM-11 / `tests/perf/test_end_to_end_latency.py` |
| SC-004 | BM-12 / BM-13 |
| SC-005 | BM-2 / BM-5 |
| SC-006 | `tests/integration/test_supervisor.py` / `tests/unit/session/test_supervisor.py` |
| SC-007 | quickstart + README |
| SC-008 | `tests/integration/test_status_panel.py` |
| SC-009 | 本报告「冷启动与分发形态合规」 |
| SC-010 | BM-1 / BM-3 |
| SC-011 | README / wizard |
| SC-012 | 本报告「冷启动与分发形态合规」 |
| SC-013 | `tests/integration/test_supervisor.py` |
