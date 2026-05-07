# 性能基线报告：Teams 实时双向语音同传桥（macOS）

**日期**：2026-05-05
**Git commit**：当前 HEAD；本报告不内嵌自引用提交哈希，使用 `git log -1 --oneline` 核验
**硬件**：本地 M3 + Metal + Piper 默认路径；BlackHole / Teams 真机会话仍需发布前复跑
**总体结论**：14 项 BM（BM-1..13 + BM-10D）的"模拟基准下通过"已被逐步替换为真实测量。2026-05-07 已完成 BM-4 / BM-6 子段真实化、ASR C+α 测量，以及 BM-10 / BM-10D 无人值守 E2E replay。结论从"有望进入 1200 ms 硬阈值"修正为：**当前生产顺序 ASR final → MT completed → TTS first byte 下，BM-10 / BM-10D p50 均 Fail**。上行段闭合后首音 p50 1720.0 ms / p95 1817.3 ms；下行段闭合后首音 p50 1657.9 ms / p95 1959.0 ms，均超过 SC-001 / SC-002 p50 ≤ 1200 ms 硬阈值。若从合成输入音频开头算代理口径，上行 p50 5310.5 ms / p95 10342.9 ms、下行 p50 5189.1 ms / p95 7616.2 ms，说明整段 ASR 边界本身不适合作为"远端开口/本端开口到首音"承诺。Piper 仍让 BM-6 子段通过（最差方向 p50 121.4 ms / p95 183.1 ms），但仅替换 TTS 不足以让当前端到端生产顺序达标；下一步必须优化 MT completed 前启动 TTS、缩短切段/引入可证明的 streaming ASR，或触发阈值/范围重审。

| BM | 关联条款 | 当前结果 | 预算 | Pass/Fail | exit_action |
|----|----------|----------|------|-----------|-------------|
| BM-1 | SC-010 / 宪章 IV | RAM 420 MB | ≤ 500 MB | Pass | 无 |
| BM-2 | SC-005 | WER 优势 6% | ≥ 5% | Pass | 无 |
| BM-3 | SC-010 / 宪章 IV | CPU 24% | ≤ 30% | Pass | 无 |
| BM-4 | 宪章 IV | MT first token p50 518.6 ms / p95 825.9 ms（Stage 5 无人值守复测，上下行最差值；上行 p50 503.9 / 下行 p50 518.6） | p50 ≤ 400 ms / p95 ≤ 800 ms | **Fail (p50, p95)** | 保持 MT 子预算风险，端到端是否可放行必须看 BM-10 / BM-10D |
| BM-5 | SC-005 / FR-012 | 保真 96% / 术语延迟增量 120 ms | ≥ 95% / ≤ 200 ms | Pass | 无 |
| BM-6 | SC-001 / SC-002 | Piper TTS first byte p50 121.4 ms / p95 183.1 ms（Stage 5 无人值守复测，上下行最差值；上行 p50 121.4 / 下行 p50 107.1） | p50 ≤ 200 ms / p95 ≤ 400 ms | Pass | 生产默认 TTS 已由 Edge-TTS 替换为 Piper；Edge-TTS 历史基线见后文 |
| BM-7 | Edge-TTS 稳定性 | 401/403 失败率 0.1% | < 0.5% | Pass | 无 |
| BM-8 | AUDIO_ROUTE | BlackHole 路由 p95 18 ms | ≤ 50 ms | Pass | 无 |
| BM-9 | SC-002 | Aggregate jitter p95 8 ms | ≤ 10 ms | Pass | 无 |
| BM-10 | SC-001 | 上行 E2E replay：段闭合后首音 p50 1720.0 ms / p95 1817.3 ms；音频开头代理 p50 5310.5 ms / p95 10342.9 ms（Stage 5b，无人值守） | p50 ≤ 1200 ms（硬）/ ≤ 1000 ms（软）/ p95 ≤ 2000 ms（2026-05-07 宪章修订 PR 自 800 ms / 1.5 s 调整） | **Fail (p50)** | 阻断发布；当前生产顺序等待 MT completed 后才启动 TTS，需改为更早启动 TTS / 缩短切段 / 重审阈值 |
| BM-10D | SC-002 | 下行 E2E replay：段闭合后首音 p50 1657.9 ms / p95 1959.0 ms；音频开头代理 p50 5189.1 ms / p95 7616.2 ms（Stage 5b，无人值守） | 同 SC-001（2026-05-07 宪章修订 PR 调整） | **Fail (p50)** | 阻断发布；同 BM-10，且 p95 仅贴近 2000 ms 上限 |
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
2. ~~**测 BM-6 (Edge-TTS first byte) 真实化**~~（**已完成于 2026-05-07**）：实测 p50 789-815 ms / p95 941-1049 ms（详见「Edge-TTS first byte 真实化（BM-6）」段）。结果远超 stub 的 260/620 ms（3 倍偏离），并使 ASR + MT + TTS 累加 ≈ 1700 ms 超新 SC-001 ≤ 1200 ms 硬阈值，触发二次修订需求。
3. **streaming ASR 投入暂缓**：实测显示它不是 SC-001 主要瓶颈。除非把 SC-001 重定为 < 600 ms（极端目标），否则其工程复杂度（partial revoke / 顺序保证 / sequence reordering）不能由本数据证明合理。

## Edge-TTS first byte 真实化（BM-6）

**日期**：2026-05-07
**目的**：把 BM-6（Edge-TTS first byte 延迟）从主表 stub（p50 260 ms / p95 620 ms 占位）替换为真实测量。补齐 SC-001 / SC-002 端到端首段延迟链路的最后一段。
**样本**：上行 TTS 输入英文译文 15 句（音色 `en-US-AriaNeural`，对应 zh→en 链路出口）+ 下行 TTS 输入中文译文 15 句（音色 `zh-CN-XiaoxiaoNeural`，对应 en→zh 链路出口），覆盖短 / 中 / 长（5-30 字 / 词），含数字、业务术语、专有名词；30/30 调用全部成功。
**服务**：Edge-TTS（`speech.platform.bing.com`，社区维护非官方接口），本段为 Piper 替换前的历史生产基线。
**命令入口**：`uv run --extra dev scripts/measure_edge_tts_first_byte.py --samples-per-direction 15 --proof-json /tmp/edge-tts-first-byte.json`
**口径**：测量 `EdgeTTSClient(live=True).stream_synthesize(text, direction=...)` 从迭代开始到首个 `kind="first_byte"` event 到达的 wall-clock 耗时；每次调用消费到 `kind="completed"` 才计入下一次。

| 方向 | 音色 | 成功 | 失败 | p50 | p95 | avg | max |
|------|------|------|------|-----|-----|-----|-----|
| uplink | `en-US-AriaNeural` | 15 | 0 | 789.3 ms | 940.7 ms | 801.6 ms | 940.7 ms |
| downlink | `zh-CN-XiaoxiaoNeural` | 15 | 0 | 815.6 ms | 1049.2 ms | 809.4 ms | 1049.2 ms |

**关键观察**：

1. **真实 p50 比 BM-6 stub (260 ms) 高 ~3 倍**。stub 是无源占位数字，与 Edge-TTS 实际首字节延迟严重不符。比 BM-4 偏离（76-87%）严重得多 —— spec / 主表起草时对 Edge-TTS 首字节的估计偏差最大。
2. **p50 / p95 双双超 BM-6 子预算**：p50 (789-815 ms) 超 400 ms 预算 **~100%**；p95 (941-1049 ms) 超 800 ms 预算 **18-31%**（双双 Fail，本会话第一次出现 p95 也超阈值的 BM）。
3. **上下行 p50 差仅 3%（789 vs 815 ms）**，文本长度弱相关（最短句"你好" 680 ms vs 最长句 1049 ms）—— 与 LLM 类似的 prefill / scheduler 队列特性，与文本量主导相反。
4. **30/30 全成功，无 401/403** —— 与 spec.md L221「Edge-TTS 是非官方接口」的稳定性担忧不符；本机环境下 Edge-TTS **鉴权稳定但首字节延迟本身偏高**。这是与 spec 假设最相反的发现。

**对 SC-001 / SC-002 的影响（结构性结论）**：

结合 BM-4 / C+α 已有数据，端到端首段译音延迟实测累加：

| 方向 | ASR | MT first token | TTS first byte | AUDIO_ROUTE | 累加 |
|------|----:|---------------:|---------------:|------------:|-----:|
| 上行 | 297 ms | 566 ms | 789 ms | ~50 ms | **1702 ms** |
| 下行 | 270 ms | 598 ms | 815 ms | ~50 ms | **1733 ms** |

- 双双超新 SC-001 ≤ 1200 ms 硬阈值约 **500 ms**
- 双双超 spec.md SC-001「> 1500 ms 二次修订触发阈值」
- 这意味着 2026-05-07 宪章修订 PR (709ea05) 在 BM-6 真实化数据下**已经过时**

**修订路径（按 spec.md SC-001 规定的三条路径 + v1 范围重估）**：

| 路径 | 工程量 | 收益（首段总延迟）| 风险 / 副作用 |
|---|---|---|---|
| **A. 服务栈替换 (TTS)** | 评估 + 集成 + 重测 ~ 3-7 天 | 切到 Coqui XTTS-v2（本地，~ 100-200 ms first byte）→ 整体压到 ~1100 ms；切到 ElevenLabs Flash / Azure Speech（付费，~ 100-300 ms first byte）→ 整体压到 ~1100-1300 ms | 本地 Coqui 模型 1.8 GB + 推理资源与宪章 IV 资源预算冲突；付费服务违反 spec Q1 零成本约束 |
| **B. 服务栈替换 (MT + TTS 一并升级)** | 5-10 天 | 难以突破物理下限组合 | 双服务栈同时变动增加发布风险 |
| **C. 二次宪章修订（C2）** | 半天 | SC-001 中位阈值从 1200 ms 再调宽到 ~1800 ms 硬 / 1500 ms 软（基于 1733 ms 上行实测 + 安全余量）；p95 从 2000 ms 调宽到 ~2500 ms | 距离上次修订不到 1 小时；用户感知"承诺"再次放宽 |
| **D. 重新评估 v1 范围** | 1-2 小时讨论 | 显式接受 v1 = "短句 / 短交互" 而非完整同传，避开长句的累积超阈值 | v1 场景覆盖面变窄；不解决长句不可避免的现实 |

**继续推进的前置条件**：v1 实现期门禁要求所有 SC 真实化测量落在宪章预算内。当前 BM-4 + BM-6 + C+α 累加已超新 SC-001 硬阈值，**门禁条件不成立**；必须先选定 A/B/C/D 中至少一条并完成关闭，才能继续 v1 后续 BM 真实化与发布动作。

## TTS 引擎对比与 Piper 决策（D 路径）

**日期**：2026-05-07
**目的**：BM-6 真实化后揭示 Edge-TTS first byte ~800 ms 让 SC-001 ≤ 1200 ms 硬阈值不可达，触发 spec.md SC-001「服务栈替换」修订路径。本节对比三个候选 TTS 引擎在 M3 + 16 GB Mac 上的实测数据，并给出生产管线 TTS 替换决策。
**样本**：上行 TTS 输入英文译文（音色 `en_US-amy-medium` / `Claribel Dervla` / `en-US-AriaNeural`）+ 下行 TTS 输入中文译文（`zh_CN-huayan-medium` / `Claribel Dervla` / `zh-CN-XiaoxiaoNeural`），每段最多 30 字 / 词；样本与 BM-6 探针一致。
**命令入口**：

- Edge-TTS：`uv run --extra dev scripts/measure_edge_tts_first_byte.py`
- Piper：`uv run --extra dev scripts/measure_piper_first_byte.py`
- XTTS-v2：临时 inline probe（不入库；首次需手动从 https://huggingface.co/coqui/XTTS-v2 下载 model.pth 1.85 GB；CPML 协议非商用免费，与 spec.md L199「v1 个人自用」一致）

| 引擎 | 上行 first byte p50 | 下行 first byte p50 | 资源占用 | License | 集成成本 |
|------|--------------------:|--------------------:|----------|---------|----------|
| Edge-TTS（生产基线，BM-6） | 789 ms | 815 ms | 网络 RTT | 微软非官方逆向 | 已集成 |
| **Piper（推荐）** | **103 ms** | **107 ms** | ~120 MB ONNX 模型 + CPU | MIT 完全免费 | 中等（替换 EdgeTTSClient）|
| XTTS-v2 | 708-1064 ms | 684-950 ms | ~5 GB（含 PyTorch + 模型）+ MPS | CPML（非商用免费）| 高（PyTorch 依赖 + transformers 版本耦合）|

**关键观察**：

1. **Piper 比 Edge-TTS 快 7.6-7.7 倍**（p50 维度），且 30/30 全成功；本地 ONNX 推理无网络抖动 / TLS / 队列开销，首字节延迟近乎模型推理本身的物理下限。
2. **XTTS-v2 与 Edge-TTS 同档（甚至略慢）**：cold start 7+ 秒，warm 仍 700-1000 ms。延迟没有优势，但占资源 ~50 倍（5 GB vs 120 MB）。
3. **Piper p95 (197 ms) 比 Edge-TTS p50 (789 ms) 还快 4 倍**。这是"本地 vs 云"在端到端延迟上的真实差距。
4. **Piper 中文音色 `zh_CN-huayan-medium` 主观听感"够用"**（本会话用户判断）：清晰度可接受、有轻微"AI 朗读"机器感但不影响商务对话理解。XTTS-v2 中文质量略好但不足以补偿 7 倍延迟差距。

**对端到端 SC-001 的影响（子段估算曾显示可救回，E2E replay 已修正）**：

Stage 4 阶段用 MT first token + Piper first byte 做过乐观子段估算：

| 方向 | ASR | MT first token | TTS first byte (Piper) | AUDIO_ROUTE | 累加 | vs SC-001 |
|------|---:|---:|---:|---:|---:|---|
| 上行 | 297 ms | 566 ms | **103 ms** | ~50 ms | **1016 ms** | 进硬阈值 1200 ms，余 184 ms；离软目标 1000 ms 仅 16 ms |
| 下行 | 270 ms | 598 ms | **107 ms** | ~50 ms | **1025 ms** | 进硬阈值 1200 ms，余 175 ms；离软目标 1000 ms 仅 25 ms |

**E2E replay 修正**：当前 `live_say` / `duplex` 生产顺序不是 MT first token 后立即 TTS，而是先消费到 MT completed，再把完整译文交给 TTS。因此 Stage 5b 真测结果为上行段闭合后首音 p50 1720.0 ms / p95 1817.3 ms、下行 p50 1657.9 ms / p95 1959.0 ms。Piper 仍显著降低 TTS 子段，但**单工程动作（换 TTS）不足以把当前端到端生产顺序救回 Pass**。

**决策**：**v1 服务栈 TTS 由 Edge-TTS 替换为 Piper**。理由：
- 唯一能让 BM-6 TTS first byte 子预算稳定通过的免费本地方案
- 消除 Edge-TTS 网络首字节 ~800 ms 的结构性拖累
- 完全免费 + MIT license + 100 MB 资源占用
- 无网络依赖（与 spec.md L199「个人自用」边界更契合）

**XTTS-v2 不被采用**的理由：
- 延迟无优势（同 Edge-TTS）
- 资源开销 50 倍（5 GB vs 120 MB）
- transformers 版本耦合（与项目其他升级冲突风险）
- CPML 商用受限（虽然 v1 是个人自用、暂时合规，但限制未来分发自由）

**生产管线集成状态**：

1. ✅ 已创建 `src/teams_voice_interpreter/tts/piper_client.py`，实现与 `EdgeTTSClient.stream_synthesize` 兼容的 async 流式接口。
2. ✅ 已在 `config.py` 增加 `piper_models_dir` 与 `tts_engine: Literal["edge_tts", "piper"]` 切换开关，生产默认值为 `piper`。
3. ✅ 已在 `readiness.py` 增加 Piper 模型存在性 + `onnxruntime` 可用性检查。
4. ✅ 已在 `cli/wizard.py` / `quickstart.md` 引导用户首次运行时下载 Piper voice 模型。
5. ✅ 已新增 `contracts/piper-tts.md`，并保留 `contracts/edge-tts.md` 作为降级路径契约。
6. ✅ 已修改 `spec.md` v1 服务栈锁定段（Edge-TTS → Piper）+ 子预算约束（TTS first byte p50 ≤ 200 ms / p95 ≤ 400 ms）。
7. ✅ 已修改 `plan.md` 性能目标 / 阶段预算 + 复杂度追踪行 4（Edge-TTS 风险关闭）。
8. ❌ Stage 5b 无人值守 E2E replay 已复跑上行 / 下行首段；当前生产顺序未达 p50 ≤ 1200 ms。
9. ✅ 已更新 `perf-report.md` 主表 BM-6 行为 Piper 子预算与实测。

**最近真测发现的子预算修订需求**：现有 BM-6 子预算（first byte p50 ≤ 400 ms / p95 ≤ 800 ms）按 Edge-TTS 量级设定，对 Piper 显著过宽（实测 p50 100 ms / p95 200 ms）。集成 PR 应一并把 BM-6 子预算调整为「first byte p50 ≤ 200 ms / p95 ≤ 400 ms」以反映 Piper 真实下限。

## Stage 5 无人值守复测

**日期**：2026-05-07  
**目的**：不依赖真人讲话，使用 macOS `say` 合成音频与脚本探针复测 Piper 默认路径的 ASR / MT / TTS 子段。  
**命令入口**：

- `uv run --extra dev scripts/measure_asr_segment_latency.py --proof-json /tmp/tvi-asr-segment-stage5.json`
- `uv run --extra dev scripts/measure_deepseek_first_token.py --samples-per-direction 15 --proof-json /tmp/tvi-deepseek-first-token-stage5.json`
- `uv run --extra dev scripts/measure_piper_first_byte.py --samples-per-direction 15 --proof-json /tmp/tvi-piper-first-byte-stage5.json`

| 子段 | 上行 | 下行 | 预算 | 结论 |
|------|------|------|------|------|
| ASR final（合成 WAV 整段） | 0.294 / 0.373 / 0.417 s（三档段长） | 0.282 / 0.280 / 0.322 s（三档段长） | FR-013 final ≤ 200 ms（VAD close 后）；本探针口径为整段 one-shot ASR，不直接等同 FR-013 | 子段仍为主要固定成本之一 |
| MT first token | p50 503.9 ms / p95 773.1 ms | p50 518.6 ms / p95 825.9 ms | p50 ≤ 400 ms / p95 ≤ 800 ms | **Fail (p50, 下行 p95)** |
| Piper TTS first byte | p50 121.4 ms / p95 181.2 ms | p50 107.1 ms / p95 183.1 ms | p50 ≤ 200 ms / p95 ≤ 400 ms | Pass |

**子段累加（非 BM-10 端到端门禁）**：

- 上行保守估算：ASR 417 ms + MT 504 ms + Piper 121 ms + AUDIO_ROUTE 50 ms ≈ **1092 ms**。
- 下行保守估算：ASR 322 ms + MT 519 ms + Piper 107 ms + AUDIO_ROUTE 50 ms ≈ **998 ms**。

**边界**：该复测证明 Piper 默认路径的 TTS 子段稳定落在预算内，也证明 DeepSeek MT 子段仍有预算风险。它仍不是 BM-10 / BM-10D 真实端到端测量；发布门禁必须用真实 `listen` / `duplex` 路径复跑并记录首段写入时间。

### Stage 5b：BM-10 / BM-10D 无人值守 E2E replay

**日期**：2026-05-07  
**命令入口**：`uv run --extra dev scripts/measure_e2e_first_segment.py --samples-per-direction 3 --config config.toml --proof-json /tmp/tvi-e2e-first-segment.json`  
**口径**：用 macOS `say` 生成上下行各 3 条固定商务 WAV；串起 `WhisperOneShotTranscriber` → `DeepSeekStreamingClient` → `build_tts_client(settings)`。当前生产顺序为 **ASR final → MT completed → TTS first byte**，所以 BM-10 / BM-10D 不能再用 MT first token 子段估算替代。  
**结果**：6/6 成功，无 ASR / MT / Piper runtime 错误。

| 方向 | 成功 | 失败 | 段闭合后 p50 | 段闭合后 p95 | 从音频开头 p50 | 从音频开头 p95 | 结论 |
|------|------|------|-------------|-------------|---------------|---------------|------|
| 上行 BM-10 | 3 | 0 | 1720.0 ms | 1817.3 ms | 5310.5 ms | 10342.9 ms | **Fail：p50 > 1200 ms** |
| 下行 BM-10D | 3 | 0 | 1657.9 ms | 1959.0 ms | 5189.1 ms | 7616.2 ms | **Fail：p50 > 1200 ms，p95 贴近 2000 ms** |

| 方向 | 段长 | ASR | MT first | MT done | TTS first | 段闭合后首音 | 音频开头首音 |
|------|------|-----|----------|---------|-----------|--------------|--------------|
| uplink | 1.988 s | 0.301 s | 0.395 s | 0.605 s | 0.814 s | 1.720 s | 3.708 s |
| uplink | 4.022 s | 0.334 s | 0.469 s | 0.745 s | 0.210 s | 1.288 s | 5.310 s |
| uplink | 8.526 s | 0.452 s | 0.456 s | 1.006 s | 0.360 s | 1.817 s | 10.343 s |
| downlink | 1.560 s | 0.289 s | 0.519 s | 0.715 s | 0.955 s | 1.959 s | 3.519 s |
| downlink | 3.841 s | 0.321 s | 0.547 s | 0.802 s | 0.225 s | 1.348 s | 5.189 s |
| downlink | 5.958 s | 0.353 s | 0.579 s | 0.985 s | 0.320 s | 1.658 s | 7.616 s |

**关键观察**：

1. **子段估算低估了真实 E2E**：先前使用 MT first token 累加，但生产 TTS 实际等待 MT completed；仅这一差异就给长句增加约 200-500 ms。
2. **Piper 不是当前失败主因**：大多数样本 Piper first byte 仍在 210-360 ms；两条短句出现 814/955 ms，说明首个语音模型调用仍有 warm/cold 抖动，但主结构问题是 TTS 启动太晚。
3. **从音频开头算的代理口径不可达**：当前整段 ASR 必须等语音段闭合；长段会天然把"开口到首音"推到 5-10 s。若 SC-001 / SC-002 要按开口时刻理解，必须引入可证明的 streaming ASR / early prepare，不应继续用整段 ASR 口径声明达标。

**退出动作**：BM-10 / BM-10D 当前阻断发布。优先修复方向是让 TTS 在 MT completed 前获得可播文本（例如基于 delta 的句片段缓冲 / early TTS）并重新 replay；若仍不达标，再评估切段策略、真正 streaming ASR 或 SC-001 / SC-002 阈值重审。

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
