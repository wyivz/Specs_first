from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


def retry_call(
    func: Callable[[], T],
    *,
    attempts: int = 3,
    base_delay_seconds: float = 0.6,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
) -> T:
    last_error: BaseException | None = None
    for attempt in range(attempts):
        try:
            return func()
        except retry_on as exc:
            last_error = exc
            if attempt >= attempts - 1:
                break
            time.sleep(base_delay_seconds * (attempt + 1))
    assert last_error is not None
    raise last_error
