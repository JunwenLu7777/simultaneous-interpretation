"""共享文案表契约测试。"""

from pathlib import Path

from teams_voice_interpreter.glossary.strings import (
    REQUIRED_STRING_KEYS,
    load_strings,
    validate_message_catalog,
)


def test_default_catalog_contains_required_keys() -> None:
    """默认中文文案表必须覆盖所有共享键。"""
    catalog = load_strings()

    assert set(REQUIRED_STRING_KEYS).issubset(catalog)


def test_default_catalog_user_visible_errors_are_two_part() -> None:
    """所有 error.* 文案必须采用两段式。"""
    catalog = load_strings()

    assert validate_message_catalog(catalog) == []


def test_catalog_validation_reports_missing_two_part_shape(tmp_path: Path) -> None:
    """校验器必须能指出缺失两段式的文案。"""
    broken = tmp_path / "zh-CN.toml"
    broken.write_text(
        '[error]\nblackhole_missing = "BlackHole missing"\n',
        encoding="utf-8",
    )

    catalog = load_strings(broken)

    assert validate_message_catalog(catalog) == ["error.blackhole_missing"]
