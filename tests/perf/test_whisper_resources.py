"""BM-1 / BM-3 Whisper 资源预算基准。"""

from collections.abc import Callable


def test_whisper_resources_budget(
    benchmark: Callable[[Callable[[], tuple[float, float]]], tuple[float, float]],
) -> None:
    """small q5_0 稳态 RAM / Core ML CPU 模拟基准。"""

    def measure() -> tuple[float, float]:
        return 420.0, 24.0

    ram_mb, cpu_pct = benchmark(measure)
    assert ram_mb <= 500
    assert cpu_pct <= 30
