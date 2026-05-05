# AGENTS.md

本文件是 Codex 在本仓库工作的项目级指引，作用范围为仓库根目录及其所有子目录。

优先级说明：

- 系统 / developer / 用户当轮明确指令优先级最高。
- `.specify/memory/constitution.md` 是项目宪章；涉及代码质量、测试纪律、UX 一致性、性能预算时，以宪章为准。
- 本文件与 `CLAUDE.md` 共同约束 AI agent 行为；涉及输出语言、产物形式、spec-kit 工件语言与反模式时，以本文件和 `CLAUDE.md` 为准。
- 若本文件、`CLAUDE.md` 与宪章出现冲突，必须先指出冲突，再按上述优先级执行；不得静默绕过。

## 1. 文档与产物语言（强约束）

所有 spec-kit 相关命令或工作流产出的工件，必须使用简体中文。包括但不限于：

- `specs/<feature>/spec.md`
- `specs/<feature>/plan.md`
- `specs/<feature>/tasks.md`
- `specs/<feature>/checklists/*.md`
- `specs/<feature>/research.md`
- `specs/<feature>/data-model.md`
- `specs/<feature>/quickstart.md`
- `specs/<feature>/perf-report.md`
- `specs/<feature>/contracts/*.md`
- `tests/` 中由命令自动生成的人类可读说明
- Codex 对用户的 spec-kit 工作流最终回复

语言规则：

- 顶级标题、章节标题、用户故事、验收场景、功能需求、成功标准、假设、待澄清事项、关键实体描述、表格表头、列表项、checklist 项、最终总结文字，必须使用简体中文。
- 技术专有名词、API 名、协议名、SDK 名、库名、文件路径、命令名、环境变量名、Git 分支名、代码标识符，必须保留原文形式。
- RFC 2119 关键词在中文正文中渲染为「必须 / 不得 / 应当 / 不应当 / 可」，并用粗体强调。
- 文件名与目录名保持 ASCII，例如 `spec.md`、`plan.md`、`001-teams-voice-interpreter/`。
- 代码、命令、JSON / YAML 配置块保留原文，不翻译关键字与字段名；可在前后用中文解释。
- 测试用例的 `it("...")` / `describe("...")` 默认使用中文；仅当测试框架对非 ASCII 测试名存在兼容性问题时，才降级为英文。

不得以「工具模板需要英文标题」为由保留英文章节标题。若工具或模板与本指令冲突，应优先调整工具或模板。

## 2. Codex 工作流映射

用户提到 `speckit.specify`、`speckit.clarify`、`speckit.plan`、`speckit.tasks`、`speckit.checklist`、`speckit.analyze`、`speckit.implement`、`speckit.constitution`、`speckit.taskstoissues` 时，Codex 应使用本仓库已安装的对应 Speckit skill：

- `speckit.specify` -> `speckit-specify`
- `speckit.clarify` -> `speckit-clarify`
- `speckit.plan` -> `speckit-plan`
- `speckit.tasks` -> `speckit-tasks`
- `speckit.checklist` -> `speckit-checklist`
- `speckit.analyze` -> `speckit-analyze`
- `speckit.implement` -> `speckit-implement`
- `speckit.constitution` -> `speckit-constitution`
- `speckit.taskstoissues` -> `speckit-taskstoissues`

执行这些 workflow 前，先读取对应 skill 的 `SKILL.md`，并遵守其只读 / 可写边界。例如 `speckit-analyze` 是严格只读分析，不得修改文件。

## 3. 项目宪章与质量门禁

常用路径：

- 宪章：`.specify/memory/constitution.md`
- spec / plan / tasks 模板：`.specify/templates/`
- spec-kit 脚本：`.specify/scripts/bash/`
- 当前 feature 工件：`specs/<NNN-feature-slug>/`

修改 spec-kit 模板、宪章、`CLAUDE.md` 或本文件时，必须检查是否会破坏中文产物约束。

实现任务时，必须遵守宪章质量门禁：

- 代码质量：类型注解、模块 docstring、lint / format / typecheck 零警告、圈复杂度与文件长度约束。
- 测试纪律：行为代码先写测试，覆盖率门禁按宪章执行。
- UX 一致性：用户可见消息统一术语，错误提示采用「发生了什么 + 用户下一步可以做什么」两段式。
- 性能要求：音频、ASR、MT、TTS 路径必须有可复现 benchmark，并写入 feature 的 `perf-report.md`。

## 4. 明确禁止的反模式

- 在本仓库的 `spec.md` / `plan.md` / `tasks.md` 中使用纯英文章节标题，例如 `## Requirements`、`## Success Criteria`。必须改用中文，例如 `## 需求`、`## 成功标准`。
- 在中文文档中保留未翻译占位符，例如 `[Brief Title]`、`[DATE]`、`Why this priority`。必须翻译或填入真实内容。
- 在中文 spec 中使用 `Given / When / Then` 而不本地化为「假设 / 当 / 那么」。
- 为了省事使用机翻直译，导致同一术语在 spec、plan、tasks、checklist、contracts 之间出现多种译法。
- 在未核验宪章、模板和相关 feature 工件的情况下，声称某个 spec-kit 产物已经合规。

## 5. Codex 执行约定

- 先读源文件，再判断；不要从文件名、提交信息或摘要推断当前状态。
- 审查 / analyze / checklist 类任务默认只读，除非用户明确要求修复。
- 用户要求生成或修改工件时，保持 diff 小而可审阅；不要顺手修改无关文件。
- 默认不提交、不 push、不创建 PR，除非用户明确要求。
- 最终回复应说明改了哪些文件、依据是什么、是否运行了验证；spec-kit 相关回复使用简体中文。
