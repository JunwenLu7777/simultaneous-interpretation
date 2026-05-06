# 性能基线报告：Teams 实时双向语音同传桥（macOS）

**日期**：2026-05-05
**Git commit**：当前 HEAD；本报告不内嵌自引用提交哈希，使用 `git log -1 --oneline` 核验
**硬件**：本地模拟基准；真实 BlackHole / Whisper 模型 / Edge-TTS 外网基准待发布前复跑
**总体结论**：14 项 BM（BM-1..13 + BM-10D）均有可执行测试入口并在当前模拟基准下通过。当前报告用于实现期门禁闭合；发布前必须在真实 M2 Pro 16 GB / macOS 13+ / Wi-Fi ≥ 50 Mbps 环境复跑并替换模拟数据。`--online-asr` 实验路径不计入下表通过项；2026-05-06 本机探针显示该路径尚未产生可交付的低延迟收益，详见「Online ASR 实验探针」。

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

## Online ASR 实验探针

**日期**：2026-05-06
**音频**：macOS `say -v Tingting` 生成 12.89 秒中文商务长句 WAV，16 kHz mono PCM
**命令入口**：`uv run --extra dev scripts/probe_online_asr.py /tmp/tvi_probe.wav --expected-text ... --max-first-partial-s ... --max-cer ... --proof-json /tmp/online-asr-proof.json`
**计时口径**：2026-05-06 对抗审查后，探针的 partial 时间按「实时音频到达下界 + 同步 ASR 重跑耗时」计算，不再使用离线快速喂完整段音频的偏乐观耗时。

| 模型 / 参数 | ASR 重跑次数 | 首个 stable partial | 首个可翻译 stable partial | 首个 final 可确认可翻译 stable partial | final ASR | CER | 结论 |
|-------------|--------------|---------------------|----------------------------|----------------------------------------|-----------|-----|------|
| `small-q5_1`, `step_ms=300` | 43 | 0.79 s | 3.49 s | n/a | 0.40 s | 0.107 | Fail：提前 partial 未被 final 确认，不能降低首段播出延迟 |
| `small-q5_1`, `step_ms=600` | 22 | 1.55 s | 3.90 s | n/a | 0.65 s | 0.107 | Fail：调大 step 仍无 final 可确认 partial |
| `small-q5_1`, `step_ms=900` | 15 | 4.49 s | 5.18 s | n/a | 0.50 s | 0.107 | Fail：调大 step 仍无 final 可确认 partial，且首个 stable partial 已变慢 |
| `large-v3-turbo-q5_0`, `step_ms=300` | 43 | 2.22 s | 8.83 s | 8.83 s | 1.08 s | 0.107 | Fail：有可确认 partial，但确认点远超低延迟预算 |

**对抗结论**：当前 `--online-asr` 是通过高频重跑本地 Whisper one-shot 模拟 partial，不是可交付的真正 streaming ASR。默认不得让 stable partial 提前调用 MT/TTS；只有先用探针 proof 证明「final 可确认可翻译 stable partial」和 CER 同时达标后，才可显式启用 `--online-asr-early-prepare --low-latency-proof <path>`。继续压低延迟应接入真正 sliding / partial ASR 引擎，而不是继续调 `step_ms` 或换大模型。

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
