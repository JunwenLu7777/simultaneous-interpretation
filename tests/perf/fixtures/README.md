# 性能 fixture 录制规范

本目录用于存放后续 BM 任务需要的本地音频 fixture。真实音频文件较大，默认不提交到仓库；
提交前必须记录来源、SHA256 与生成脚本路径。

| 文件 | 来源 | 用途 | SHA256 |
|------|------|------|--------|
| `conference-cn.wav` | Common Voice 普通话商务语料筛选后拼接 | BM-1 / BM-2 / BM-10 上行基准 | 待生成 |
| `conference-en.wav` | LibriSpeech 英文商务风格子集筛选后拼接 | BM-4 / BM-6 / BM-10D 下行基准 | 待生成 |
| `long-cn-2h.wav` | `conference-cn.wav` 加静音与噪声片段循环拼接 | BM-12 / BM-13 长会话稳定性 | 待生成 |

生成脚本落位于 `scripts/build-perf-fixtures.py`。脚本必须输出每个文件的采样率、声道数、
持续时间与 SHA256，并把同一信息同步写入 `specs/001-teams-voice-interpreter/perf-report.md`。
