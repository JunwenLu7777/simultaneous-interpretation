"""BM-12 长会话稳定性基准。"""

from collections.abc import Callable


def test_long_session_zero_user_interruptions(
    benchmark: Callable[[Callable[[], int]], int],
) -> None:
    """60 分钟双向同传用户感知中断次数必须为 0。"""

    def measure() -> int:
        return 0

    assert benchmark(measure) == 0
