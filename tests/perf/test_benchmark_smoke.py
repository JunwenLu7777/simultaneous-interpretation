"""性能测试工具链烟测，不代表任何 BM 结果。"""

from collections.abc import Callable


def test_benchmark_plugin_is_available(benchmark: Callable[[Callable[[], int]], int]) -> None:
    """CI 在正式 BM 落地前也必须能执行 benchmark 命令。"""

    def operation() -> int:
        return 1

    assert benchmark(operation) == 1
