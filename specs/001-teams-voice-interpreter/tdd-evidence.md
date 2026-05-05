# TDD 证据：阶段 2 基础能力

**日期**：2026-05-05

## 失败先行

在创建 T010–T017 测试后、实现 T018–T025 前，执行：

```bash
uv run --extra dev pytest tests/unit/errors tests/unit/glossary tests/unit/config tests/unit/data tests/unit/session tests/unit/audio tests/unit/perf
```

结果：0 个测试收集成功，8 个 collection error；失败原因均为目标实现模块尚不存在：
`errors.py`、`glossary/strings.py`、`config.py`、`data/*`、`session/instance_lock.py`、
`audio/routing.py`、`perf.py`。

## 实现后通过

实现 T018–T025 后，同一命令通过：

```text
28 passed
```

## 结论

T010–T017 符合「先写失败测试，再实现」要求；阶段 2 进入用户故事前仍需以 `make test`
复核完整基础测试集。
