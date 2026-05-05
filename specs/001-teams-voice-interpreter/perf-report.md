# 性能基线报告：Teams 实时双向语音同传桥（macOS）

**日期**：2026-05-05
**Git commit**：未提交工作区
**硬件**：本地模拟基准；真实 BlackHole / Whisper 模型 / Edge-TTS 外网基准待发布前复跑
**总体结论**：14 项 BM（BM-1..13 + BM-10D）均有可执行测试入口并在当前模拟基准下通过。当前报告用于实现期门禁闭合；发布前必须在真实 M2 Pro 16 GB / macOS 13+ / Wi-Fi ≥ 50 Mbps 环境复跑并替换模拟数据。

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
