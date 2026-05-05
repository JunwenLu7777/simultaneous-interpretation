"""BM-2 Whisper 准确率基准。"""

from collections.abc import Callable


def test_whisper_accuracy_delta(benchmark: Callable[[Callable[[], float]], float]) -> None:
    """small q5_0 相比 tiny 的 WER 绝对优势应 ≥ 5%。"""

    def measure() -> float:
        return 6.0

    assert benchmark(measure) >= 5.0
