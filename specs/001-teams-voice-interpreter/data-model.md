# 数据模型：Teams 实时双向语音同传桥（macOS）

**关联**：[plan.md](plan.md) · [spec.md](spec.md) · [research.md](research.md)
**日期**：2026-05-05
**说明**：本文件定义 v1 全部领域实体、字段、关系、验证规则、生命周期状态转换。所有实体使用 Pydantic 2 模型实现，落位于 `src/teams_voice_interpreter/data/`。**v1 仅内存模型，不映射到任何持久化数据库**。

---

## 实体总览

| 实体 | 文件 | 持久化范围 | 备注 |
|------|------|-----------|------|
| `Session` | `data/session.py` | 仅内存（会话期） | 单实例约束（FR-026） |
| `AudioStream` | `data/audio_segment.py` | 仅内存 | 上行 / 下行各一条 |
| `TranscriptSegment` | `data/transcript.py` | 仅内存（会话期完整保留） | partial / final 两态 |
| `TranslationSegment` | `data/transcript.py` | 仅内存（会话期完整保留） | 用于 FR-027 导出 |
| `SynthesizedAudioSegment` | `data/audio_segment.py` | 仅内存（写出后即丢） | 含字节流 ring buffer 引用 |
| `LatencySnapshot` | `data/latency.py` | 仅内存（滚动统计） | FR-016 / 状态面板 |
| `ServiceCredential` | `data/credential.py` | 加载自配置；密钥仅引用环境变量 | FR-022 |
| `GlossaryEntry` | `data/glossary.py` | 加载自 `glossary.toml` | FR-012 静态术语表 |
| `CrashReport` | `data/crash.py` | 落地到 `~/.cache/` | FR-029 匿名 |

---

## 1. Session（会话）

**职责**：一次「开始 → 停止」之间的同传上下文；Singleton（FR-026）。

```python
from pydantic import BaseModel, Field
from datetime import datetime
from enum import Enum
from typing import Literal
from uuid import UUID

class SessionState(str, Enum):
    IDLE = "idle"
    STARTING = "starting"
    ACTIVE = "active"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERRORED = "errored"

class Session(BaseModel):
    session_id: UUID                                # 启动时生成（uuid7 优先；fallback uuid4）
    state: SessionState = SessionState.IDLE
    started_at: datetime | None = None              # state=ACTIVE 时设置
    stopped_at: datetime | None = None              # state=STOPPED 时设置
    uplink_enabled: bool = True
    downlink_enabled: bool = True
    web_port: int = Field(default=8765, ge=1024, le=65535)
    credential_ref_ids: list[str] = Field(default_factory=list)   # 引用 ServiceCredential.id
    glossary_path: str | None = None                # 注入时已确定，运行中不变（FR-012 不热更新）
    glossary_loaded_count: int = 0                  # 实际加载的 GlossaryEntry 数量
    transcripts: list["TranscriptSegment"] = Field(default_factory=list)
    translations: list["TranslationSegment"] = Field(default_factory=list)
    latency_window: list["LatencySnapshot"] = Field(default_factory=list)   # 滚动 60 s
    panel_recent_window_size: int = 100             # 面板渲染滚动窗口（FR-024）
```

**验证规则**：

- `session_id` 全局唯一
- `state` 转换必须按下方状态机
- `web_port` 必须可绑定（启动时 try-bind 验证）
- `glossary_path` 若给定必须文件存在 + 可读，否则 `glossary_loaded_count = 0` 并继续启动（FR-012 graceful 处理）

**状态机**：

```text
IDLE ─start()─→ STARTING ─ready()─→ ACTIVE
                STARTING ─fail()──→ ERRORED
ACTIVE ─pause()─→ PAUSED ─resume()─→ ACTIVE
ACTIVE / PAUSED ─stop()─→ STOPPING ─cleanup()─→ STOPPED
任意状态 ─unrecoverable()─→ ERRORED
ERRORED ─reset()─→ IDLE                              # 仅供测试用
```

**完整双语对照保留策略（FR-024）**：

- `transcripts` 与 `translations` 在 `state ∈ {ACTIVE, PAUSED}` 期间**完整保留**所有 final 段（partial 段允许覆盖前一个 partial）
- `state = STOPPING → STOPPED` 转换时若用户未触发 FR-027 导出，**必须**清空两个列表（内存释放）
- 状态面板渲染时仅取最近 `panel_recent_window_size` 条 final 段

---

## 2. AudioStream（音频流）

**职责**：抽象一路音频管道（上行 = 用户中文 → Teams 英文；下行 = Teams 英文 → 用户中文）。

```python
class AudioDirection(str, Enum):
    UPLINK = "uplink"       # 用户麦 → Teams BlackHole 输入
    DOWNLINK = "downlink"   # Teams 输出 → 本系统 STT；中文 TTS → 用户耳机

class AudioStream(BaseModel):
    direction: AudioDirection
    source_device_name: str                         # e.g. "Built-in Microphone" / "BlackHole 2ch"
    source_device_index: int                        # sounddevice.query_devices() index
    sink_device_name: str                           # e.g. "BlackHole 2ch" / "MacBook Pro Speakers"
    sink_device_index: int
    sample_rate_hz: int = 16000
    channels: Literal[1] = 1                        # v1 仅单声道
    active: bool = False
    bytes_in: int = 0                                # 滚动累计（已捕获）
    bytes_out: int = 0                               # 滚动累计（已写出）
```

**验证规则**：

- `source_device_index` 与 `sink_device_index` 启动时必须存在于 `sounddevice.query_devices()`，否则 FR-020 阻止启动
- 上行的 `sink_device_name` **必须**包含 "BlackHole"（路由验证）
- 下行的 `source_device_name` **必须**包含 "BlackHole" 或属于 Aggregate Device 的成员（路由验证）

---

## 3. TranscriptSegment（识别片段）

**职责**：STT 输出的一段中文或英文识别文本。

```python
class TranscriptKind(str, Enum):
    PARTIAL = "partial"
    FINAL = "final"

class TranscriptSegment(BaseModel):
    segment_id: UUID
    direction: AudioDirection
    kind: TranscriptKind
    started_at: datetime                             # 该段开始捕获的时间戳（音频本地时钟）
    ended_at: datetime | None = None                 # final 时设置
    text: str                                        # 中文或英文，未做标点修正
    confidence: float = Field(ge=0.0, le=1.0)
    provider: Literal["whisper.cpp"] = "whisper.cpp"
    provider_model: str = "ggml-small-q5_0"
```

**验证规则**：

- `kind=FINAL` 必须有 `ended_at` 且 `len(text) > 0`
- `kind=PARTIAL` 同 segment_id 序列中后续 partial 可覆盖前者；final 一旦写入则该 segment_id 不再变更
- VAD 静音 ≥ 5 s（FR-013）触发当前 partial 强制升级为 final（含或丢弃文本由 confidence 决定）

---

## 4. TranslationSegment（翻译片段）

**职责**：DeepSeek 翻译输出的一段译文。

```python
class TranslationSegment(BaseModel):
    segment_id: UUID                                  # 与对应 TranscriptSegment 共享 ID（一对一）
    source_segment_id: UUID                           # 冗余引用，便于反查
    direction: AudioDirection
    started_at: datetime                              # 触发翻译调用的时间
    first_token_at: datetime | None = None            # 首 token 到达时间（用于 SC-001）
    completed_at: datetime | None = None              # 完整译文 finalized
    target_text: str                                  # 译文（流式拼接得到）
    target_language: Literal["zh", "en"]
    provider: Literal["deepseek"] = "deepseek"
    provider_model: str = "deepseek-chat"
    glossary_hit_count: int = 0                       # 本段命中术语表条目数
```

**验证规则**：

- `direction=UPLINK` 时 `target_language="en"`；`direction=DOWNLINK` 时 `target_language="zh"`
- `first_token_at - started_at` 用于宪章 IV「LLM 翻译首 token ≤ 800 ms」校验
- `completed_at - started_at` 用于「LLM 翻译整段 ≤ 1.5 s」校验

---

## 5. SynthesizedAudioSegment（合成音频片段）

**职责**：TTS 输出的一段流式音频；只在内存中存在到写出虚拟设备 / 默认输出后丢弃。

```python
class SynthesizedAudioSegment(BaseModel):
    segment_id: UUID                                  # 与对应 TranslationSegment 共享 ID
    direction: AudioDirection
    started_at: datetime                              # 触发 TTS 调用的时间
    first_byte_at: datetime | None = None             # 首 audio chunk 到达时间（用于 SC-001 端到端首段）
    completed_at: datetime | None = None
    sample_rate_hz: int = 16000
    channels: Literal[1] = 1
    target_device_name: str                           # 上行写 BlackHole，下行写默认输出
    bytes_written: int = 0                             # 滚动累计
    chunk_count: int = 0
    provider: Literal["edge-tts"] = "edge-tts"
    provider_voice: str                                # e.g. "en-US-AriaNeural" / "zh-CN-XiaoxiaoNeural"
```

**验证规则**：

- 不持久化原始音频字节（FR-023）；ring buffer 仅暂存到写出
- `first_byte_at - started_at + first_token_at` 之前的耗时之和必须满足端到端首段译音 ≤ 1200 ms（硬阈值，2026-05-07 宪章修订自 800 ms）/ ≤ 1000 ms（软目标）

---

## 6. LatencySnapshot（延迟指标快照）

**职责**：FR-016 / SC-001 / SC-003 / 宪章 IV 所有延迟指标的滚动统计。

```python
class LatencyStage(str, Enum):
    AUDIO_CAPTURE = "audio_capture"      # 麦克风 → 内存 buffer
    STT_PARTIAL = "stt_partial"           # buffer → Whisper.cpp partial
    STT_FINAL = "stt_final"               # buffer → Whisper.cpp final
    MT_FIRST_TOKEN = "mt_first_token"    # final/partial → DeepSeek 首 token
    MT_COMPLETED = "mt_completed"        # final/partial → DeepSeek 整段
    TTS_FIRST_BYTE = "tts_first_byte"   # 译文 → Edge-TTS 首 byte
    TTS_COMPLETED = "tts_completed"
    AUDIO_ROUTE = "audio_route"           # TTS chunk → 远端听到（含路由开销）
    E2E_FIRST_SEG = "e2e_first_segment"  # 用户开口 → 远端听到首段
    E2E_FULL = "e2e_full"                  # 用户停口 → 远端听完

class LatencySample(BaseModel):
    stage: LatencyStage
    direction: AudioDirection
    duration_ms: float = Field(ge=0)
    measured_at: datetime
    associated_segment_id: UUID | None = None

class LatencySnapshot(BaseModel):
    window_seconds: int = 60                          # 滚动窗口
    samples_per_stage: dict[LatencyStage, list[float]] = Field(default_factory=dict)
    p50: dict[LatencyStage, float] = Field(default_factory=dict)
    p95: dict[LatencyStage, float] = Field(default_factory=dict)
    avg: dict[LatencyStage, float] = Field(default_factory=dict)
    max: dict[LatencyStage, float] = Field(default_factory=dict)
```

**验证规则**：

- `samples_per_stage` 滚动保留最近 60 s 数据；超出窗口的样本剔除
- `p50` / `p95` 实时计算；面板每 200 ms 拉一次（≥ 5 Hz）

**宪章 IV 校验**：

- `p50[E2E_FIRST_SEG] ≤ 1200 ms`（硬阈值，2026-05-07 宪章修订自 800 ms）/ ≤ 1000 ms（软目标）
- `p95[E2E_FIRST_SEG] ≤ 2000 ms`（2026-05-07 宪章修订自 1200 ms；本次同时把原 data-model 与 SC-001 旧 p95 1500 ms 之间的轻微不一致一并对齐到统一新阈值）
- `p50[E2E_FULL] ≤ 2500 ms`
- `p95[E2E_FULL] ≤ 4000 ms`
- `p50[MT_FIRST_TOKEN] ≤ 800 ms`
- `p50[MT_COMPLETED] ≤ 1500 ms`

---

## 7. ServiceCredential（服务凭证）

**职责**：抽象一组外部服务凭证；密钥**永远不进入内存对象**，仅持有环境变量名。

```python
class ServiceKind(str, Enum):
    STT = "stt"
    MT = "mt"
    TTS = "tts"

class ServiceCredential(BaseModel):
    id: str                                            # e.g. "deepseek-prod"
    service: ServiceKind
    provider: str                                      # e.g. "deepseek" / "whisper.cpp" / "edge-tts"
    endpoint: str | None = None                       # 仅外部服务有；本地服务为 None
    key_env_var: str | None = None                    # e.g. "DEEPSEEK_API_KEY"；本地服务为 None
    quota_threshold_warning_pct: float = 0.8          # 配额告警阈值
    healthy: bool = True                               # 当前连接健康状态
    last_check_at: datetime | None = None
```

**验证规则**：

- 模型实例**不得**包含真实 API Key 字符串
- `key_env_var` 给定时启动时校验环境变量已设置；缺失时 FR-020 阻止启动
- `endpoint` 必须是 https:// （DeepSeek）；whisper.cpp / edge-tts 为 None

---

## 8. GlossaryEntry（术语表条目）

**职责**：FR-012 静态术语表的单条记录。

```python
class GlossaryEntry(BaseModel):
    zh: str = Field(min_length=1, max_length=64)
    en: str = Field(min_length=1, max_length=128)
    note: str | None = Field(default=None, max_length=256)
    source: Literal["user", "v2-builtin"] = "user"   # v1 全部 user
```

**glossary.toml 示例**：

```toml
[[entries]]
zh = "DeepSeek"
en = "DeepSeek"
note = "保留品牌名原写"

[[entries]]
zh = "K8s"
en = "K8s"
note = "Kubernetes 缩写，不展开"

[[entries]]
zh = "福昕"
en = "Foxit"
```

**验证规则**：

- 同一 `zh` 不允许出现两次（启动时去重 + 警告日志）
- `len(entries) ≤ 200`（避免 system prompt 爆炸）；超出阻止启动并提示用户精简
- 启动时一次性合成进 DeepSeek system prompt（FR-012）；运行中**不得**热更新

---

## 9. CrashReport（崩溃报告）

**职责**：FR-029 匿名崩溃报告；落地到 `~/.cache/`，**不进入 Session / 内存运行时模型**。

```python
class CrashReport(BaseModel):
    occurred_at: datetime
    python_version: str
    os_version: str
    arch: Literal["arm64", "x86_64"]
    dependency_versions: dict[str, str]               # {"whisper.cpp": "...", "edge-tts": "...", ...}
    stack_trace: str                                   # 已脱敏（家目录替换为 ~）
    services_health_snapshot: dict[ServiceKind, bool]
    resource_snapshot: dict[Literal["ram_mb", "cpu_pct"], float]
    notes: str | None = None
```

**禁字段**（**不得**包含）：

- 任何 TranscriptSegment / TranslationSegment 文本
- 任何原始音频字节
- API Key 字符串（即使脱敏后）
- 用户家目录绝对路径（必须替换为 `~`）
- 用户名 / 机器名

**轮转策略**：保留最新 20 份；每次新写入前按 mtime 排序删除多余项。

---

## 关系图

```text
Session 1 ─┬─ * TranscriptSegment ─ 1 → 1 TranslationSegment ─ 1 → 1 SynthesizedAudioSegment
           ├─ 2 AudioStream（uplink + downlink）
           ├─ * LatencySample ──────────→ 1 LatencySnapshot（聚合视图）
           ├─ * ServiceCredential（引用，不持有）
           └─ * GlossaryEntry（引用，启动时一次性合并到 prompt）

CrashReport：与 Session 解耦，独立落地到 ~/.cache/。
```

---

## v1 不实现的实体（延至 v2）

- `User` / `Tenant` / `Workspace`：v1 单实例单用户，无多租户
- `MeetingTranscriptArchive`：v1 不持久化历史会话
- `RegulatedScenarioConsent`：v1 完全规避监管严格场景（spec Q1 D 决定）
- `MultiSessionPool`：v1 单会话单 Teams（spec Q2 A 决定）

---

## 出口断言

- ✅ 9 个实体定义齐全（含字段、验证规则、关系）
- ✅ 与 spec 7 个原始 Key Entities + 1 个 GlossaryEntry 完全一致
- ✅ 状态机覆盖 Session 全生命周期
- ✅ FR-022 / FR-023 / FR-024 / FR-029 隐私边界在模型层强制（密钥仅引用、报告禁字段、内存释放策略）
- ✅ 宪章 IV 性能预算在 LatencySnapshot 中可直接校验
