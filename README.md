# Teams 实时双向语音同传桥

本项目实现 macOS 上的 Microsoft Teams 双向中英实时语音同传桥。v1 使用 BlackHole 2ch、
Whisper.cpp、DeepSeek streaming 与 Edge-TTS 组成低成本同传管线，并提供 CLI 与本地 Web
控制台。

## 快速开始

完整流程见
[`specs/001-teams-voice-interpreter/quickstart.md`](specs/001-teams-voice-interpreter/quickstart.md)。

```bash
uv sync --extra dev
scripts/install-blackhole.sh
cp config.example.toml config.toml
$EDITOR config.toml
tvi doctor --mode realtime --confirm-teams-route
tvi serve
tvi say "你好，我们开始会议。"
tvi ptt --seconds 3 --target blackhole
tvi listen --target default --direction uplink --chunks 3
tvi duplex
```

Web 控制台默认绑定 `http://127.0.0.1:8765`。

`config.toml` 用于保存本机 DeepSeek API Key，已被 `.gitignore` 忽略；也可继续使用
`DEEPSEEK_API_KEY` 环境变量，环境变量优先级更高。

`tvi doctor --mode realtime` 是进入 Teams 前的硬门禁：设备、凭证、Teams 路由、
Edge-TTS 解码和音频写出任一项未过，都会以非 0 退出。`tvi listen` 用于单向本地校准，
`tvi duplex` 用于真实双向：默认麦克风中文上行到上行虚拟设备，Teams 扬声器英文从下行
虚拟设备进入程序，再把中文译音写到默认输出。正式会议必须使用两路不同虚拟设备，避免回灌。

## 核心命令

```bash
tvi doctor
tvi serve
tvi say "你好，我们开始会议。" --target blackhole
tvi ptt --seconds 3 --target blackhole
tvi listen --target default --direction uplink
tvi duplex
tvi start
tvi pause
tvi resume
tvi status
tvi stop
```

## 监管严格场景免责声明

发生了什么：本工具面向普通商务交流辅助场景，v1 **不**声明适用于医疗、律师、政府、金融、
HR 绩效谈判等监管严格或高风险场景，也不替代人工专业译员或正式会议记录。

下一步如何做：如会议涉及上述场景，请停止使用本工具作为唯一翻译来源，并改用符合所在组织
合规要求的人工同传、官方认证服务或经法务 / 合规团队批准的方案。

## 宪章合规性说明

| 原则 | 状态 |
|------|------|
| I. 代码质量 | OK：`ruff` / `mypy` / `radon` 纳入 `make lint` 与 CI |
| II. 测试纪律 | OK：阶段 2 TDD 证据见 `specs/001-teams-voice-interpreter/tdd-evidence.md` |
| III. UX 一致性 | OK：用户可见错误走 `UserFacingError` 两段式与共享中文文案 |
| IV. 性能要求 | OK：BM-1..13 + BM-10D 入口见 `tests/perf/`，报告见 `perf-report.md` |

## 开发命令

```bash
make lint
make typecheck
make test
make benchmark
make coverage
```

## 贡献指南

- 行为代码遵守 TDD：先写失败测试，再实现，再重构。
- 新增用户可见文案必须进入 `src/teams_voice_interpreter/glossary/i18n/zh-CN.toml`。
- 新增音频、ASR、MT、TTS 路径必须补充 `tests/perf/` benchmark 并更新 `perf-report.md`。
- PR 必须链接对应 `spec.md`、`plan.md`、`tasks.md`。

## License

当前仓库未声明开源许可证；除非后续补充 License 文件，否则按私有项目处理。
