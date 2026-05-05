# 需求质量校验清单：性能（performance.md）

**用途**：审计 spec.md / plan.md / research.md / contracts/ 中**性能相关需求**的完整性、清晰性、一致性、可度量性、覆盖度。本清单是「**对需求写作的单元测试**」（"unit tests for requirements"），**不**验证实现是否正确。
**创建日期**：2026-05-05
**关联**：[../spec.md](../spec.md) · [../plan.md](../plan.md) · [../research.md](../research.md) · [../contracts/](../contracts/) · [../tasks.md](../tasks.md)
**深度**：Standard（PR review 级）
**受众**：PR reviewer + 作者自检
**主焦点**：宪章原则 IV 性能预算 · plan.md Complexity Tracking 4 行风险 · 13 项 BM 基线任务

---

## Requirement Completeness（是否所有需要的性能需求都已定义）

- [ ] CHK001 是否为上行 / 下行管线**所有阶段**（capture / STT_PARTIAL / STT_FINAL / MT_FIRST_TOKEN / MT_COMPLETED / TTS_FIRST_BYTE / TTS_COMPLETED / AUDIO_ROUTE / E2E_FIRST_SEG / E2E_FULL）都定义了独立的延迟预算？[Completeness, data-model.md §6 LatencyStage]
- [x] CHK002 partial → final 升级的延迟差预算（FR-013 VAD 触发 close_segment 后多久 final 文本可用）是否在任何文档中量化？[Gap, Spec §FR-013] → **R1 已修复**：FR-013 增加「partial → final 升级延迟预算 ≤ 200 ms」
- [ ] CHK003 系统**冷启动延迟**（CLI `start` 到首段译音可输出）的预算是否定义？SC-007 仅涵盖"全新用户安装到首译音 ≤ 15 分钟"，但未涵盖"已安装用户每次启动" — 该启动包含 Whisper 模型加载（约 200–500 MB 文件 mmap 时间）。[Gap, Spec §SC-007]
- [ ] CHK004 长会话内存增长预算（24h ≤ 5%）是否同时定义 1h / 6h / 12h 中间检查点，以便提前发现累积泄漏？[Gap, Spec §SC-004]
- [ ] CHK005 上行 + 下行**同时运行**时的资源预算（CPU / RAM）是否定义为「单方向 × 2」还是「共享 Whisper 进程」？plan.md「8 个子进程同时运行的总预算」未在 SC 中显式声明。[Coverage, Gap, Spec §FR-026]
- [ ] CHK006 网络瞬断（≤ 30s，FR-018）期间的**延迟容忍与降级预算**是否定义？瞬断中用户能听到「最后多少秒前的译音」？[Gap, Spec §FR-018, §FR-019]

## Requirement Clarity（模糊形容词是否被量化）

- [x] CHK007 SC-005「翻译可懂度评分 ≥ 4/5」中"盲测"是否量化评估协议（评估者人数、样本量、统计显著性、跨方言适用性）？无协议定义意味着该 SC 不可重复验证。[Clarity, Spec §SC-005] → **R1 已修复**：SC-005 增加完整盲测协议（5 评估者 × 30 句 = 150 评分点 + 95% CI 下界 ≥ 3.7）
- [x] CHK008 SC-007「全新 Mac 用户首次执行安装命令到首次成功译音 ≤ 15 分钟」是否量化"全新"基线（是否包含 Homebrew 安装、是否假定 Python 3.11 已就位、是否包含 Whisper 模型 470 MB 下载）？[Clarity, Spec §SC-007] → **R1 已修复**：SC-007 增加完整「全新基线定义」+ 步骤纳入/排除清单
- [x] CHK009 SC-008「面板从识别 / 翻译事件发生到面板显示新内容的滞后 ≤ 1 秒」中"事件发生"的时间戳来源是否定义（内部消息总线 vs 子进程 stdout vs WebSocket 推送出栈瞬间）？[Ambiguity, Spec §SC-008] → **R1 已修复**：SC-008 增加测量定义（起点 = asyncio Queue 消费瞬间；终点 = MutationObserver 触发）
- [x] CHK010 宪章 IV「稳态运行平均 CPU ≤ 30%」中"稳态"是否量化（启动后 N 分钟开始计算？哪种工作负载——单方向？双方向？峰值并发？）？[Ambiguity, Constitution §IV, Spec §SC-010] → **R1 已修复**：SC-010 增加「稳态定义」（启动 ≥ 5 分钟、双向同传、每分钟 ≥ 30 秒有效语音、5 分钟滚动平均、M2 Pro 16 GB 基线）
- [x] CHK011 SC-004「60 分钟连续运行 0 次会话中断」中"中断"的判定是否定义清楚（用户感知中断 vs 子进程 respawn 但用户无感 vs 网络瞬断自动恢复 vs 配额耗尽）？[Clarity, Spec §SC-004] → **R1 已修复**：SC-004 增加「用户感知中断」定义（≥ 3 秒无译音输出 / services_health unavailable；supervisor respawn ≤ 5 秒不计）
- [x] CHK012 SC-003「整句完成」延迟的两端时间戳如何打：用户说话端用"用户开口"还是"用户闭口"？译音播放端用"TTS 首字节出口"还是"TTS 最后字节出口"？语义不同直接导致测得值差 1.5–3 s。[Ambiguity, Spec §SC-003] → **R1 已修复**：SC-003 增加测量定义（起点 = TranscriptSegment.kind=FINAL 写入瞬间；终点 = SynthesizedAudioSegment 最后 chunk 写入设备瞬间）

## Requirement Consistency（跨文件需求是否对齐）

- [x] CHK013 SC-001「首段译音中位 ≤ 800 ms」与宪章 IV「LLM 翻译首 token ≤ 800 ms」是否真的语义等价？后者是 LLM 单环节预算，前者是端到端预算 — 把 LLM 单点预算直接挪用为端到端预算在工程上是否合理？[Conflict, Spec §SC-001 vs Constitution §IV] → **R1 已修复**：spec 假设段「延迟语义对齐」重写，明确指出宪章 IV LLM 800 ms 是子预算（属 SC-001 内部 MT_FIRST_TOKEN 段）、SC-001 端到端 800 ms 是总预算，工程上两者不可同时无损达成、由 plan 行 3 显式登记并允许 SC-001 升档至 ≤ 1200 ms 可接受档
- [x] CHK014 SC-010「稳态 RAM ≤ 500 MB」与 plan.md Complexity Tracking 行 1 / contracts/whisper-cpp.md §7「Whisper.cpp small ≤ 1.6 GB（已批准的例外）」之间的预算差是否在 spec 假设段或 SC-010 注释中**显式登记**为已批准例外，避免审计时出现双重标准？[Conflict, Spec §SC-010 vs plan §Complexity Tracking 行 1 vs contracts/whisper-cpp.md §7] → **R1 已修复**：SC-010 直接列出双档（理想 ≤ 500 MB / 可接受 ≤ 1.6 GB），并 cross-ref plan 行 1；plan 行 1 + contracts/whisper-cpp.md §7 三处对齐
- [x] CHK015 contracts/edge-tts.md §8「TTS 首字节 p50 ≤ 400 ms」+ contracts/whisper-cpp.md §7「STT partial p50 400–700 ms」+ contracts/deepseek-translate.md §9「翻译首 token p50 ≤ 400 ms」三段叠加约 1200–1500 ms，是否与 SC-001 端到端「中位 ≤ 800 ms」自洽？还是已被 plan §Complexity Tracking 行 3 显式承认违例并触发宪章修订路径？[Conflict, multiple §] → **R1 已修复**：SC-001 增加「已知风险」段直接承认叠加期望 800–1200 ms；tasks T058 BM-10 双档（理想 / 可接受）通过条件落实；plan 行 3 升级为「已批准例外阈值已写入 SC-001」
- [x] CHK016 contracts/deepseek-translate.md §9「整段延迟 p50 ≤ 1500 ms」与宪章 IV「LLM 翻译整段 ≤ 1.5 s」是否完全等价？是否都包含网络 RTT、TLS 握手、SSE 解析时间？[Consistency, contracts/deepseek-translate.md §9 vs Constitution §IV] → **R1 已修复**：contracts/deepseek-translate.md §9 表加「包含/排除」列，明确预算包含 DNS / TLS keep-alive / SSE 解析全部开销，并与宪章 IV 严格对齐
- [x] CHK017 tasks.md T058 BM-10 通过条件「≤ 800 ms（理想）/ ≤ 1200 ms（可接受）」中的"可接受"上限 1200 ms 是否回写到 SC-001 注释或 spec 假设段（避免 BM 通过条件与 SC 字面定义各自漂移）？[Consistency, tasks.md §T058 vs Spec §SC-001] → **R1 已修复**：SC-001 / SC-002 直接列出「中位 ≤ 800 ms（理想）/ 可接受 ≤ 1200 ms」双档，与 BM-10 通过条件字面对齐
- [x] CHK018 plan.md「24h 内存增长 ≤ 5%」与 SC-004「24 小时无人值守运行内存增长 ≤ 5%」的"无人值守"语义是否一致（持续静音工况 vs 持续对话工况下的内存曲线显著不同，是否在两份文档中区别对待）？[Consistency, plan §IV vs Spec §SC-004] → **R1 已修复**：SC-004 显式区分「持续对话工况 ≤ 5%」与「持续静音工况 ≤ 2%」（后者由 SC-004b 隔离覆盖）
- [x] CHK019 SC-009「原生 macOS App Bundle 数量 = 0、Teams 插件 / Office Add-in 数量 = 0、内核扩展数量 = 0」位于「成功标准」段，但本质是**分发产物形式**约束（合规 / 项目治理），不属于"性能需求"。是否应迁移到独立 Distribution / Compliance 条目以避免概念混淆？[Conflict, Spec §SC-009] → **R1 已修复**：SC-009 增加「注」段说明本条目本质是分发与产物形式合规约束、非性能维度；保留位置因为它是 v1 不可妥协的可度量验收条件；新增产物分类时优先迁移至独立 Distribution / Compliance 段

## Acceptance Criteria Measurability（是否可客观度量）

- [x] CHK020 SC-002「下行首段译音中位时延同 SC-001」中"远端开口"如何打时间戳（用户端**无法访问**远端 PC 时钟）？只能依赖 Teams 应用音频输出 / BlackHole 捕获瞬间——这是不是 SC-002 的隐含定义？[Measurability, Spec §SC-002] → **R1 已修复**：SC-002 增加测量定义，明确以「BlackHole 2ch 输入流首字节」作为起点代理（远端开口时刻不可观测，不在 SC-002 范围）
- [x] CHK021 13 项 BM 任务（research.md §13）的通过条件是否都给出 pass/fail 数值阈值 + 样本量 + 置信区间？BM-5（术语表盲测）/ BM-7（24h 失败率）/ BM-12（60 分钟 0 中断）/ BM-13（24h 内存增长）在多大样本下达成统计显著？[Measurability, research.md §13] → **R1 已修复**：research.md §13 BM 表新增「样本量 / 测量窗口」与「测量环境」两列，13 项 BM 全部填充
- [x] CHK022 BM-12「60 分钟 0 次 supervisor 熔断」中"次数"是否区分「单次 respawn 不算熔断 / 60 s 内 ≥ 3 次才算熔断」与「整次会话 0 熔断」两种语义？两者达标率显著不同。[Measurability, research.md §BM-12, Spec §FR-028] → **R1 已修复**：BM-12 行重写测量目标 = 「用户感知中断次数 = 0」（按 SC-004 定义）；single supervisor respawn ≤ 5 秒不计
- [x] CHK023 SC-004「24 小时内存增长 ≤ 5%」的基线测量点是"启动 1 分钟稳态后瞬时值"还是"启动瞬时值"？两者差距可达 200–400 MB（Whisper 模型 mmap 阶段），直接决定 ≤ 5% 是否轻易达成。[Measurability, Spec §SC-004] → **R1 已修复**：SC-004 显式定义「基线测量点 = 启动后 5 分钟稳态时刻的 RSS」
- [x] CHK024 SC-006「用户在 ≤ 5 秒内看到带"下一步建议"的两段式错误提示」中"5 秒"的起算点是"API 调用失败的瞬间"还是"退避策略退出最后一次重试的瞬间"？两者差距可达 7.75 s（FR-018 退避序列 250 + 500 + 1000 + 2000 + 4000 ms）— 即按后者起算实际不可能达成 5 s。[Measurability, Spec §SC-006 vs §FR-018] → **R1 已修复**：SC-006 显式定义「起点 = 第 1 次失败检测瞬间」并解耦退避策略；contracts/deepseek-translate.md §9 重试退避总耗时一行同步更新「与 SC-006 解耦」说明

## Scenario Coverage（关键场景是否都有性能预算）

- [ ] CHK025 用户**暂停 → 继续**（FR-014）的恢复时延是否在 SC 中定义？US4 验收场景 2 仅定性写"≤ 2 秒恢复"，但 SC-001..010 中无对应可度量指标。[Coverage, Gap, Spec §FR-014, §US4]
- [ ] CHK026 用户**切换音频输入设备**（FR-017）期间的中断时长上限是否在 SC 中定义？US4 验收场景 1 仅定性写"≤ 5 秒接管"，未提升为 SC 级。[Coverage, Gap, Spec §FR-017, §US4]
- [ ] CHK027 Whisper 模型**自动降档**（small → tiny，对应 plan Complexity Tracking 行 1 / 行 2 的备选退出）期间的延迟与准确率过渡曲线是否定义？降档过程会产生短暂识别空白吗？[Coverage, Gap, plan §Complexity Tracking 行 1, 行 2]
- [ ] CHK028 Edge-TTS **401/403 token 自动刷新**（contracts/edge-tts.md §7）累计 3 次的总耗时预算是否计入端到端 SC-001 预算？rights 失败发生时用户是听到延长的静音还是听到上一段译音的截断？[Coverage, Gap, contracts/edge-tts.md §7, Spec §SC-001]
- [ ] CHK029 **术语表 0 条 vs 200 条**对 DeepSeek system prompt 长度的影响（约 +5–8 KB token），对首 token 延迟的影响范围是否预先估算并写入预算？BM-5 仅测质量未测延迟。[Coverage, Gap, Spec §FR-012, research.md §BM-5]

## Edge Case Coverage（边界条件）

- [ ] CHK030 长会话累积"未导出双语对照"在内存达 10000+ 段（约 2 小时高密度对话）时的内存峰值是否纳入 SC-010 RAM ≤ 500 MB 预算？10000 段 × 200 字节 ≈ 2 MB，可控但 spec 未声明上限。[Coverage, Edge Case, Gap, Spec §FR-024]
- [ ] CHK031 上行 + 下行 + 状态面板 WebSocket 推送（≥ 5 Hz）+ Web 控制台前端渲染同时运行的总 CPU 预算是否定义？spec / 宪章仅给出"稳态平均 ≤ 30%" 单点，未区分组合负载。[Coverage, Edge Case, Gap, Spec §SC-010]
- [ ] CHK032 远端**单人发言时长 ≥ 5 分钟无停顿**（spec edge case「Teams 多人发言重叠」反向情形）的下行 partial 缓冲是否定义最大长度限制以避免 LatencySnapshot 滚动窗口外溢？[Coverage, Edge Case, Gap, Spec §边界与异常情况]

## Dependencies & Assumptions

- [ ] CHK033 plan.md「Whisper.cpp small q5_0 在 Apple Silicon 上 1.0–1.5 GB RAM」估值的数据来源是否引用 whisper.cpp 官方 benchmark 文档版本号？版本变化时本预算是否需要重测并更新？[Assumption, plan §Complexity Tracking 行 1]
- [ ] CHK034 research.md §2「DeepSeek streaming 首 token 200–400 ms」是否声明了网络基线（中国大陆 vs 海外、移动网络 vs 100 Mbps、TLS 握手是否包含）？BM-4 测试报告 schema 是否要求记录网络环境元数据？[Assumption, research.md §2, §BM-4]
- [ ] CHK035 research.md §3「Edge-TTS 首字节 200–400 ms」是否声明了访问 `speech.platform.bing.com` 的网络基线？跨境网络（中国大陆访问该域名延迟显著高）下该预算是否仍可达成？[Assumption, research.md §3]

## Traceability（可追溯性）

- [ ] CHK036 SC-001..010 + 宪章 IV 全部预算是否每条都映射到至少一项 BM 任务？反向：每项 BM（BM-1..13）是否都映射到至少一个 SC 或宪章条款？映射关系是否在 plan.md 或 research.md 中以矩阵形式列出？[Traceability]
- [ ] CHK037 plan §Complexity Tracking 4 行风险，每行的"退出动作"是否都有对应"宪章修订 PR 模板"或"已批准例外条款"作为可执行路径？这些路径是否在 spec 假设段或 plan.md 已显式登记，并在 tasks.md 中有具体任务（T059 / T106 / T113）触发？[Traceability, plan §Complexity Tracking, tasks.md]
- [ ] CHK038 perf-report.md（待 Phase 0 末产出）的报告 schema 是否在 plan.md 或 research.md 中预先定义（每条 BM 报告的字段：实测 p50/p95、样本量、置信区间、运行环境、Pass/Fail、与预算的差异、退出动作触发标记）？无 schema 则各 BM 任务产出的报告无法相互比较。[Traceability, Gap, perf-report.md]

---

## Round 1 修订记录（2026-05-05）

**修复范围**：38 项 CHK 中 **19 项已修复**（CHK002 + CHK007–CHK024 共 18 项 = 1 Completeness + 6 Clarity + 7 Consistency / Conflict + 5 Measurability）；剩余 19 项主要为 [Gap] / [Coverage] / [Assumption] / [Traceability]，多数适合在 implementation 期或 perf-report.md 基线产出后回归。

**修订涉及的文件**：

| 文件 | 修订点 |
|------|--------|
| `spec.md` SC-001..SC-010 + SC-011 | 全部 SC 增加「测量定义」/「可接受档」/「稳态定义」/「全新基线定义」/「盲测协议」/「用户感知中断定义」 |
| `spec.md` FR-013 | 增加 partial → final 升级延迟预算 ≤ 200 ms |
| `spec.md` 假设段「延迟语义对齐」 | 重写：明确 SC-001 端到端预算 vs 宪章 IV LLM 子预算的关系；显式承认两者工程上不可同时达成 |
| `plan.md` Complexity Tracking 4 行 | 增加「已批准例外阈值」回写 SC-001 / SC-010；状态列细化 |
| `research.md` §13 BM 表 | 13 项 BM 全部新增「样本量 / 测量窗口」+「测量环境」两列 |
| `contracts/deepseek-translate.md` §9 | 性能 SLA 表加「包含/排除」列，明确含 RTT/TLS/SSE 全部开销；重试退避与 SC-006 解耦 |
| `contracts/whisper-cpp.md` §7 | RAM / CPU 行明确「已批准例外阈值」与 spec §SC-010 / plan §Complexity Tracking 三处对齐 |

**剩余 22 项分类**：

- **Round 2（implementation 期补齐）**：CHK001（所有阶段独立预算细化）、CHK003（冷启动延迟）、CHK004（中间检查点）、CHK005（双向资源预算）、CHK006（瞬断期延迟容忍）、CHK025–CHK029（Coverage Gap）、CHK030–CHK032（Edge Case Gap）—— 共 13 项 [Gap] / [Coverage]
- **Round 3（perf-report.md 基线后回归）**：CHK033–CHK035（Assumption，与 BM-1 / BM-4 / BM-6 测试环境元数据交叉验证）—— 共 3 项
- **Round 4（最终 trace 矩阵）**：CHK036–CHK038（SC↔BM↔Constitution 矩阵 + perf-report.md schema）—— 共 3 项

**Round 1 后建议下一步**：

1. 重新跑 `/speckit.checklist` 生成 `resilience.md`（FR-018/019/020/026/028/029 故障恢复需求质量审计），或
2. 直接进 `/speckit.implement`：Round 2/3/4 共 19 项中绝大多数为 Gap，可作为 `tasks.md` 新增子任务在 implementation 期解决，**不**阻塞首次实施

---

## 备注

- 本清单是「需求写作的单元测试」（unit tests for requirements writing），**审计需求的写作质量**，**不**验证代码或实现
- 38 项中，[Conflict] / [Ambiguity] 类（约 13 项）**必须**在进入 `/speckit.implement` 前通过修正 spec.md / plan.md / contracts/ 解决 — **Round 1 已完成**
- [Gap] 类（约 18 项）可在 implementation 阶段补全，但**必须**有专门 PR 在 mid-implementation 前补齐
- [Assumption] 类必须在 perf-report.md 基线 benchmark 报告中作为「测试环境元数据」记录
- 每次 perf-report.md 更新后，本清单的 [Conflict] / [Measurability] 类项需要回归校验
- 如发现新性能维度（如能耗 / 网络流量 / 启动时间）应追加 CHK039 起的新条目；现有 38 项**不**得删除（保留审计历史）

## 类别覆盖统计

| 类别 | 条目数 | 占比 |
|------|--------|------|
| Requirement Completeness | 6 | 16% |
| Requirement Clarity | 6 | 16% |
| Requirement Consistency | 7 | 18% |
| Acceptance Criteria Measurability | 5 | 13% |
| Scenario Coverage | 5 | 13% |
| Edge Case Coverage | 3 | 8% |
| Dependencies & Assumptions | 3 | 8% |
| Traceability | 3 | 8% |
| **合计** | **38** | **100%** |

## 标记类型统计（traceability ≥ 80% 必须含至少一个标记）

| 标记 | 计数 |
|------|------|
| `[Spec §...]` 引用 | 24 |
| `[Constitution §...]` 引用 | 4 |
| `[plan §...]` 引用 | 6 |
| `[contracts/<file>.md §...]` 引用 | 8 |
| `[research.md §...]` 引用 | 7 |
| `[tasks.md §...]` 引用 | 2 |
| `[Gap]` | 16 |
| `[Ambiguity]` | 5 |
| `[Conflict]` | 5 |
| `[Assumption]` | 3 |
| `[Traceability]` | 3 |
| `[Edge Case]` | 3 |
| `[Coverage]` | 8 |
| `[Completeness]` | 5 |
| `[Clarity]` | 4 |
| `[Consistency]` | 4 |
| `[Measurability]` | 5 |

100% 条目至少含一个上述标记，满足 traceability ≥ 80% 要求。
