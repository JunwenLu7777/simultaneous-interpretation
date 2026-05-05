# 契约：Edge-TTS 流式合成

**关联**：[plan.md](../plan.md) · [research.md](../research.md) §3
**实现位置**：`src/teams_voice_interpreter/tts/edge_tts_client.py`
**警告**：**Edge-TTS 是非官方接口**（社区维护的 Microsoft Edge 浏览器 TTS 客户端逆向调用），不是 Microsoft Azure 官方付费 API。本契约描述当前 `edge-tts` 7.x 版本行为；任何接口变更均可能让本契约失效，必须有阶段 0 BM-7 监控 + Coqui XTTS-v2 降级路径。

## 1. 适用范围

承担**流式 TTS** 全部职责（FR-009 / FR-010）。v1 双向译音合成均通过本契约。

## 2. 客户端

- **库**：`edge-tts` ≥ 7.0
- **核心 API**：
  ```python
  from edge_tts import Communicate
  comm = Communicate(text=text, voice=voice, rate=rate, pitch=pitch)
  async for chunk in comm.stream():
      if chunk["type"] == "audio":
          yield chunk["data"]   # bytes (mp3 / opus / pcm 取决于音色支持)
      elif chunk["type"] == "WordBoundary":
          ...                    # 单词时间戳，可用于 lip-sync，v1 不使用
  ```

## 3. 音色契约

| 方向 | 默认音色 | 备选 | 备注 |
|------|---------|------|------|
| 上行（英文给远端） | `en-US-AriaNeural` | `en-US-GuyNeural` / `en-GB-SoniaNeural` | Aria：友好商务感 |
| 下行（中文给用户） | `zh-CN-XiaoxiaoNeural` | `zh-CN-YunxiNeural` / `zh-CN-YunyangNeural` | Xiaoxiao：标准普通话女声 |

**音色枚举校验**：启动时调用 `edge_tts.list_voices()` 校验配置中的音色是否仍可用，缺失时阻止启动并给出可用音色列表。

## 4. 输入 schema

```python
class TTSRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)        # 译文文本
    voice: str                                                # 音色 ID
    rate: str = "+0%"                                         # 语速调整 -100% ~ +200%
    pitch: str = "+0Hz"                                       # 音高 -50Hz ~ +50Hz
    output_format: Literal["audio-24khz-48kbitrate-mono-mp3"] = "audio-24khz-48kbitrate-mono-mp3"
```

## 5. 输出契约（流式 chunks）

- **chunk 大小**：≈ 25 ms 音频 / chunk
- **首字节延迟**：实测 200–400 ms（普通网络）
- **格式**：mp3 24 kHz mono，48 kbps；客户端用 `pydub` 或 `audioop` 解码到 16 kHz PCM16 写入 sounddevice / BlackHole
- **总长度**：依赖 text；30 字中文译文约 2–3 s 音频

## 6. 客户端封装

```python
async def stream_synthesize(
    text: str,
    voice: str,
    direction: AudioDirection,
) -> AsyncIterator[TTSEvent]:
    """
    Yields:
      TTSEvent(kind=FIRST_BYTE, audio_chunk=bytes, latency_ms=int)
      TTSEvent(kind=AUDIO_CHUNK, audio_chunk=bytes)
      ... 多次 AUDIO_CHUNK ...
      TTSEvent(kind=COMPLETED, total_bytes=int, total_latency_ms=int)

    失败抛 EdgeTTSError，由调用方走 FR-018 退避重连。
    连续 ≥ 30 s 不可恢复触发 FR-019 + plan.md 复杂度追踪行 4 降级。
    """
```

## 7. 错误处理契约

| 错误 | 客户端动作 |
|------|-----------|
| `aiohttp.ClientResponseError` 401/403 | 视为鉴权 token 过期，自动调用 `_get_tts_token()` 刷新一次；连续 3 次失败标记接口降级，触发 plan 复杂度追踪行 4 |
| 5xx | 指数退避重试（250 / 500 / 1000 ms），最多 3 次；第一次失败必须立即推送 `service_error` retry 状态 |
| 网络中断 | 同上 |
| 音色 ID 无效 | 不重试，抛 `UserFacingError`（"音色 ID 无效，请在 config.toml 中切换"） |
| 文本含非法字符 | 自动 sanitize（去除 SSML 注入） |

## 8. 性能 SLA

| 指标 | 预算 | 测量位置 |
|------|------|----------|
| 首字节延迟 | p50 ≤ 400 ms / p95 ≤ 800 ms | `LatencySample(stage=TTS_FIRST_BYTE)` |
| 整段延迟（30 字译文 → 完整音频） | p50 ≤ 1500 ms | `LatencySample(stage=TTS_COMPLETED)` |
| 24h 401/403 失败率 | < 0.5% | 阶段 0 BM-7 |

## 9. 降级路径（复杂度追踪行 4）

触发条件：

- 单次会话内 ≥ 3 次 401/403 → 立即切到本地 Coqui XTTS-v2
- 启动 ping 失败 → 提示用户检查网络 / 切付费档
- BM-7 周报失败率 ≥ 0.5% → 自动开 issue 跟进

延迟语义：

- 正常路径下，Edge-TTS retry 不计入 SC-001 的首段成功样本；失败发生后必须按 SC-006 在 ≤ 5 秒显示 retry 状态。
- retry 期间该方向最多播放 ≤ 2 秒旧译音，之后静音并标记「正在重试」。
- 3 次 401/403 失败后的降级切换不保证落在 SC-001 正常首段预算内，必须在 perf-report 中单独记录为 `exit_action=stack_replacement` 或 `edge_tts_degraded`。

降级实现（`tts/coqui_fallback.py`，v1 占位、v1.1 实施）：

```python
class CoquiXTTSv2Client:
    """本地 XTTS-v2 推理；模型 1.8 GB，需用户首次显式同意下载。"""
    async def stream_synthesize(...) -> AsyncIterator[TTSEvent]:
        ...
```

## 10. 契约测试要求

`tests/contract/test_edge_tts.py` 必须覆盖：

- ✅ 正常流式响应（fixture 录制的 audio chunks）
- ✅ 401/403 自动 token 刷新
- ✅ 音色枚举校验
- ✅ 文本 SSML 注入防御（输入 `<speak>...` 应被 sanitize）
- ✅ 100% 分支覆盖

## 11. 安全 / 隐私

- **不得**在日志中记录完整译文（仅记 hash + 字数）
- **不得**持久化音频 chunks
- **不得**复用同一 token 跨进程（每个进程独立刷新）
