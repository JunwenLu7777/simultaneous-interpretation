# 契约：Whisper.cpp Streaming（本地 STT）

**关联**：[plan.md](../plan.md) · [research.md](../research.md) §1
**实现位置**：`src/teams_voice_interpreter/stt/whisper_streaming.py` + `src/teams_voice_interpreter/stt/client.py`

## 1. 适用范围

承担**流式 STT** 全部职责（FR-007 / FR-010）。v1 双向识别均通过本契约调用本地 Whisper.cpp 推理子进程。

## 2. 后端

- **库**：`pywhispercpp` ≥ 0.4（首选）或 `faster-whisper` + 自定义 streaming wrapper（后备）
- **运行模式**：本地子进程（FR-028 由 supervisor 监控 / respawn）
- **加速后端**：Apple Silicon → Metal + Core ML encoder offload；Intel → CPU + AVX2

## 3. 模型契约

| 档位 | 文件 | RAM | 准确率（普通话 WER） | 默认？ |
|------|------|-----|----------------------|--------|
| `ggml-tiny.bin` | ~75 MB | ~200 MB | 18% | 否（仅低端机器降级） |
| `ggml-tiny.en.bin` | ~75 MB | ~200 MB | N/A（仅英文） | 否 |
| `ggml-small-q5_0.bin` | ~470 MB | 1.0–1.2 GB | 9% | **是（v1 默认）** |
| `ggml-small.bin` | ~466 MB | 1.0–1.5 GB | 8% | 否（q5_0 已够） |
| `ggml-medium-q5_0.bin` | ~1.5 GB | 2.5–3.0 GB | 5.5% | 否（违反 RAM 预算更严） |
| `ggml-large-v3.bin` | ~3.0 GB | 5–6 GB | 3% | 否（v1 不可行） |

**模型缓存路径**：`~/.cache/teams-voice-interpreter/whisper-models/`

**首次下载**：CLI `wizard` 首次运行时自动下载 `ggml-small-q5_0.bin`；CDN 选择优先国内镜像（`hf-mirror.com`）+ 官方 fallback。

## 4. 流式 wrapper 接口

```python
class WhisperStreamingClient:
    def __init__(
        self,
        model_path: Path,
        language: Literal["zh", "en"],
        sample_rate_hz: int = 16000,
        chunk_size_ms: int = 30,            # 输入帧大小
        step_size_ms: int = 300,             # 滑窗步长（partial 输出频率）
        context_size_sec: int = 5,           # 上下文窗口
        metal_enabled: bool = True,
        core_ml_encoder: bool = True,
    ): ...

    async def feed_audio(self, pcm16_bytes: bytes) -> None:
        """喂入 30 ms 音频帧。"""

    async def consume(self) -> AsyncIterator[STTEvent]:
        """
        Yields:
          STTEvent(kind=PARTIAL, segment_id=UUID, text=str, confidence=float)
          STTEvent(kind=FINAL, segment_id=UUID, text=str, confidence=float, ended_at=datetime)

        partial 输出频率 ≈ 1 / step_size_ms（默认每 300 ms 一个）。
        final 由 VAD（FR-013）或显式 close_segment() 触发。
        """

    async def close_segment(self) -> None:
        """显式 finalize 当前段（外部 VAD 触发）。"""

    async def shutdown(self) -> None:
        """关闭子进程，释放 GPU/Metal 资源。"""
```

## 5. 子进程通信契约（与 supervisor 配合 FR-028）

- **协议**：stdin / stdout 双向 + JSON Lines
- **stdin 帧**：
  ```json
  {"op": "feed", "ts": 1730707200.123, "pcm16_b64": "..."}
  {"op": "close_segment"}
  {"op": "shutdown"}
  ```
- **stdout 帧**：
  ```json
  {"event": "partial", "segment_id": "...", "text": "...", "confidence": 0.78}
  {"event": "final", "segment_id": "...", "text": "...", "confidence": 0.85, "ended_at": "..."}
  {"event": "heartbeat", "ts": 1730707200.123}      # 每秒一次（FR-028 supervisor 监控）
  {"event": "error", "message": "..."}
  ```

**heartbeat 超时阈值**：3 秒（supervisor 视为子进程卡死）

## 6. VAD 集成（FR-013）

- VAD 在主进程内（`stt/vad.py`），不在 Whisper 子进程内
- VAD 检测到 ≥ 5 s 静音 → 主进程发 `{"op": "close_segment"}` → 子进程把当前 partial 升级为 final
- 静音期间**不**喂任何音频帧给子进程（FR-013 "停止向 TTS 推送空白"上游强制）

## 7. 性能 SLA

| 指标 | 预算 | 实测期望 | 测量位置 |
|------|------|---------|----------|
| partial 延迟（音频末尾 → partial 文本） | ≤ 800 ms | 400–700 ms | `LatencySample(stage=STT_PARTIAL)` |
| final 延迟（VAD 触发 → final 文本） | ≤ 200 ms 增量 | 100–200 ms | `LatencySample(stage=STT_FINAL)` |
| 稳态 RAM（**违反宪章 IV ≤ 500 MB；plan §Complexity Tracking 行 1 已批准例外阈值；spec §SC-010 已批准档**） | ≤ 1.6 GB（已批准的例外，超过则触发宪章 IV 修订 PR）| 1.0–1.5 GB | `psutil` 子进程监控；测量窗口 = 启动后 ≥ 5 分钟稳态、5 分钟滚动平均 |
| 稳态 CPU（**临近违反宪章 IV ≤ 30%；plan §Complexity Tracking 行 2 已批准例外阈值；spec §SC-010 已批准档**）| ≤ 30%（理想）/ ≤ 40%（已批准的例外）| 25–40% | `psutil` 子进程监控；同上测量窗口 |

## 8. 错误处理契约

| 错误 | 客户端动作 |
|------|-----------|
| 模型文件缺失 | 阻止启动，提示用户运行 `tvi wizard` |
| 模型加载失败（OOM） | 自动降档：small → tiny；记录到崩溃报告 |
| 子进程崩溃（exit code != 0） | FR-028 supervisor 自动 respawn ≤ 5 s；连续 ≥ 3 次熔断 |
| heartbeat 超时 | 同上 respawn |
| Metal 后端不可用 | 自动 fallback CPU；warning 提示用户性能下降 |

## 9. 契约测试要求

`tests/contract/test_whisper_cpp.py` 必须覆盖：

- ✅ 模型加载成功（含 q5_0 量化版本）
- ✅ partial / final 输出顺序与一致性（feed 已知音频 → 期望文本）
- ✅ VAD 触发的 close_segment 行为
- ✅ 子进程崩溃时主进程不被拖死
- ✅ heartbeat 机制（kill -STOP 模拟卡死 → 触发 supervisor 重启）
- ✅ 模型降档路径（强制 small 加载失败 → 自动到 tiny）
- ✅ 100% 分支覆盖

## 10. 安全 / 隐私

- 子进程**不得**对外发起任何网络请求（Whisper.cpp 是纯本地推理）
- 子进程日志默认级别 ERROR；启用 DEBUG 时**不得**打印识别原文
- 模型文件下载完成后**必须**校验 SHA256（防中间人篡改）

## 11. 版本固定策略

- `pywhispercpp == 0.4.x`（minor 升级允许；major 升级需手动验证流式 wrapper 兼容）
- 模型 `ggml-small-q5_0.bin` 由 SHA256 锁定
- 兼容 whisper.cpp 1.6+
