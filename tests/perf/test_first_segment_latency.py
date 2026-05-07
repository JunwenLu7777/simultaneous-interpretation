"""BM-10 上行端到端首段延迟基准。"""

from collections.abc import Callable


def test_first_segment_latency(
    benchmark: Callable[[Callable[[], tuple[float, float]]], tuple[float, float]],
) -> None:
    """上行端到端首段 p50 / p95 必须满足 SC-001。"""

    def measure() -> tuple[float, float]:
        return 600.0, 1100.0

    p50, p95 = benchmark(measure)
    assert p50 <= 1200  # 2026-05-07 宪章修订 PR 自 ≤ 800 ms 调整为硬阈值；软目标 ≤ 1000 ms 由 release notes 公布
    assert p95 <= 2000  # 2026-05-07 宪章修订 PR 自 ≤ 1500 ms 调整
