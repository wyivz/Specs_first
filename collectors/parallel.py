from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, TypeVar

T = TypeVar("T")


def run_platform_tasks(
    tasks: list[tuple[str, Callable[[], T]]],
    *,
    enabled: bool = True,
    max_workers: int | None = None,
) -> list[T]:
    """Run independent platform collectors; safe when each task uses per-platform rate limits."""
    if not tasks:
        return []
    if not enabled or len(tasks) == 1:
        return [fn() for _, fn in tasks]

    workers = max_workers or min(len(tasks), 4)
    ordered: list[T | None] = [None] * len(tasks)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_to_index = {pool.submit(fn): index for index, (_, fn) in enumerate(tasks)}
        for future in as_completed(future_to_index):
            index = future_to_index[future]
            ordered[index] = future.result()
    return list(ordered)
