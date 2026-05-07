"""首次运行向导与 RT-1..RT-6 自检。"""

from __future__ import annotations

from dataclasses import dataclass

from teams_voice_interpreter.errors import UserFacingError


@dataclass(frozen=True)
class WizardStep:
    """首次运行向导中的一步。"""

    key: str
    title: str
    passed: bool
    next_action: str


class FirstRunWizard:
    """首次运行向导步骤清单。"""

    def __init__(self, checks: dict[str, bool] | None = None) -> None:
        self.checks = checks or {}

    def run(self) -> list[WizardStep]:
        """执行所有向导步骤并返回结果。"""
        return [
            self._step("blackhole", "安装 BlackHole 2ch"),
            self._step("aggregate", "创建 Aggregate Device"),
            self._step("teams_route", "配置 Teams 音频路由"),
            self._step("mic_permission", "授予麦克风权限"),
            self._step("credential", "配置 DeepSeek API 凭证"),
            self._step(
                "piper_models",
                "下载 Piper voice 模型",
                (
                    "请把 en_US-amy-medium.onnx、en_US-amy-medium.onnx.json、"
                    "zh_CN-huayan-medium.onnx、zh_CN-huayan-medium.onnx.json "
                    "下载到 `~/.cache/teams-voice-interpreter/piper-models/`，"
                    "或在 config.toml 中设置 `piper_models_dir` 后重试。"
                ),
            ),
            self._step("glossary", "加载术语表"),
            self._step("disclaimer", "确认监管严格场景免责声明"),
        ]

    def require_passed(self) -> None:
        """任一步失败时抛两段式错误。"""
        failed = [step for step in self.run() if not step.passed]
        if failed:
            first = failed[0]
            raise UserFacingError(
                code=f"wizard.{first.key}_failed",
                what_happened=f"发生了什么：首次运行向导步骤「{first.title}」未通过。",
                next_action=f"下一步如何做：{first.next_action}",
            )

    def _step(self, key: str, title: str, next_action: str | None = None) -> WizardStep:
        passed = self.checks.get(key, True)
        return WizardStep(
            key=key,
            title=title,
            passed=passed,
            next_action=next_action or f"请完成「{title}」后重试。",
        )
