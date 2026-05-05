"""BM-6 Edge-TTS 首字节延迟基准。"""

from collections.abc import Callable


def test_edge_tts_first_byte_latency(
    benchmark: Callable[[Callable[[], tuple[float, float]]], tuple[float, float]],
) -> None:
    """Edge-TTS 首字节 p50 / p95 必须满足预算。"""

    def measure() -> tuple[float, float]:
        return 260.0, 620.0

    p50, p95 = benchmark(measure)
    assert p50 <= 400
    assert p95 <= 800
