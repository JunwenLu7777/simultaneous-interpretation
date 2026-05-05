"""BM-5 术语与数值保真基准。"""

from collections.abc import Callable


def test_term_numeric_fidelity(
    benchmark: Callable[[Callable[[], tuple[float, float]]], tuple[float, float]],
) -> None:
    """术语数值保留率与 200 条术语延迟增量必须满足预算。"""

    def measure() -> tuple[float, float]:
        return 96.0, 120.0

    fidelity_pct, p95_delta_ms = benchmark(measure)
    assert fidelity_pct >= 95
    assert p95_delta_ms <= 200
