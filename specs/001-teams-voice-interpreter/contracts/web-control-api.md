# 契约：本地 Web 控制台 API

**关联**：[plan.md](../plan.md) · [research.md](../research.md) §6 · [spec.md FR-014 / FR-015 / FR-016 / FR-017 / FR-026 / FR-027](../spec.md)
**实现位置**：`src/teams_voice_interpreter/web/{server,routes/*}.py` + `src/teams_voice_interpreter/web/static/`

## 1. 适用范围

- **CLI 与 Web 共享**会话核心，本契约描述 **Web 端用户表面**的 REST + WebSocket 接口
- 用户表面**v1 锁定**：CLI 子命令 + 本地 Web 控制台；不引入 macOS 状态栏 / 终端 TUI / 系统通知

## 2. 服务约束

- **绑定**：`127.0.0.1:8765`（仅 localhost；外部不可达）
- **协议**：HTTP/1.1 + WebSocket（`ws://`，未启 TLS——本地访问不需）
- **CORS**：禁用（仅 same-origin）
- **鉴权**：v1 假定本地访问无需鉴权（`127.0.0.1` 限定）；v1.1 评估 PIN 码方案
- **进程内嵌**：FastAPI 与 CLI 共享 Python 进程；CLI 启动时 `uvicorn` 在同进程 ASGI server 启动

## 3. REST 端点

### 3.1 `POST /api/control/start`

**触发会话启动（FR-014）。**

**请求**：

```json
{}
```

**响应（200 OK）**：

```json
{
  "session_id": "0193bc12-...-7f21",
  "started_at": "2026-05-05T10:30:00.000Z",
  "uplink_enabled": true,
  "downlink_enabled": true,
  "web_port": 8765,
  "glossary_loaded_count": 12
}
```

**响应（409 Conflict — FR-026 单实例）**：

```json
{
  "error_code": "session_already_active",
  "message": "已有活跃会话 SessionId=0193bc12-...，请先在原浏览器或终端执行 stop 操作。",
  "next_action": "在原浏览器点击「停止」按钮或在终端运行 `tvi stop`"
}
```

**响应（428 Precondition Required — wizard 未完成）**：

```json
{
  "error_code": "wizard_incomplete",
  "message": "首次配置向导未完成。",
  "next_action": "请在终端运行 `tvi wizard` 完成 BlackHole 安装、Aggregate Device 创建、API 凭证设置。"
}
```

**响应（503 Service Unavailable — 外部服务）**：

```json
{
  "error_code": "deepseek_unreachable",
  "message": "无法连接到 DeepSeek API（最近一次错误：401 鉴权失败）。",
  "next_action": "请检查环境变量 DEEPSEEK_API_KEY 是否已正确设置。"
}
```

### 3.2 `POST /api/control/pause`

**响应（200 OK）**：

```json
{"session_id": "...", "state": "paused", "paused_at": "..."}
```

**响应（409）**：会话不在 `ACTIVE` 状态。

### 3.3 `POST /api/control/resume`

**响应（200 OK）**：`{"session_id": "...", "state": "active", "resumed_at": "..."}`

### 3.4 `POST /api/control/stop`

**响应（200 OK）**：

```json
{
  "session_id": "...",
  "state": "stopping",
  "stopped_at": "...",
  "duration_sec": 1234.56,
  "export_window_remaining_sec": 30
}
```

**注**：`export_window_remaining_sec` 是 FR-024 规定的"会话停止后内存释放前的导出窗口"，默认 30 秒。窗口内可调用 `/api/export`，过期后导出按钮置灰。

### 3.5 `GET /api/status`

**响应（200 OK）**：

```json
{
  "session": {
    "session_id": "...",
    "state": "active",
    "started_at": "...",
    "uplink_enabled": true,
    "downlink_enabled": true,
    "duration_sec": 123.45
  },
  "latest_uplink": {
    "zh": "我们下一季度计划上线 K8s 1.30。",
    "en": "We plan to roll out K8s 1.30 next quarter.",
    "first_segment_latency_ms": 850,
    "completed_at": "..."
  },
  "latest_downlink": {
    "en": "Sounds great. What's the migration timeline?",
    "zh": "听起来不错。迁移时间表是怎样的？",
    "first_segment_latency_ms": 920,
    "completed_at": "..."
  },
  "latency": {
    "p50_first_segment_ms": 880,
    "p95_first_segment_ms": 1180,
    "p50_e2e_full_ms": 2400,
    "p95_e2e_full_ms": 3850
  },
  "services_health": {
    "deepseek": "healthy",
    "whisper": "healthy",
    "edge_tts": "healthy"
  }
}
```

### 3.6 `POST /api/export` (FR-027)

**请求**：

```json
{
  "session_id": "..."
}
```

**响应（200 OK）**：返回 Markdown 文件下载流，`Content-Disposition: attachment; filename="teams-session-2026-05-05T10-30-00.md"`。

**Markdown 格式**：

```markdown
# Teams 同传会话记录

**SessionId**: `0193bc12-...-7f21`
**开始时间**: 2026-05-05 10:30:00 UTC+8
**停止时间**: 2026-05-05 11:50:24 UTC+8
**总时长**: 1 小时 20 分 24 秒
**上行**: 启用（用户中文 → 远端英文）
**下行**: 启用（远端英文 → 用户中文）
**服务栈版本**: deepseek-chat / whisper-small-q5_0 / edge-tts-7.0.2 / blackhole-2ch

---

## 对话记录

### [10:30:15] 上行（中 → 英）

**原文**：我们下一季度计划上线 K8s 1.30。

**译文**：We plan to roll out K8s 1.30 next quarter.

### [10:30:23] 下行（英 → 中）

**原文**：Sounds great. What's the migration timeline?

**译文**：听起来不错。迁移时间表是怎样的？

...
```

**响应（410 Gone）**：导出窗口已过期，内存已释放。

### 3.7 `GET /api/wizard/status`

返回首次配置向导各步骤的完成状态（RT-1..RT-6）。

## 4. WebSocket 端点

### 4.1 `/ws/status`

**用途**：FR-016 状态面板实时推送（≥ 5 Hz）。

**握手**：

- 客户端：`new WebSocket("ws://localhost:8765/ws/status")`
- 服务端：接受 + 心跳

**服务端推送消息**（每 200 ms 一条）：

```json
{
  "type": "status_update",
  "ts": "2026-05-05T10:30:01.234Z",
  "session_state": "active",
  "duration_sec": 123.4,
  "latency": {
    "p50_first_segment_ms": 880,
    "p95_first_segment_ms": 1180,
    "p50_e2e_full_ms": 2400
  },
  "services_health": {
    "deepseek": "healthy",
    "whisper": "healthy",
    "edge_tts": "healthy"
  }
}
```

**partial / final 段流式推送**（事件驱动，不固定频率）：

```json
{
  "type": "transcript_partial",
  "direction": "uplink",
  "segment_id": "...",
  "text": "我们下一季度计划上线 K8",
  "confidence": 0.65
}
```

```json
{
  "type": "transcript_final",
  "direction": "uplink",
  "segment_id": "...",
  "text": "我们下一季度计划上线 K8s 1.30。",
  "confidence": 0.92,
  "ended_at": "..."
}
```

```json
{
  "type": "translation_first_token",
  "direction": "uplink",
  "segment_id": "...",
  "first_token_ms": 320
}
```

```json
{
  "type": "translation_completed",
  "direction": "uplink",
  "segment_id": "...",
  "target_text": "We plan to roll out K8s 1.30 next quarter.",
  "completed_ms": 1240
}
```

**错误推送（FR-018 / FR-020）**：

```json
{
  "type": "service_error",
  "service": "deepseek",
  "message": "DeepSeek API 暂时不可用，正在重连（第 2 次，下次 1000 ms 后）",
  "next_action": "请稍候。如持续超过 30 秒会自动停止该方向。"
}
```

```json
{
  "type": "subprocess_recovered",
  "service": "whisper.cpp",
  "respawn_count_60s": 1,
  "message": "Whisper.cpp 已崩溃并自动恢复"
}
```

```json
{
  "type": "subprocess_circuit_break",
  "service": "whisper.cpp",
  "message": "Whisper.cpp 在 60 秒内崩溃 3 次，已停止上行同传。",
  "next_action": "建议检查内存压力或在配置中切换到 ggml-tiny 模型重启。"
}
```

## 5. 单页前端契约

### 5.1 `index.html`

- 单文件 HTML + Pico.css 1.5 + HTMX 1.9
- 加载时通过 WebSocket 连接 `/ws/status`，并通过 `htmx` 装饰按钮触发 REST 操作
- 启动时调用 `Notification.requestPermission()` 请求浏览器原生通知权限（用于会议中弹窗，弥补页面被遮蔽时的盲区）

### 5.2 关键 DOM 结构

```html
<header>
  <span id="session-state">空闲</span>
  <span id="duration">--:--:--</span>
  <button hx-post="/api/control/start">开始同传</button>
  <button hx-post="/api/control/pause">暂停</button>
  <button hx-post="/api/control/stop">停止</button>
</header>

<section class="latency-panel">
  <div>首段译音 p50 <span id="lat-fs-p50">--</span> ms · p95 <span id="lat-fs-p95">--</span> ms</div>
  <div>整段端到端 p50 <span id="lat-e2e-p50">--</span> ms · p95 <span id="lat-e2e-p95">--</span> ms</div>
</section>

<section class="services-health">
  <span class="badge" id="health-deepseek">DeepSeek 健康</span>
  <span class="badge" id="health-whisper">Whisper 健康</span>
  <span class="badge" id="health-edge-tts">Edge-TTS 健康</span>
</section>

<section class="transcript-panel">
  <div class="uplink">
    <h3>上行（中 → 英）</h3>
    <div class="latest-zh" id="uplink-latest-zh">--</div>
    <div class="latest-en" id="uplink-latest-en">--</div>
  </div>
  <div class="downlink">
    <h3>下行（英 → 中）</h3>
    <div class="latest-en" id="downlink-latest-en">--</div>
    <div class="latest-zh" id="downlink-latest-zh">--</div>
  </div>
</section>

<button hx-post="/api/export" id="export-btn">导出对话记录</button>

<div id="toast-container"></div>   <!-- 错误两段式提示 -->
```

### 5.3 浏览器通知

- 异常事件（`service_error` / `subprocess_circuit_break`）→ `new Notification(...)` 弹原生通知
- 用户首次启动时引导授权
- **不**使用 macOS 系统通知中心 API（避免触碰原生 App 红线）

## 6. 性能 SLA

| 指标 | 预算 | 测量位置 |
|------|------|----------|
| WebSocket 推送频率 | ≥ 5 Hz（200 ms 间隔） | 服务端定时器 |
| `GET /api/status` 响应延迟 | p95 ≤ 50 ms | 端点本地计时 |
| 面板从识别事件到 DOM 更新 | ≤ 1 s（SC-008） | 前端 measurement |
| 启动 `POST /api/control/start` 到 UI 显示「已收到 / 处理中」 | ≤ 1 s（FR-015 / 宪章 III） | 前端 measurement |

## 7. 契约测试要求

`tests/contract/test_web_control_api.py` 必须覆盖：

- ✅ 全部 REST 端点的 200 / 4xx / 5xx 路径
- ✅ WebSocket 连接、消息 schema 校验、5 Hz 推送频率
- ✅ FR-026 单实例：第二次 start 返回 409
- ✅ FR-027 导出：window 内可下载，过期返回 410
- ✅ 错误两段式（"发生了什么 + 下一步如何做"）格式校验
- ✅ 100% 分支覆盖

## 8. 安全 / 隐私

- 仅绑定 `127.0.0.1`，外部不可达
- **不**记录请求 body 完整内容（仅 hash + 字数）
- WebSocket 不记录推送历史（仅 metrics）
- 静态资源 `index.html` 只读、随包发布

## 9. 版本兼容

- FastAPI 0.115+
- HTMX 1.9（CDN：`https://unpkg.com/htmx.org@1.9.12` 或本地 `static/vendor/htmx.min.js`）
- Pico.css 1.5（同上）
- 浏览器：Safari 16+ / Chrome 110+ / Firefox 110+（要求 WebSocket + Notification API）
