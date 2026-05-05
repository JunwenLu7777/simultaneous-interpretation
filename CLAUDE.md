# CLAUDE.md

> 本文件给所有在本仓库工作的 AI agent（Claude Code、Codex、其他）下达项目级硬指令。
> 优先级高于默认行为，与项目宪章 `.specify/memory/constitution.md` 平级（如有冲突，以宪章为准）。

## 1. 文档与产物语言（强约束）

所有 spec-kit 命令（包括但不限于 `/speckit.specify`、`/speckit.clarify`、`/speckit.plan`、
`/speckit.tasks`、`/speckit.checklist`、`/speckit.analyze`、`/speckit.implement`、
`/speckit.constitution`、`/speckit.taskstoissues`）所产出的全部工件，**必须使用简体中文**。
受此约束的工件包括但不限于：

- `specs/<feature>/spec.md`
- `specs/<feature>/plan.md`
- `specs/<feature>/tasks.md`
- `specs/<feature>/checklists/*.md`
- `specs/<feature>/research.md`、`data-model.md`、`quickstart.md`、`perf-report.md`
- `contracts/`、`tests/` 中由命令自动生成的人类可读说明
- 命令在终端给用户的最终回复

具体语言规范：

- **必须中文**：顶级标题、章节标题（H2 / H3 / H4 / H5）、用户故事、验收场景、功能需求、
  成功标准、假设、待澄清事项、关键实体描述、表格表头、列表项、checklist 项、命令
  最终在终端回复给用户的总结文字。
- **必须保留原文形式**：技术专有名词（API 名、协议名、SDK 名、库名、文件路径、命令名、
  环境变量名、Git 分支名、代码标识符）。
- **RFC 2119 关键词**：将 MUST / MUST NOT / SHOULD / SHOULD NOT / MAY 在中文正文中
  渲染为「必须 / 不得 / 应当 / 不应当 / 可」，并用粗体强调，以保留规范级强约束语义。
- **文件名与目录名**：仍使用 ASCII（如 `spec.md`、`plan.md`、`001-teams-voice-interpreter/`），
  以保证 spec-kit 工具脚本与跨平台文件系统兼容。
- **代码、命令、JSON / YAML 配置块**：保留原文，不得翻译关键字与字段名；可在前后用中文
  补充注释。
- **测试用例的 `it("...")` / `describe("...")`**：默认使用中文，便于阅读；如某测试框架
  对非 ASCII 测试名有兼容性问题再降级为英文。

不得以「H2 标题需要英文以兼容 spec-kit 工具」为由保留英文章节标题——若工具与本指令
冲突，应当先尝试调整工具或脚本，而不是回退到英文。

## 2. 与项目宪章的关系

本文件与 `.specify/memory/constitution.md` 共同构成项目级强约束。两者发生冲突时：

- 涉及代码质量、测试纪律、UX 一致性、性能预算 → 以**宪章**为准。
- 涉及输出语言、产物形式、AI agent 行为约定 → 以**本文件**为准。

新增或修改本文件时，应当在同一次 PR 中检查 `.specify/memory/constitution.md` 与
`.specify/templates/` 下模板，确保不存在与本指令冲突的英文硬编码模板片段。

## 3. 常用工件路径速查

- 宪章：`.specify/memory/constitution.md`
- spec / plan / tasks 模板：`.specify/templates/`
- spec-kit 脚本：`.specify/scripts/bash/`
- 当前 feature 工件：`specs/<NNN-feature-slug>/`

## 4. 反模式（明确禁止）

- 在本仓库的 spec / plan / tasks 中使用纯英文章节标题（如 `## Requirements`、
  `## Success Criteria`）。改用中文（如 `## 需求`、`## 成功标准`）。
- 在中文文档中混入未翻译的占位符（如 `[Brief Title]`、`[DATE]`、`Why this priority`）。
  必须翻译或填入真实内容。
- 在中文 spec 中使用 `Given / When / Then` 而不本地化为「假设 / 当 / 那么」。
- 因「省事」而把翻译降级为机翻直译，导致术语在文档间出现多种译法。术语必须先经过统一
  术语表（项目无术语表时，由首份正式 spec 在 `.specify/memory/glossary.md` 中创建并维护）。
