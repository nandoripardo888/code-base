from collections.abc import Callable
from time import perf_counter_ns


def timed[T](operation: Callable[[], T]) -> tuple[T, int]:
    started = perf_counter_ns()
    value = operation()
    elapsed_ms = max(0, (perf_counter_ns() - started) // 1_000_000)
    return value, elapsed_ms
