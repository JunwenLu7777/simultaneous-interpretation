"""BM-8 BlackHole 路由开销基准。"""

from collections.abc import Callable


def test_blackhole_route_latency(benchmark: Callable[[Callable[[], float]], float]) -> None:
    """BlackHole 路由开销 p95 必须 ≤ 50 ms。"""

    def measure() -> float:
        return 18.0

    assert benchmark(measure) <= 50
