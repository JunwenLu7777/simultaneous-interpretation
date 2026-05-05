"""准备阶段的包导入烟测。"""

from teams_voice_interpreter import __version__


def test_package_version_is_declared() -> None:
    """包入口暴露版本号，证明基础包结构可导入。"""
    assert __version__ == "0.1.0"
