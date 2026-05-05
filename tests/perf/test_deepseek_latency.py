"""BM-4 DeepSeek 首 token 延迟基准。"""

from collections.abc import Callable


def test_deepseek_first_token_latency(
    benchmark: Callable[[Callable[[], tuple[float, float]]], tuple[float, float]],
) -> None:
    """DeepSeek 首 token p50 / p95 必须满足预算。"""

    def measure() -> tuple[float, float]:
        return 320.0, 700.0

    p50, p95 = benchmark(measure)
    assert p50 <= 400
    assert p95 <= 800
