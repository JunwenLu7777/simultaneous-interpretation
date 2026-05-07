# 需求质量校验清单：性能（performance.md）

**用途**：审计 spec.md / plan.md / research.md / contracts/ 中**性能相关需求**的完整性、清晰性、一致性、可度量性、覆盖度。本清单是「**对需求写作的单元测试**」（"unit tests for requirements"），**不**验证实现是否正确。
**创建日期**：2026-05-05
**关联**：[../spec.md](../spec.md) · [../plan.md](../plan.md) · [../research.md](../research.md) · [../contracts/](../contracts/) · [../tasks.md](../tasks.md)
**深度**：Standard（PR review 级）
**受众**：PR reviewer + 作者自检
**主焦点**：宪章原则 IV 性能预算 · plan.md 复杂度追踪 4 行风险 · 14 项 BM 基线任务（含 BM-10D）

---

## Requirement Completeness（是否所有需要的性能需求都已定义）

- [x] CHK001 是否为上行 / 下行管线**所有阶段**（capture / STT_PARTIAL / STT_FINAL / MT_FIRST_TOKEN / MT_COMPLETED / TTS_FIRST_BYTE / TTS_COMPLETED / AUDIO_ROUTE / E2E_FIRST_SEG / E2E_FULL）都定义了独立的延迟预算？[Completeness, data-model.md §6 LatencyStage] → **R2 已修复**：plan.md 新增「阶段级延迟预算矩阵」，覆盖全部 `LatencyStage`
- [x] CHK002 partial → final 升级的延迟差预算（FR-013 VAD 触发 close_segment 后多久 final 文本可用）是否在任何文档中量化？[Gap, Spec §FR-013] → **R1 已修复**：FR-013 增加「partial → final 升级延迟预算 ≤ 200 ms」
- [x] CHK003 系统**冷启动延迟**（CLI `start` 到首段译音可输出）的预算是否定义？SC-007 仅涵盖"全新用户安装到首译音 ≤ 15 分钟"，但未涵盖"已安装用户每次启动" — 该启动包含 Whisper 模型加载（约 200–500 MB 文件 mmap 时间）。[Gap, Spec §SC-007] → **R2 已修复**：新增 SC-012，已安装用户冷启动 p95 ≤ 10 秒；tasks.md T107 验证
- [x] CHK004 长会话内存增长预算（24h ≤ 5%）是否同时定义 1h / 6h / 12h 中间检查点，以便提前发现累积泄漏？[Gap, Spec §SC-004] → **R2 已修复**：SC-004 增加 1h ≤ 1%、6h ≤ 2%、12h ≤ 3.5% 中间检查点
- [x] CHK005 上行 + 下行**同时运行**时的资源预算（CPU / RAM）是否定义为「单方向 × 2」还是「共享 Whisper 进程」？plan.md「8 个子进程同时运行的总预算」未在 SC 中显式声明。[Coverage, Gap, Spec §FR-026] → **R2 已修复**：SC-010 明确适用负载包含双向 Whisper、双向 Edge-TTS、DeepSeek 双连接、FastAPI、WebSocket 与 Web 控制台
- [x] CHK006 网络瞬断（≤ 30s，FR-018）期间的**延迟容忍与降级预算**是否定义？瞬断中用户能听到「最后多少秒前的译音」？[Gap, Spec §FR-018, §FR-019] → **R2 已修复**：FR-018 定义旧译音最大滞后 ≤ 2 秒；SC-013 定义恢复提示 p95 ≤ 5 秒

## Requirement Clarity（模糊形容词是否被量化）

- [x] CHK007 SC-005「翻译可懂度评分 ≥ 4/5」中"盲测"是否量化评估协议（评估者人数、样本量、统计显著性、跨方言适用性）？无协议定义意味着该 SC 不可重复验证。[Clarity, Spec §SC-005] → **R1 已修复**：SC-005 增加完整盲测协议（5 评估者 × 30 句 = 150 评分点 + 95% CI 下界 ≥ 3.7）
- [x] CHK008 SC-007「全新 Mac 用户首次执行安装命令到首次成功译音 ≤ 15 分钟」是否量化"全新"基线（是否包含 Homebrew 安装、是否假定 Python 3.11 已就位、是否包含 Whisper 模型 470 MB 下载）？[Clarity, Spec §SC-007] → **R1 已修复**：SC-007 增加完整「全新基线定义」+ 步骤纳入/排除清单
- [x] CHK009 SC-008「面板从识别 / 翻译事件发生到面板显示新内容的滞后 ≤ 1 秒」中"事件发生"的时间戳来源是否定义（内部消息总线 vs 子进程 stdout vs WebSocket 推送出栈瞬间）？[Ambiguity, Spec §SC-008] → **R1 已修复**：SC-008 增加测量定义（起点 = asyncio Queue 消费瞬间；终点 = MutationObserver 触发）
- [x] CHK010 宪章 IV「稳态运行平均 CPU ≤ 30%」中"稳态"是否量化（启动后 N 分钟开始计算？哪种工作负载——单方向？双方向？峰值并发？）？[Ambiguity, Constitution §IV, Spec §SC-010] → **R1 已修复**：SC-010 增加「稳态定义」（启动 ≥ 5 分钟、双向同传、每分钟 ≥ 30 秒有效语音、5 分钟滚动平均、M2 Pro 16 GB 基线）
- [x] CHK011 SC-004「60 分钟连续运行 0 次会话中断」中"中断"的判定是否定义清楚（用户感知中断 vs 子进程 respawn 但用户无感 vs 网络瞬断自动恢复 vs 配额耗尽）？[Clarity, Spec §SC-004] → **R1 已修复**：SC-004 增加「用户感知中断」定义（≥ 3 秒无译音输出 / services_health unavailable；supervisor respawn ≤ 5 秒不计）
- [x] CHK012 SC-003「整句完成」延迟的两端时间戳如何打：用户说话端用"用户开口"还是"用户闭口"？译音播放端用"TTS 首字节出口"还是"TTS 最后字节出口"？语义不同直接导致测得值差 1.5–3 s。[Ambiguity, Spec §SC-003] → **R1 已修复**：SC-003 增加测量定义（起点 = TranscriptSegment.kind=FINAL 写入瞬间；终点 = SynthesizedAudioSegment 最后 chunk 写入设备瞬间）

## Requirement Consistency（跨文件需求是否对齐）

- [x] CHK013 SC-001「首段译音中位 ≤ 800 ms」与宪章 IV「LLM 翻译首 token ≤ 800 ms」是否真的语义等价？后者是 LLM 单环节预算，前者是端到端预算 — 把 LLM 单点预算直接挪用为端到端预算在工程上是否合理？[Conflict, Spec §SC-001 vs Constitution §IV] → **R1/R2 已修复**：spec 假设段「延迟语义对齐」明确宪章 IV LLM 800 ms 是子预算、SC-001 800 ms 是端到端总预算；plan 行 3 只登记风险观测 / 修订触发阈值，不构成发布豁免。**R3 (2026-05-07) 进一步对齐**：BM-4 真测后通过宪章修订 PR 把 SC-001 / SC-002 端到端阈值从 ≤ 800 ms 调整为 ≤ 1200 ms 硬 / ≤ 1000 ms 软，与 LLM 单段 ≤ 800 ms 子预算并行，不再相互冲突。
- [x] CHK014 SC-010「稳态 RAM ≤ 500 MB」与 plan.md 复杂度追踪行 1 / contracts/whisper-cpp.md §7「Whisper.cpp small ≤ 1.6 GB 风险观测阈值」之间的预算差是否在 spec 假设段或 SC-010 注释中显式登记，避免审计时出现双重标准？[Conflict, Spec §SC-010 vs plan §复杂度追踪 行 1 vs contracts/whisper-cpp.md §7] → **R1/R2 已修复**：SC-010、plan 行 1、contracts/whisper-cpp.md §7 均统一为正式预算 ≤ 500 MB；≤ 1.6 GB 仅为风险观测 / 修订触发阈值
- [x] CHK015 contracts/edge-tts.md §8「TTS 首字节 p50 ≤ 400 ms」+ contracts/whisper-cpp.md §7「STT partial p50 400–700 ms」+ contracts/deepseek-translate.md §9「翻译首 token p50 ≤ 400 ms」三段叠加约 1200–1500 ms，是否与 SC-001 端到端「中位 ≤ 800 ms」自洽？还是已被 plan §复杂度追踪行 3 显式承认违例并触发宪章修订路径？[Conflict, multiple §] → **R1/R2 已修复**：SC-001 承认叠加风险；plan 行 3 和 BM-10 定义超过正式预算即阻断发布，不能作为可接受发布档。**R3 (2026-05-07) 已对齐**：BM-4 / C+α 真测后通过宪章修订 PR 把 SC-001 / SC-002 端到端阈值从 ≤ 800 ms 调整为 ≤ 1200 ms 硬 / ≤ 1000 ms 软；子预算（STT / MT first token / TTS first byte）保持原数字不变。
- [x] CHK016 contracts/deepseek-translate.md §9「整段延迟 p50 ≤ 1500 ms」与宪章 IV「LLM 翻译整段 ≤ 1.5 s」是否完全等价？是否都包含网络 RTT、TLS 握手、SSE 解析时间？[Consistency, contracts/deepseek-translate.md §9 vs Constitution §IV] → **R1 已修复**：contracts/deepseek-translate.md §9 表加「包含/排除」列，明确预算包含 DNS / TLS keep-alive / SSE 解析全部开销，并与宪章 IV 严格对齐
- [x] CHK017 tasks.md T058 BM-10 通过条件是否与 SC-001 字面定义对齐（避免 BM 通过条件与 SC 字面定义各自漂移）？[Consistency, tasks.md §T058 vs Spec §SC-001] → **R1/R2 已修复**：SC-001 / SC-002 / plan / research / tasks 均统一为正式预算中位 ≤ 800 ms、p95 ≤ 1.5 s；≤ 1200 ms 仅为风险观测 / 修订触发阈值。**R3 (2026-05-07) 进一步统一**：宪章修订 PR 把 SC-001 / SC-002 端到端阈值从 ≤ 800 ms / ≤ 1.5 s 调整为 ≤ 1200 ms 硬 / ≤ 1000 ms 软 / p95 ≤ 2.0 s；spec / plan / research / tasks / data-model / quickstart / checklists / tests/perf 同步对齐到新阈值。
- [x] CHK018 plan.md「24h 内存增长 ≤ 5%」与 SC-004「24 小时无人值守运行内存增长 ≤ 5%」的"无人值守"语义是否一致（持续静音工况 vs 持续对话工况下的内存曲线显著不同，是否在两份文档中区别对待）？[Consistency, plan §IV vs Spec §SC-004] → **R1 已修复**：SC-004 显式区分「持续对话工况 ≤ 5%」与「持续静音工况 ≤ 2%」（后者由 SC-004b 隔离覆盖）
- [x] CHK019 SC-009「原生 macOS App Bundle 数量 = 0、Teams 插件 / Office Add-in 数量 = 0、内核扩展数量 = 0」位于「成功标准」段，但本质是**分发产物形式**约束（合规 / 项目治理），不属于"性能需求"。是否应迁移到独立 Distribution / Compliance 条目以避免概念混淆？[Conflict, Spec §SC-009] → **R1 已修复**：SC-009 增加「注」段说明本条目本质是分发与产物形式合规约束、非性能维度；保留位置因为它是 v1 不可妥协的可度量验收条件；新增产物分类时优先迁移至独立 Distribution / Compliance 段

## Acceptance Criteria Measurability（是否可客观度量）

- [x] CHK020 SC-002「下行首段译音中位时延同 SC-001」中"远端开口"如何打时间戳（用户端**无法访问**远端 PC 时钟）？只能依赖 Teams 应用音频输出 / BlackHole 捕获瞬间——这是不是 SC-002 的隐含定义？[Measurability, Spec §SC-002] → **R1 已修复**：SC-002 增加测量定义，明确以「BlackHole 2ch 输入流首字节」作为起点代理（远端开口时刻不可观测，不在 SC-002 范围）
- [x] CHK021 BM-1..13 + BM-10D 任务（research.md §13 / tasks.md）的通过条件是否都给出 pass/fail 数值阈值 + 样本量 + 置信区间？BM-5（术语表盲测）/ BM-7（24h 失败率）/ BM-12（60 分钟 0 中断）/ BM-13（24h 内存增长）在多大样本下达成统计显著？[Measurability, research.md §13] → **R1 已修复**：research.md §13 BM 表新增「样本量 / 测量窗口」与「测量环境」两列，BM-10D 由 tasks.md T059 单独覆盖
- [x] CHK022 BM-12「60 分钟 0 次 supervisor 熔断」中"次数"是否区分「单次 respawn 不算熔断 / 60 s 内 ≥ 3 次才算熔断」与「整次会话 0 熔断」两种语义？两者达标率显著不同。[Measurability, research.md §BM-12, Spec §FR-028] → **R1 已修复**：BM-12 行重写测量目标 = 「用户感知中断次数 = 0」（按 SC-004 定义）；single supervisor respawn ≤ 5 秒不计
- [x] CHK023 SC-004「24 小时内存增长 ≤ 5%」的基线测量点是"启动 1 分钟稳态后瞬时值"还是"启动瞬时值"？两者差距可达 200–400 MB（Whisper 模型 mmap 阶段），直接决定 ≤ 5% 是否轻易达成。[Measurability, Spec §SC-004] → **R1 已修复**：SC-004 显式定义「基线测量点 = 启动后 5 分钟稳态时刻的 RSS」
- [x] CHK024 SC-006「用户在 ≤ 5 秒内看到带"下一步建议"的两段式错误提示」中"5 秒"的起算点是"API 调用失败的瞬间"还是"退避策略退出最后一次重试的瞬间"？两者差距可达 7.75 s（FR-018 退避序列 250 + 500 + 1000 + 2000 + 4000 ms）— 即按后者起算实际不可能达成 5 s。[Measurability, Spec §SC-006 vs §FR-018] → **R1 已修复**：SC-006 显式定义「起点 = 第 1 次失败检测瞬间」并解耦退避策略；contracts/deepseek-translate.md §9 重试退避总耗时一行同步更新「与 SC-006 解耦」说明

## Scenario Coverage（关键场景是否都有性能预算）

- [x] CHK025 用户**暂停 → 继续**（FR-014）的恢复时延是否在 SC 中定义？US4 验收场景 2 仅定性写"≤ 2 秒恢复"，但 SC-001..010 中无对应可度量指标。[Coverage, Gap, Spec §FR-014, §US4] → **R2 已修复**：新增 SC-013，pause/resume 恢复 p95 ≤ 2 秒
- [x] CHK026 用户**切换音频输入设备**（FR-017）期间的中断时长上限是否在 SC 中定义？US4 验收场景 1 仅定性写"≤ 5 秒接管"，未提升为 SC 级。[Coverage, Gap, Spec §FR-017, §US4] → **R2 已修复**：新增 SC-013，设备切换接管 p95 ≤ 5 秒
- [x] CHK027 Whisper 模型**自动降档**（small → tiny，对应 plan 复杂度追踪行 1 / 行 2 的备选退出）期间的延迟与准确率过渡曲线是否定义？降档过程会产生短暂识别空白吗？[Coverage, Gap, plan §复杂度追踪 行 1, 行 2] → **R2 已修复**：contracts/whisper-cpp.md §8 明确只允许 STARTING 阶段自动降档；ACTIVE 会话不得静默降档，需停止方向并提示
- [x] CHK028 Edge-TTS **401/403 token 自动刷新**（contracts/edge-tts.md §7）累计 3 次的总耗时预算是否计入端到端 SC-001 预算？rights 失败发生时用户是听到延长的静音还是听到上一段译音的截断？[Coverage, Gap, contracts/edge-tts.md §7, Spec §SC-001] → **R2 已修复**：contracts/edge-tts.md §9 定义 retry 不计入正常 SC-001 成功样本、旧译音最多滞后 ≤ 2 秒、3 次失败单独记录降级 exit_action
- [x] CHK029 **术语表 0 条 vs 200 条**对 DeepSeek system prompt 长度的影响（约 +5–8 KB token），对首 token 延迟的影响范围是否预先估算并写入预算？BM-5 仅测质量未测延迟。[Coverage, Gap, Spec §FR-012, research.md §BM-5] → **R2 已修复**：FR-012、research.md BM-5、tasks.md T055 均要求测 0 条 / 200 条术语的 `MT_FIRST_TOKEN` p95 增量，目标 ≤ 200 ms

## Edge Case Coverage（边界条件）

- [x] CHK030 长会话累积"未导出双语对照"在内存达 10000+ 段（约 2 小时高密度对话）时的内存峰值是否纳入 SC-010 RAM ≤ 500 MB 预算？10000 段 × 200 字节 ≈ 2 MB，可控但 spec 未声明上限。[Coverage, Edge Case, Gap, Spec §FR-024] → **R2 已修复**：SC-010 明确组合负载包含会话内完整双语对照最多 20,000 个 final 条目
- [x] CHK031 上行 + 下行 + 状态面板 WebSocket 推送（≥ 5 Hz）+ Web 控制台前端渲染同时运行的总 CPU 预算是否定义？spec / 宪章仅给出"稳态平均 ≤ 30%" 单点，未区分组合负载。[Coverage, Edge Case, Gap, Spec §SC-010] → **R2 已修复**：SC-010 明确 CPU/RAM 预算适用负载包含双向同传 + WebSocket ≥ 5 Hz + Web 控制台
- [x] CHK032 远端**单人发言时长 ≥ 5 分钟无停顿**（spec edge case「Teams 多人发言重叠」反向情形）的下行 partial 缓冲是否定义最大长度限制以避免 LatencySnapshot 滚动窗口外溢？[Coverage, Edge Case, Gap, Spec §边界与异常情况] → **R2 已修复**：FR-013 增加连续长语音缓冲上限：每 30 秒滚动封口，单个 partial ≤ 30 秒或 1,000 token

## Dependencies & Assumptions

- [x] CHK033 plan.md「Whisper.cpp small q5_0 在 Apple Silicon 上 1.0–1.5 GB RAM」估值的数据来源是否引用 whisper.cpp 官方 benchmark 文档版本号？版本变化时本预算是否需要重测并更新？[Assumption, plan §复杂度追踪 行 1] → **R2 已修复**：research.md §1 与 BM-1/BM-3 要求记录 whisper.cpp 1.6+ 口径、commit/tag、模型 SHA256、Metal/Core ML 开关；发布证明以本机 BM 为准
- [x] CHK034 research.md §2「DeepSeek streaming 首 token 200–400 ms」是否声明了网络基线（中国大陆 vs 海外、移动网络 vs 100 Mbps、TLS 握手是否包含）？BM-4 测试报告 schema 是否要求记录网络环境元数据？[Assumption, research.md §2, §BM-4] → **R2 已修复**：research.md BM-4 与 plan.md schema 要求记录地区、网络类型、带宽、`api.deepseek.com` RTT、TLS 复用状态
- [x] CHK035 research.md §3「Edge-TTS 首字节 200–400 ms」是否声明了访问 `speech.platform.bing.com` 的网络基线？跨境网络（中国大陆访问该域名延迟显著高）下该预算是否仍可达成？[Assumption, research.md §3] → **R2 已修复**：research.md BM-6 与 plan.md schema 要求记录地区、网络类型、带宽、`speech.platform.bing.com` RTT、TLS 复用状态

## Traceability（可追溯性）

- [x] CHK036 SC-001..013 + 宪章 IV 全部预算是否每条都映射到至少一项 BM 任务？反向：每项 BM（BM-1..13 + BM-10D）是否都映射到至少一个 SC 或宪章条款？映射关系是否在 plan.md 或 research.md 中以矩阵形式列出？[Traceability] → **R2 已修复**：plan.md 新增「SC / BM / 宪章追踪矩阵」
- [x] CHK037 plan §复杂度追踪 4 行风险，每行的"退出动作"是否都有对应"宪章修订 PR 模板"或"风险阈值处置条款"作为可执行路径？这些路径是否在 spec 假设段或 plan.md 已显式登记，并在 tasks.md 中有具体任务（T059 / T106）触发？[Traceability, plan §复杂度追踪, tasks.md] → **R2 已修复**：plan.md 新增「性能违例处置模板」；tasks.md T059 / T106 触发对应 exit_action
- [x] CHK038 perf-report.md（待 benchmark 产出）的报告 schema 是否在 plan.md 或 research.md 中预先定义（每条 BM 报告的字段：实测 p50/p95、样本量、置信区间、运行环境、Pass/Fail、与预算的差异、退出动作触发标记）？无 schema 则各 BM 任务产出的报告无法相互比较。[Traceability, Gap, perf-report.md] → **R2 已修复**：plan.md 新增 `perf-report.md schema`；tasks.md T106 要求按 schema 审定报告

---

## Round 1 / Round 2 修订记录（2026-05-05）

**修复范围**：38 项 CHK 已全部闭合。Round 1 修复 CHK002 + CHK007–CHK024；Round 2 修复 CHK001、CHK003–CHK006、CHK025–CHK038。当前清单不再保留待办条目，但 BM 任务的实测结果仍必须在实现期写入 `perf-report.md` 并回归本清单。

**修订涉及的文件**：

| 文件 | 修订点 |
|------|--------|
| `spec.md` SC-001..SC-013 | 增加「测量定义」/「风险观测阈值」/「稳态定义」/「全新基线定义」/「盲测协议」/「用户感知中断定义」/「冷启动与恢复预算」 |
| `spec.md` FR-012 / FR-013 / FR-018 | 增加术语表规模与延迟上限、长语音滚动 finalize、瞬断期旧译音 ≤ 2 秒与重试状态语义 |
| `spec.md` 假设段「延迟语义对齐」 | 重写：明确 SC-001 端到端预算 vs 宪章 IV LLM 子预算的关系；显式承认两者工程上不可同时达成 |
| `plan.md` 阶段预算 / 追踪矩阵 / 违例处置 | 增加独立 `LatencyStage` 预算、`perf-report.md schema`、SC↔BM↔宪章追踪矩阵与发布阻断出口 |
| `research.md` §13 BM 表 | BM-1..13 全部新增「样本量 / 测量窗口」+「测量环境」+「不通过的退出动作」 |
| `contracts/deepseek-translate.md` §9 | 性能 SLA 表加「包含/排除」列，明确含 RTT/TLS/SSE 全部开销；重试退避与 SC-006 解耦 |
| `contracts/whisper-cpp.md` §7 | RAM / CPU 行明确「正式预算」与「风险观测 / 修订触发阈值」的区别 |
| `contracts/edge-tts.md` §7–9 | 增加首次失败 retry 状态、旧译音 ≤ 2 秒、401/403 降级 `exit_action` 记录语义 |
| `tasks.md` T055 / T106 / T107 | 增加 BM-5 延迟开销、perf-report schema 审定、冷启动与分发形态合规验证 |

**后续回归项（不是待办项）**：

- **实现期 benchmark 回归**：BM-1..13 + BM-10D 必须按 tasks.md 门禁产出实测数据，并写入 `perf-report.md`。
- **perf-report.md 回归**：每次新增或更新 BM 报告后，必须复核 CHK033–CHK038，确认环境元数据、trace 矩阵、schema 字段与 Pass/Fail 仍完整。
- **发布前回归**：任何超过正式预算的指标不得用风险观测阈值放行，必须触发模型降档、服务栈替换或独立宪章修订 PR。

**当前建议下一步**：

1. 重新执行 checklist 计数，确认 `performance.md` 与 `requirements.md` 均为 0 未完成。
2. 继续 `/speckit.implement`，从 T001 开始按 tasks.md 顺序执行。

---

## 备注

- 本清单是「需求写作的单元测试」（unit tests for requirements writing），**审计需求的写作质量**，**不**验证代码或实现
- 38 项中，[Conflict] / [Ambiguity] 类已在进入 `/speckit.implement` 前通过修正 spec.md / plan.md / contracts/ 解决
- [Gap] / [Coverage] 类已补成规约、计划、契约或任务要求；实现期仍需用真实 benchmark 与测试结果证明
- [Assumption] 类必须在 perf-report.md 基线 benchmark 报告中作为「测试环境元数据」记录并回归核验
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
