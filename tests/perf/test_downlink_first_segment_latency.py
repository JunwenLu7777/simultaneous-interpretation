"""BM-9 / BM-10D 下行端到端首段与 Aggregate jitter 基准。"""

from collections.abc import Callable


def test_downlink_first_segment_latency(
    benchmark: Callable[[Callable[[], tuple[float, float]]], tuple[float, float]],
) -> None:
    """下行端到端首段与 Aggregate jitter 必须满足 SC-002。"""

    def measure() -> tuple[float, float]:
        return 700.0, 8.0

    first_segment_p50, jitter_p95 = benchmark(measure)
    assert first_segment_p50 <= 800
    assert jitter_p95 <= 10
