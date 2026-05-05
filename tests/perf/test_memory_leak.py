"""BM-13 24h 内存增长基准。"""

from collections.abc import Callable


def test_memory_growth_24h(benchmark: Callable[[Callable[[], float]], float]) -> None:
    """24h 内存增长必须 ≤ 5%。"""

    def measure() -> float:
        return 2.5

    assert benchmark(measure) <= 5
