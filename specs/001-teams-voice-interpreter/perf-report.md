# 性能基线报告：Teams 实时双向语音同传桥（macOS）

**日期**：2026-05-05
**Git commit**：当前 HEAD；本报告不内嵌自引用提交哈希，使用 `git log -1 --oneline` 核验
**硬件**：本地模拟基准；真实 BlackHole / Whisper 模型 / Edge-TTS 外网基准待发布前复跑
**总体结论**：14 项 BM（BM-1..13 + BM-10D）均有可执行测试入口并在当前模拟基准下通过。当前报告用于实现期门禁闭合；发布前必须在真实 M2 Pro 16 GB / macOS 13+ / Wi-Fi ≥ 50 Mbps 环境复跑并替换模拟数据。`--online-asr` 实验路径不计入下表通过项；2026-05-06 上行与 2026-05-07 下行本机探针均显示该路径尚未产生可交付的低延迟收益，详见「Online ASR 实验探针」。

| BM | 关联条款 | 当前结果 | 预算 | Pass/Fail | exit_action |
|----|----------|----------|------|-----------|-------------|
| BM-1 | SC-010 / 宪章 IV | RAM 420 MB | ≤ 500 MB | Pass | 无 |
| BM-2 | SC-005 | WER 优势 6% | ≥ 5% | Pass | 无 |
| BM-3 | SC-010 / 宪章 IV | CPU 24% | ≤ 30% | Pass | 无 |
| BM-4 | 宪章 IV | MT first token p50 320 ms / p95 700 ms | p50 ≤ 400 ms / p95 ≤ 800 ms | Pass | 无 |
| BM-5 | SC-005 / FR-012 | 保真 96% / 术语延迟增量 120 ms | ≥ 95% / ≤ 200 ms | Pass | 无 |
| BM-6 | SC-001 / SC-002 | TTS first byte p50 260 ms / p95 620 ms | p50 ≤ 400 ms / p95 ≤ 800 ms | Pass | 无 |
| BM-7 | Edge-TTS 稳定性 | 401/403 失败率 0.1% | < 0.5% | Pass | 无 |
| BM-8 | AUDIO_ROUTE | BlackHole 路由 p95 18 ms | ≤ 50 ms | Pass | 无 |
| BM-9 | SC-002 | Aggregate jitter p95 8 ms | ≤ 10 ms | Pass | 无 |
| BM-10 | SC-001 | 上行首段 p50 600 ms / p95 1100 ms | p50 ≤ 800 ms / p95 ≤ 1.5 s | Pass | 无 |
| BM-10D | SC-002 | 下行首段 p50 700 ms | p50 ≤ 800 ms / p95 ≤ 1.5 s | Pass | 无 |
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
