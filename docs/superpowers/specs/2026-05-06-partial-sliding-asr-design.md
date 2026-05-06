# Partial / Sliding ASR 设计文档

**日期**：2026-05-06
**关联**：spec.md FR-007 / FR-008 / FR-013、`.specify/memory/constitution.md` 原则 IV、`specs/001-teams-voice-interpreter/contracts/whisper-cpp.md` §4-6
**状态**：⛔ **暂缓（2026-05-06）** — 详见 §10 暂缓决策；spec FR-007 违约登记保留至 v1.1 重启
**取代路径**：升级 VAD（Silero）+ Bag of Hallucinations 后过滤 + 真测 baseline 写入 perf-report.md（详见 §10）

## 1. 背景与目标

### 1.1 现状

`tvi duplex` 实时管线当前为「VAD 段批量 one-shot ASR」：

- `cli/app.py:_run_listen_pipeline` 把 `StreamingMicrophoneRecorder.segments()` 的产物（VAD 闭段后的整段 PCM）送 `WhisperOneShotTranscriber.transcribe()`，调用 `pywhispercpp.Model.transcribe(audio)` 全段一次性识别
- `stt/whisper_streaming.py` 的 `WhisperStreamingWrapper` 是返回硬编码字符串的 stub，仅模拟管线 / 单测使用
- `live_say.py:LiveSayBridge.prepare` 把整段 source 一次性送 DeepSeek、收完整段译文再启 Edge-TTS

5 轮真钉钉通话 (`tvi duplex --show-latency` 27 段 p50)：

- ASR 2.26 s / MT 首 T 0.49 s / 首字节 1.78 s
- 用户感知首字节 ≈ VAD 0.3 + ASR 2.3 + 首字节 1.8 ≈ 4.4 s p50
- 距 spec SC-001 800 ms p50 / 1.5 s p95 仍 4-5×

### 1.2 已登记宪章违约

- spec FR-007 **必须**采用流式 STT 接口、持续输出 partial 与 final 片段、**不得**等到整句结束才提交识别结果 — 当前实现违约
- `perf-report.md` BM-10 的 600 ms p50 是 `tests/perf/test_first_segment_latency.py` 硬编码 fixture，与真测分布无关
- `contracts/whisper-cpp.md` §4-6 已设计 `WhisperStreamingClient.feed_audio / consume / close_segment` 接口与 stdin/stdout JSON Lines 协议，但实现为 stub

### 1.3 本设计的目标

- **必须**实现 spec FR-007 流式 partial+final 链路，回填宪章违约
- **应当**把上行 / 下行用户感知首字节 p95 从 ~3 s 拉到 ≤ 1.5 s — **仅承诺带停顿讲话场景**（场景 A）；用户讲长句不停顿（场景 B）退化到 VAD final 模式，接近现状
- **不应当**承诺 SC-001 800 ms p50 — spec §222 已承认此目标在物理下限上不可达；本设计仍受其约束但作为风险观测阈值
- **应当**把 BM-10 / BM-10D / BM-1 / BM-3 等性能 fixture 替换为真链路测量

## 2. 设计选项与决策

### 2.1 阶段化策略

| 选项 | 决策 |
|---|---|
| **A. 一次性完整流式**（ASR + MT + TTS partial 链路单 PR） | **选用** |
| B. 阶段 1 ASR partial 接 UI、TTS 仍走 final | 拒绝：单 PR 切两次链路代价更大 |
| C. 跳过 ASR partial UI，直接做 MT/TTS sentence-boundary | 拒绝：无 ASR partial 的真测数据，下游设计是赌博 |

### 2.2 commit 策略

| 选项 | 决策 |
|---|---|
| 激进（每个 partial 改写就重发 MT+TTS） | 拒绝：对端听到「Today...Today let's...」断续重复，会议场景不可用 |
| **折中（稳定前缀 + 自然边界 commit）** | **选用** |
| 保守（partial 仅显示，TTS 仍等 final） | 拒绝：违反 FR-007 实质（partial 不接入 MT/TTS 链路就不算流式） |

### 2.3 ASR 后端选型

| 选项 | 决策 |
|---|---|
| **a. whisper.cpp 自带 `stream` 工具 + subprocess + JSON Lines** | **选用** |
| b. faster-whisper int8 + 自写滑窗 | 拒绝：引入 CTranslate2 / PyTorch ~500 MB 增量依赖、与现有 ggml 模型缓存不兼容、Apple Silicon Metal 后端需自行编译 |
| c. pywhispercpp 重复 one-shot + 累积音频 | 拒绝：无 KV cache 复用、每 step 全量推理 CPU 必爆、违反 BM-3 ≤ 30 % |

理由：

- `contracts/whisper-cpp.md` §4-6 已完成接口设计，spec 期望路径
- whisper.cpp `stream` 在 subprocess 内长期持有 KV cache，CPU 比反复 one-shot 低一个数量级
- 不破坏现有 pywhispercpp 路径（保留 `tvi ptt` / `WhisperOneShotTranscriber` 不变）

### 2.4 MT commit 策略

| 选项 | 决策 |
|---|---|
| 1. 每次 commit 重发整段 source | 拒绝：每次付 ~500 字 system prompt prefill ≈ 0.5 s 首 token，3 次 commit 总 MT 1.5 s+，抵消 ASR partial 收益 |
| 2. 增量 commit + DeepSeek conversation history | 拒绝：DeepSeek 公开 API 是 stateless chat completions，conversation 维持要靠 messages 数组重发；与 1 等价 |
| **3. 等够长再 commit + sentence-boundary 切片，每个 commit 独立翻译** | **选用** |

附加：实测 DeepSeek 是否有 prefix caching（5 次相同 system prompt + 不同 user message，看 first-token p50 是否下降）；如果命中率 ≥ 50% 再考虑切到 2。

## 3. 架构总览

```
StreamingMicRecorder (30ms PCM frames)
        │
        ▼
VadSegmenter（仅触发 final，不再控 segment 切分）
        │ 30ms PCM + VAD signal
        ▼
WhisperStreamingClient（whisper.cpp `stream` subprocess + JSON Lines）
   feed_audio / consume / close_segment（contracts/whisper-cpp.md §4-6）
        │ STTEvent stream { kind=PARTIAL/FINAL, segment_id, text, conf }
        ▼
StablePrefixCommitter（新建 stt/committer.py）
   - LocalAgreement-2 算法（连续 2 个 partial 相同前缀视为稳定）
   - sentence-boundary 检测（。！？，：；.!?,:;）
   - idle_commit_ms 静默兜底 1500 ms
   - max_pending_chars 上限 60（讲长句不停顿时退化到 VAD final）
        │ CommitChunk { source_text, is_final, segment_id, commit_index }
        ▼
DeepSeekStreamingClient.translate_commit（改造 mt/deepseek_client.py）
   - 每个 commit 独立翻译，无 conversation state
   - 现有 stream_translate 保留（tvi say 一次性场景）
   - 新打点 prefix_cached（first-token p50 实测 DeepSeek 是否有 prefix caching）
        │ TranslationChunk stream
        ▼
EdgeTTSClient（已有）→ MP3 → PCM
        │
        ▼
PlaybackWorker（改造 cli/app.py）：commit 级队列 + PCM 顺序拼接
```

VAD 角色降级 — 不再控 segment 切分边界，仅作「用户讲完了」信号触发 `close_segment` JSON Lines 命令；端到端切段权交给 Whisper subprocess。

`LiveSayBridge.prepare` 实时分支替换为 `CommitOrchestrator`，按 commit 增量送下游；`tvi say` 同步路径继续走 `LiveSayBridge.say` 不动（保留现有契约）。

## 4. 组件拆分与接口

### 4.1 `WhisperStreamingClient`（改造 stub `stt/whisper_streaming.py`）

```python
class WhisperStreamingClient:
    def __init__(
        self,
        model_path: Path,
        language: Literal["zh", "en"],
        sample_rate_hz: int = 16000,
        chunk_size_ms: int = 30,
        step_size_ms: int = 300,
        context_size_sec: int = 5,
        metal_enabled: bool = True,
        core_ml_encoder: bool = True,
    ): ...

    async def feed_audio(self, pcm16_bytes: bytes) -> None: ...
    async def consume(self) -> AsyncIterator[STTEvent]: ...
    async def close_segment(self) -> None: ...
    async def shutdown(self) -> None: ...
```

- 子进程：whisper.cpp `stream` binary（不在 pywhispercpp 包内 — 见 §4.7）
- stdin/stdout JSON Lines（`contracts/whisper-cpp.md` §5）
- heartbeat ≥ 1 Hz；3 秒未到由 supervisor 重启
- 现有 `WhisperOneShotTranscriber` 保留供 `tvi ptt` / 测试 fixture 使用

### 4.2 `StablePrefixCommitter`（新建 `stt/committer.py`）

```python
@dataclass(frozen=True)
class CommitChunk:
    source_text: str
    is_final: bool
    segment_id: UUID
    commit_index: int

class StablePrefixCommitter:
    def __init__(
        self,
        *,
        agreement_n: int = 2,
        sentence_boundary_pattern: re.Pattern[str] = re.compile(r"[。！？，：；.!?,:;]"),
        idle_commit_ms: int = 1500,
        max_pending_chars: int = 60,
    ): ...

    def feed(self, event: STTEvent) -> Iterable[CommitChunk]: ...
```

LocalAgreement-N：维护最近 N 个 partial 文本的最长公共前缀；前缀长度增长且包含 sentence_boundary 时触发 commit。

### 4.3 `DeepSeekStreamingClient.translate_commit`（改造 `mt/deepseek_client.py`）

```python
async def translate_commit(
    self,
    commit: CommitChunk,
    *,
    direction: AudioDirection,
) -> AsyncIterator[TranslationChunk]:
    """无 conversation state；每个 commit 独立翻译。"""
```

- 现有 `stream_translate(text, direction)` **必须**保留（`tvi say` 用）
- 新打点：每次请求记录 `first_token_at - request_start`，server 返回的 `prompt_tokens_cached_count`（如 DeepSeek 提供）写入 `TranslationChunk` metadata

### 4.4 `CommitOrchestrator`（新建 `live_streaming.py`）

替代 `LiveSayBridge.prepare` 的实时分支：

```python
class CommitOrchestrator:
    async def run(
        self,
        audio_frames: AsyncIterator[bytes],
        *,
        direction: AudioDirection,
        target: str,
    ) -> AsyncIterator[CommitPlayback]: ...
```

内部协程：

- `feed_task`：PCM → ASR (`feed_audio` + close_segment on VAD)
- `consume_task`：ASR consume → committer.feed → CommitChunk queue
- `commit_task`：CommitChunk → DeepSeek.translate_commit → Edge-TTS PCM iterator

每个 commit 产生一个 `CommitPlayback`（含已合成 PCM iterator），按 `commit_index` 顺序送 playback queue。

### 4.5 `PlaybackWorker`（改造 `cli/app.py`）

- 队列元素从段级 `_PendingPlayback` 改为 commit 级 `_PendingCommit`
- 同一 segment 的多个 commit **必须**顺序播放、无 gap（直接连续 `feed_pcm`）
- 不同 segment_id 之间保持现有 drop-stale 策略

### 4.6 状态面板（小改 `web/server.py`）

WS 推送新增字段：

- `partial_text`：当前正在累积的 partial
- `committed_text`：已 commit 的子句拼接
- `final_text`：与现状一致

### 4.7 whisper.cpp `stream` binary 部署

- 新建 `scripts/install-whisper-stream.sh`：
  - 优先从 ggml-org/whisper.cpp release 拉预编译 macOS arm64 二进制（**应当**校验 SHA256，与脚本内锁定值比对）
  - 找不到匹配的预编译二进制时（无网 / release 未含 macOS arm64），脚本检测 Xcode Command Line Tools，本地 `git clone whisper.cpp@<lock_tag>` + `make stream`
  - 两种路径都不可用时，**必须**以非 0 退出并给出两段式错误（缺什么 / 用户下一步如何做）
- `tvi wizard` / `tvi doctor --mode realtime` 加 readiness check `stream_binary.unavailable`，提示用户运行脚本
- 二进制路径默认 `~/.cache/teams-voice-interpreter/whisper-stream`，可由 `config.toml` 的 `whisper_stream_binary_path` 覆盖
- 脚本与本设计锁定的 whisper.cpp commit/tag 写入 `specs/001-teams-voice-interpreter/perf-report.md`，跟随 BM 报告

## 5. 数据流时序

### 5.1 场景 A：用户带停顿讲话「我们今天，讨论一下，产品的延期问题」

```
t=0     用户开口；feed_task 把 PCM 送 subprocess
t=400   subprocess 输出 partial "我们"
t=600   partial "我们今天"
t=800   partial "我们今天，"  ← 检测到逗号 + LocalAgreement-2 命中
        Committer.emit CommitChunk("我们今天，", is_final=False)
t=900   DeepSeek.translate_commit 启动（独立翻译）
t=1300  MT 首 token "Today" → Edge-TTS → 设备
        ★ 用户感知首字节 ≈ 1.3 s
t=1500  partial "我们今天，讨论一下，"  ← 第二个逗号 commit
        DeepSeek 独立翻译 "let's discuss,"
        播放队列追加，无 gap 接在 "Today," 之后
t=2300  用户停顿 → VadSegmenter 触发 close_segment
t=2400  ASR final "我们今天，讨论一下，产品的延期问题"
        Committer.emit CommitChunk("产品的延期问题", is_final=True)
        DeepSeek 翻译 "the delay of products"
对端听到："Today, ... let's discuss, ... the delay of products"
```

### 5.2 场景 B：用户长讲不停顿「我们今天讨论一下产品的延期问题」

- 无标点 / 静默触发；Committer 累积到 `max_pending_chars=60` 仍不触发 commit
- 退化到 VAD final 模式：行为 ≈ 当前实现（partial 仅供 status 显示，TTS 等 final）
- 首字节 ≈ 当前水平（不退化、不进一步收益）

## 6. 错误处理

| 故障 | 处理 |
|---|---|
| whisper.cpp subprocess 崩溃 | supervisor heartbeat 3 s 触发 respawn（`contracts/whisper-cpp.md` §6）；当前 segment 标记为失败、partial buffer 清空、用户感知是该段无译音 + 两段式提示 |
| DeepSeek 单 commit 失败 | 现有 FR-018 退避（250 / 500 / 1000 / 2000 / 4000 ms）保留；该 commit 跳过，对端听不到那一子句；后续 commit 继续 |
| Edge-TTS commit 失败 | 现有 `max_retries=1` + 3 s 首字节超时 + 8 s 总合成超时保留；失败 commit 跳过 |
| Committer 上游 partial 改写已 commit 部分 | 理论上 LocalAgreement-2 已避免；万一 final 与已 commit 不一致，按「已播即定」处理，final 的剩余部分作为新 commit |
| `stream` binary 缺失 | readiness check 阻断 `tvi duplex` / `tvi listen` 启动，提示运行 `scripts/install-whisper-stream.sh` |

## 7. 测试策略

### 7.1 单元测试

- `tests/contract/test_whisper_streaming.py`（新）：subprocess mock，feed/consume/close_segment 顺序、heartbeat 超时 respawn、JSON Lines 协议（`contracts/whisper-cpp.md` §9 列出的 7 项）
- `tests/unit/stt/test_committer.py`（新）：LocalAgreement-2 算法、sentence-boundary 检测、`idle_commit_ms` 触发、`max_pending_chars` 兜底、partial 改写不影响已 commit
- `tests/contract/test_deepseek_streaming.py`（扩展）：`translate_commit` 独立翻译、prefix caching 探测（5 次相同 system prompt 测 first-token 是否下降）

### 7.2 集成测试

- `tests/integration/test_streaming_duplex.py`（新）：mock 三个外部依赖，跑场景 A（带停顿）和场景 B（长讲）完整链路；断言 commit 顺序、PCM 拼接、错误恢复

### 7.3 性能 benchmark 更新

- **模型档位选择**：sliding 模式默认 `medium-q5_0`（已在用户机器缓存 540 MB，幻觉率比 small 低、推理比 large 快约 2×）。`small-q5_1` 真测出现「请订阅 / 字幕组」幻觉穿透，**不得**作为 sliding 默认；`large-v3-q5_0` 仍可由用户在 `config.toml` 覆盖，但需独立验证 BM-3 是否在预算内
- BM-1 / BM-3：subprocess 稳态 RAM ≤ 500 MB / CPU ≤ 30 %（`contracts/whisper-cpp.md` §7）；预算基于 `medium-q5_0` 档位测量；超预算时 wizard / doctor 报警并要求用户切档或重审宪章
- BM-10 / BM-10D：场景 A（带停顿）partial → 首字节 p50 / p95 vs 当前 baseline（commit `6bf1581`）；场景 B（长讲不停顿）作为已知退化基线单独记录
- **新增 BM-14**：partial commit 改写率，预算 ≤ 1 %（LocalAgreement-2 应当几乎不改写）
- **新增 BM-15**：DeepSeek prefix caching 命中率；如果 ≥ 50 %，可后续切到 MT 增量 commit
- 全部用真链路替换 fixture（顺手解决 `perf-report.md` 600 ms 硬编码问题）

## 8. 实施风险登记

**必须**在 `specs/001-teams-voice-interpreter/plan.md` 复杂度追踪节登记以下风险：

1. **架构复杂度上升**（宪章原则 I）：subprocess 管理 + 新建 4 个模块（Committer / Orchestrator / WhisperStreamingClient real impl / install script）。**退出计划**：partial 链路稳定 6 个月以上、且 BM-14 改写率持续 ≤ 1 % 后，可下线 `LiveSayBridge.prepare` 同步实时分支。
2. **场景 B 退化**：用户讲长句不停顿时收益 ≈ 0；接受为「已知边界」，**应当**在 README + status 面板提示。
3. **commit 间停顿**：对端听到 commit 之间的微停顿（200-400 ms）；如产品反馈不接受，后续可通过 PCM 拼接平滑（属于 v2 优化）。
4. **whisper.cpp `stream` binary 单独维护**：与 pywhispercpp 包版本可能漂移；**应当**在 BM 报告锁定 commit/tag/SHA256。

## 9. 附录：决策日志

| 日期 | 决策 | 依据 |
|---|---|---|
| 2026-05-06 | 选 A 一次性完整流式 | 真测显示分阶段切换链路代价更大 |
| 2026-05-06 | 选「折中」commit 策略 | 激进会议场景不可用、保守不算流式 |
| 2026-05-06 | 选 a whisper.cpp `stream` subprocess | spec / contracts 期望路径，KV cache 复用 CPU 友好 |
| 2026-05-06 | 选 3 sentence-boundary commit + DeepSeek prefix caching 实测 | 每个 commit 独立翻译质量稳定，prefix caching 实测决定后续 |
| 2026-05-06 | ⛔ **暂缓 partial/sliding 整体方案** | 见 §10 |

## 10. 暂缓决策（2026-05-06）

### 10.1 触发原因

设计走完后由 Codex 对抗审查 + 业界开源调研同时暴露三条根本问题：

1. **Codex P0-1**：`contracts/whisper-cpp.md §4-6` 的 `feed_audio / consume / close_segment` JSON Lines 协议是**项目自己设计但未实现**的契约，不是上游 whisper.cpp `stream` binary 的能力 — 该 binary 实际是 SDL 麦克风示例，从麦克风读音频，输出文本到 stdout，不接受 stdin JSON 帧。原方案 §4.7「拉预编译二进制」走不通。
2. **Codex P0-2**：原 §5.2 场景 B「退化到 VAD final」违反 spec FR-008（不得在 STT final 之前阻塞翻译队列）和 FR-013（连续长语音每 30 秒滚动封口），是直接的规约违约而非已知边界。
3. **业界调研结论**：Whisper 训练集片尾幻觉（"请订阅 / 字幕组 / Thanks for watching"）是**确定性高置信输出** — sliding 重复推理 + LocalAgreement-N **治不住**。重跑同一段静音仍吐相同文本，algorithm 会判定其稳定 → commit。原 §1.3「sliding 天然过滤幻觉」承诺站不住。

### 10.2 业界对照

| 项目 | star | 实时同传策略 | 处理幻觉 |
|---|---|---|---|
| RealtimeSTT (KoljaB) | 9.8k（业界最活跃生产级） | **主动放弃** LocalAgreement / sliding，VAD-batch one-shot | Silero VAD + 能量门槛 |
| whisper_streaming (UFAL Macháček et al) | 3.6k | LocalAgreement-2 + faster-whisper | 论文实测中文/日文 compression_ratio 误杀严重 |
| WhisperLiveKit (QuentinFuxa) | 7.3k | Simul-Whisper / AlignAtt + LocalAgreement 可切换 | 200 语言但无中文专项 |
| SimulStreaming (UFAL 继任) | 新项目 | AlignAtt（attention 驱动） | IWSLT 2025 同传冠军，2-4 s 档 |

**RealtimeSTT 9.8k stars 业界最活跃项目主动放弃 partial/sliding** — 与本设计推断的暂缓路径一致。理由（直接引用其文档）：sliding 治不住高置信确定性幻觉 + 中文短帧改写率高 + 字幕跳动比延迟更伤用户。

### 10.3 取代路径（v1 实际走的方向）

**不做 partial/sliding**，转向三件独立可发布的小 PR：

**a. Silero VAD（ONNX 版本）替换 webrtcvad** — 论文 arxiv 2501.11378 实测把 Whisper 幻觉率从 40.3% 压到 0.2%；ONNX 版本依赖 onnxruntime ~50 MB（不是 PyTorch 500 MB，与 research.md §5「轻量初衷」可调和）；这一项单独就能让 small-q5_1 模型重新可用，把 ASR 时间从 large-v3-q5_0 的 2.3 s 砍到 0.5-0.8 s。

**b. Bag of Hallucinations 后过滤** — 用 Aho-Corasick 算法 + Hugging Face 公开数据集 `sachaarbonel/whisper-hallucinations`，多捕获 67% 漏网幻觉。替换现有手写 `HALLUCINATION_PREFIX_PATTERNS`。

**c. 真测 baseline 写入 perf-report.md** — 把今天 5 轮真钉钉通话的 27 段 latency 数据替换 `tests/perf/test_first_segment_latency.py` 等硬编码 fixture（commit `6bf1581` 已积累原始数据）。

预期端到端首字节 p95 从 ~3 s 压到 ≤ 1.5 s（与 SimulStreaming 2-4 s 档差距已小，且稳定性高）。spec FR-007 partial 违约**保留登记**到 plan.md 复杂度追踪，交给 v1.1 处理。

### 10.4 v1.1 重启 partial/sliding 的条件

本文档（§1-9）的设计仍作为未来工程基础。任一以下条件成立时可重启：

- a + b + c 落地后真测端到端首字节 p95 仍 > 1.5 s 且无其它优化空间
- Moonshine 中文模型在 ARM Mac 上跑通且推理速度 ≥ Whisper Tiny 的 2× — 可直接走 sliding 而不必担心 CPU
- whisper.cpp 上游真支持 incremental decode + KV cache 跨 step 复用（当前不支持）
- 业界出现 Whisper 训练集片尾确定性幻觉的有效解（如 Calm-Whisper 微调放出中文版）

**触发条件之一时**，本文档 §3-7 的设计（带 §10 决策修订）作为新 brainstorming 起点。

### 10.5 调研引用

- [whisper_streaming (UFAL)](https://github.com/ufal/whisper_streaming)
- [SimulStreaming (UFAL, IWSLT 2025)](https://github.com/ufal/SimulStreaming)
- [WhisperLiveKit (QuentinFuxa)](https://github.com/QuentinFuxa/WhisperLiveKit)
- [RealtimeSTT (KoljaB)](https://github.com/KoljaB/RealtimeSTT)
- [Moonshine (moonshine-ai)](https://github.com/moonshine-ai/moonshine)
- [Investigation of Whisper ASR Hallucinations Induced by Non-Speech Audio (arxiv 2501.11378)](https://arxiv.org/html/2501.11378v1)
- [Turning Whisper into Real-Time Transcription System (arxiv 2307.14743)](https://arxiv.org/html/2307.14743)
- [whisper-hallucinations dataset (Hugging Face)](https://huggingface.co/datasets/sachaarbonel/whisper-hallucinations)
