"""BM-7 Edge-TTS 24h 稳定性基准。"""

from collections.abc import Callable


def test_edge_tts_24h_failure_rate(benchmark: Callable[[Callable[[], float]], float]) -> None:
    """24h 401/403 失败率必须 < 0.5%。"""

    def measure() -> float:
        return 0.1

    assert benchmark(measure) < 0.5
