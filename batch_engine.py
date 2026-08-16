from __future__ import annotations

from collections.abc import Callable, Sequence
from threading import Event

from models import RegistrationData, RegistrationResult

RETRYABLE_STAGES = {
    "browser_start",
    "email_request",
    "mail",
    "network",
}


def process_batch(
    items: Sequence[tuple[int, RegistrationData]],
    run_one: Callable[[RegistrationData], RegistrationResult],
    retries: int,
    delay_seconds: float,
    stop_event: Event,
    on_result: Callable[[int, RegistrationResult], None] | None = None,
    on_attempt: Callable[[int, int, int], None] | None = None,
    on_prepare_attempt: Callable[[int, RegistrationData, int], None] | None = None,
    retry_backoff_seconds: float = 1,
) -> dict[int, RegistrationResult]:
    results: dict[int, RegistrationResult] = {}
    total_attempts = max(1, retries + 1)
    for position, (row_index, data) in enumerate(items):
        if stop_event.is_set():
            stopped = RegistrationResult.stopped("queued")
            results[row_index] = stopped
            if on_result:
                on_result(row_index, stopped)
            continue

        result = RegistrationResult.failed("queued", "尚未执行")
        for attempt in range(1, total_attempts + 1):
            if stop_event.is_set():
                result = RegistrationResult.stopped("retry_wait")
                break
            try:
                if on_prepare_attempt:
                    on_prepare_attempt(row_index, data, attempt)
                if on_attempt:
                    on_attempt(row_index, attempt, total_attempts)
                result = run_one(data)
            except Exception as exc:
                result = RegistrationResult.failed("worker", str(exc))
            if result.status in ("success", "stopped"):
                break
            if result.stage not in RETRYABLE_STAGES:
                break
            if attempt < total_attempts and retry_backoff_seconds > 0:
                backoff = min(30, retry_backoff_seconds * (2 ** (attempt - 1)))
                if stop_event.wait(backoff):
                    result = RegistrationResult.stopped("retry_wait")
                    break

        results[row_index] = result
        if on_result:
            on_result(row_index, result)
        if position < len(items) - 1 and delay_seconds > 0:
            stop_event.wait(delay_seconds)
    return results
