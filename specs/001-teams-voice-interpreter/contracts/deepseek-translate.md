# 契约：DeepSeek Streaming Chat Completion

**关联**：[plan.md](../plan.md) · [research.md](../research.md) §2
**实现位置**：`src/teams_voice_interpreter/mt/deepseek_client.py`

## 1. 适用范围

承担**翻译模块（MT）**的全部职责（FR-008 / FR-010）。v1 双向翻译均通过本契约调用 DeepSeek `/v1/chat/completions` 端点的 SSE streaming 模式。

## 2. 端点

- **Base URL**：`https://api.deepseek.com`
- **Path**：`/v1/chat/completions`
- **Method**：`POST`
- **协议**：HTTPS + Server-Sent Events (`Content-Type: text/event-stream`)

## 3. 鉴权

- 通过 HTTP header `Authorization: Bearer ${DEEPSEEK_API_KEY}`
- API Key 仅从环境变量 `DEEPSEEK_API_KEY` 读取（FR-022），**不得**进入任何配置文件或日志

## 4. 请求 schema

```json
{
  "model": "deepseek-chat",
  "stream": true,
  "messages": [
    {"role": "system", "content": "<拼装后的 system prompt，含术语表>"},
    {"role": "user", "content": "<历史第 N-7 句源语言>"},
    {"role": "assistant", "content": "<历史第 N-7 句目标语言译文>"},
    "...（FR-012 滚动 8 句历史）...",
    {"role": "user", "content": "<当前 partial 或 final 源语言>"}
  ],
  "temperature": 0.3,
  "max_tokens": 256,
  "top_p": 0.9
}
```

**字段约束**：

- `model`：默认 `deepseek-chat`；可在 `config.toml` 切到 `deepseek-coder`（不推荐）；**禁止** `deepseek-reasoner`（首 token 慢 1.5×）
- `stream`：**必须** `true`
- `messages`：长度 ≤ 18（system + 8 对历史 + 1 当前）
- `temperature`：0.3（实验测得译文稳定性最佳）
- `max_tokens`：256（普通商务对话单句足够）

## 5. system prompt 拼装契约

由 `mt/prompt.py::build_system_prompt(direction, glossary)` 在会话启动时一次性合成：

```text
你是专业商务同声传译。请将下列{源语言}文本翻译为流畅自然的{目标语言}，保留专有名词、英文缩写、数字、日期、金额的原始或常见映射。

专有名词术语表（必须严格使用）：
- {zh1} ↔ {en1}（备注：{note1}）
- {zh2} ↔ {en2}
... （遍历所有 GlossaryEntry，最多 200 条）

规则：
1. 输出仅含译文，不要解释、不要前缀、不要引号包裹。
2. 流式输出，不必等到完整一句再开始。
3. 数字、日期、金额、英文缩写（如 SDK / API / K8s）保留原写法。
4. 若输入是部分识别（partial），请尽力翻译可见部分；若后续修正会覆盖你之前的输出，请保持翻译稳定不要剧烈跳变。
```

**变量**：

- `{源语言}` / `{目标语言}`：根据 `AudioDirection` 决定（上行 = 中→英；下行 = 英→中）
- `{zh1, en1, note1, ...}`：来自 `GlossaryEntry` 列表

## 6. 流式响应 schema

每个 SSE 事件 `data:` 行为一个 JSON：

```json
{
  "id": "chatcmpl-...",
  "object": "chat.completion.chunk",
  "created": 1730707200,
  "model": "deepseek-chat",
  "choices": [{
    "index": 0,
    "delta": {"content": "<token 文本>"},
    "finish_reason": null
  }]
}
```

**终止信号**：单独一行 `data: [DONE]`。

## 7. 客户端解析契约

```python
async def stream_translate(
    source_text: str,
    direction: AudioDirection,
    context_window: list[tuple[str, str]],   # (source, target) 对
    glossary_prompt: str,                     # 已预拼装好的 system prompt
) -> AsyncIterator[StreamEvent]:
    """
    Yields StreamEvent in order:
      StreamEvent(kind=FIRST_TOKEN, token=str, latency_ms=int)
      StreamEvent(kind=DELTA, token=str)
      ... 多次 DELTA ...
      StreamEvent(kind=COMPLETED, full_text=str, total_latency_ms=int)

    若失败抛出 DeepSeekError，由调用方走 FR-018 退避重连。
    """
```

## 8. 错误码契约

| HTTP | 错误类型 | 客户端动作 |
|------|---------|-----------|
| 200 | 正常 SSE | 解析 |
| 400 | `invalid_request_error` | 不重试，抛 `UserFacingError`（"请求格式错误，请检查 system prompt 与 messages"） |
| 401 | `authentication_error` | 不重试，抛 `UserFacingError`（"API Key 无效，请在环境变量中更新 DEEPSEEK_API_KEY"） |
| 402 | `insufficient_quota` | 不重试，抛 `UserFacingError`（"DeepSeek 配额耗尽，请充值或更换 API Key"） |
| 429 | `rate_limit_exceeded` | 指数退避重试（250 / 500 / 1000 / 2000 / 4000 ms），最多 5 次 |
| 500 / 502 / 503 / 504 | server error | 同上指数退避 |
| 网络超时（30 s） | timeout | 同上指数退避 |
| 连续 ≥ 30 秒未恢复 | persistent unavailable | FR-019：停止该方向 + 用户提示 |

## 9. 性能 SLA（来自宪章 IV）

| 指标 | 预算 | 测量位置 | 包含/排除 |
|------|------|----------|-----------|
| 首 token 延迟 | p50 ≤ 400 ms / p95 ≤ 800 ms | `LatencySample(stage=MT_FIRST_TOKEN)` | **包含**：DNS 查询（首次后被复用 cache）、TLS 1.3 握手（首次后 keep-alive 复用）、SSE 解析、HTTP/2 帧分发；**排除**：用户网络抖动期重连（由 FR-018 退避独立预算覆盖）|
| 整段延迟（30–80 字英文输出） | p50 ≤ 1500 ms / p95 ≤ 2500 ms | `LatencySample(stage=MT_COMPLETED)` | 同上；与宪章 IV「LLM 翻译整段 ≤ 1.5 s」严格对齐——本预算包含网络 RTT 与 SSE 解析全部开销，**未**单独剥离纯模型推理时间 |
| 重试退避总耗时 | ≤ 8 s（5 次累计：250+500+1000+2000+4000 = 7.75 s）| 客户端内部计时 | 与 SC-006「5 秒内看到错误提示」**解耦**：状态面板必须在第 1 次失败检测瞬间立即推送可视提示，**不**等到退避序列结束 |

## 10. 契约测试要求（宪章 II）

`tests/contract/test_deepseek_streaming.py` 必须覆盖：

- ✅ 正常 streaming 响应（fixture 录制的 200 OK SSE）
- ✅ 401 / 402 / 429 / 5xx 各错误码的客户端处理
- ✅ 网络中途断开（ConnectionResetError）
- ✅ SSE 格式异常（缺失 `data:` 前缀、JSON 解析失败）
- ✅ `[DONE]` 终止符正常处理
- ✅ 100% 分支覆盖（重试、超时、错误分类）

## 11. 安全 / 隐私

- **不得**在请求中包含原始音频字节
- **不得**在日志中记录完整 user message（仅记 hash + 字数）
- **不得**在错误堆栈中泄露 API Key（`httpx` 日志级别 ≥ INFO 时已自动屏蔽）

## 12. 版本固定策略

- `model = "deepseek-chat"`：DeepSeek 同模型 ID 升级时（如 V3 → V4）行为可能变化；CI 每周跑一次 BM-5（术语表注入测试）侦测回归
- 客户端代码兼容 `chat.completion.chunk` schema 至少 v1.x，向前不兼容时锁定 SDK 版本
