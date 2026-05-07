# 契约：Piper 本地流式合成

**关联**：[plan.md](../plan.md) · [perf-report.md](../perf-report.md)「TTS 引擎对比与 Piper 决策」  
**实现位置**：`src/teams_voice_interpreter/tts/piper_client.py`  
**定位**：v1 生产默认 TTS 引擎；`Edge-TTS` 仅作为显式配置的降级路径保留。

## 1. 适用范围

承担 FR-009 / FR-010 的**流式 TTS**职责。上行链路使用英文 voice，向上行虚拟设备写入译音；下行链路使用中文 voice，向 macOS 默认输出写入译音。

## 2. 客户端

- **库**：`piper-tts` + `onnxruntime`
- **核心 API**：`piper.PiperVoice.load(...).synthesize(text)`
- **封装接口**：`PiperClient.stream_synthesize(text, direction=...)`
- **模型目录**：`Settings.resolved_piper_models_dir()`；默认 `~/.cache/teams-voice-interpreter/piper-models`

## 3. voice 契约

| 方向 | 默认 voice | 文件 |
|------|------------|------|
| 上行（英文给远端） | `en_US-amy-medium` | `en_US-amy-medium.onnx` + `en_US-amy-medium.onnx.json` |
| 下行（中文给用户） | `zh_CN-huayan-medium` | `zh_CN-huayan-medium.onnx` + `zh_CN-huayan-medium.onnx.json` |

启动前 **必须**校验默认两个 voice 文件存在；缺失时 `tvi doctor` / `tvi wizard` 必须 fail-closed，并提示用户从 `rhasspy/piper-voices` 下载到模型目录。

## 4. 输出契约

- **格式**：raw PCM16 mono，当前默认 voice 为 22050 Hz
- **事件**：复用 `TTSEvent`
  - `kind="first_byte"`：首个非空 PCM chunk
  - `kind="audio_chunk"`：后续 PCM chunk
  - `kind="completed"`：合成完成
- **下游处理**：按 `audio_format="pcm_s16le_22050"` 走 PCM 解码 / 重采样分支，必要时重采样到目标设备采样率。

## 5. 性能 SLA

| 指标 | 预算 | 测量位置 |
|------|------|----------|
| 首字节延迟 | p50 ≤ 200 ms / p95 ≤ 400 ms | `LatencySample(stage=TTS_FIRST_BYTE)` / BM-6 |
| 整段延迟（30 字 / 词译文 → 完整音频） | p50 ≤ 1500 ms | `LatencySample(stage=TTS_COMPLETED)` |
| 默认模型占用 | voice 文件总量约 120 MB | `tvi doctor` readiness 输出 |

当前 Piper 探针实测：上行 p50 ≈ 103 ms、下行 p50 ≈ 107 ms；该结果把 SC-001 / SC-002 端到端首段译音重新拉回 ≤ 1200 ms 硬阈值内。

## 6. 错误处理契约

| 错误 | 客户端动作 |
|------|-----------|
| 缺少 `.onnx` 或 `.onnx.json` | 不重试，抛 `PiperTTSError(code="tts.voice_invalid")`，提示下载对应 voice 文件 |
| `piper-tts` / `onnxruntime` 未安装 | `tvi doctor` / `tvi wizard` 阻断启动，提示运行 `uv sync --extra dev` |
| ONNX runtime / IO / 模型损坏 | 包装为 `PiperTTSError(code="tts.piper_synthesize_failed")`，提示重新下载模型并确认依赖 |
| 空文本 | 抛 `PiperTTSError(code="tts.empty_text")`，等待下一段有效译文 |
| 未返回任何音频 | 抛 `PiperTTSError(code="tts.no_audio")`，允许实时路径按既有可恢复错误策略轻量重试 |

## 7. 降级路径

`config.toml` 可显式设置：

```toml
tts_engine = "edge_tts"
```

该降级路径用于 Piper 模型暂未下载、Piper ONNX runtime 异常或用户需要临时保留原 Edge-TTS 行为时使用。选择 Edge-TTS 后，系统必须重新按 `contracts/edge-tts.md` 的非官方接口风险与较宽延迟预算审计，且不得把 Edge-TTS 结果声明为 Piper 生产默认路径。

## 8. 契约测试要求

`tests/unit/test_piper_client.py` 必须覆盖：

- 默认中英文 voice 映射
- voice 文件缺失 fail-closed
- 同步 generator 到异步 `TTSEvent` 流转换
- 空音频与 ONNX runtime 异常包装
- `audio_format="pcm_s16le_22050"` 输出格式

## 9. 安全 / 隐私

- **不得**在日志中记录完整译文（仅记 hash + 字数或摘要）
- **不得**持久化音频 chunks
- Piper 本地推理不向第三方发送 TTS 文本，但 DeepSeek 翻译链路仍按既有隐私边界处理
