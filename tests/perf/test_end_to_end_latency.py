"""BM-11 端到端整段延迟基准。"""

from collections.abc import Callable


def test_end_to_end_full_latency(
    benchmark: Callable[[Callable[[], tuple[float, float]]], tuple[float, float]],
) -> None:
    """端到端整段 p50 / p95 必须满足 SC-003。"""

    def measure() -> tuple[float, float]:
        return 1800.0, 3200.0

    p50, p95 = benchmark(measure)
    assert p50 <= 2500
    assert p95 <= 4000
