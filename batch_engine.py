from __future__ import annotations

from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Event

from models import RegistrationData, RegistrationResult

RETRYABLE_STAGES = {
    "browser_start",
    "email_request",
    "mail",
    "network",
    "usps_service_unavailable",
    "usps_service_timeout",
}
MAX_BATCH_WORKERS = 5


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
    max_workers: int = 1,
) -> dict[int, RegistrationResult]:
    workers = max(1, min(int(max_workers), MAX_BATCH_WORKERS, len(items) or 1))
    if workers == 1:
        return _process_sequential(
            items,
            run_one,
            retries,
            delay_seconds,
            stop_event,
            on_result,
            on_attempt,
            on_prepare_attempt,
            retry_backoff_seconds,
        )

    results: dict[int, RegistrationResult] = {}
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="USPSBrowser") as executor:
        futures = {
            executor.submit(
                _process_concurrent_item,
                position,
                len(items),
                row_index,
                data,
                retries,
                delay_seconds,
                stop_event,
                on_result,
                on_attempt,
                on_prepare_attempt,
                run_one,
                retry_backoff_seconds,
            ): (position, row_index)
            for position, (row_index, data) in enumerate(items)
        }
        for future in as_completed(futures):
            position, row_index = futures[future]
            result = future.result()
            results[row_index] = result
    return results


def _process_concurrent_item(
    position: int,
    total_items: int,
    row_index: int,
    data: RegistrationData,
    retries: int,
    delay_seconds: float,
    stop_event: Event,
    on_result: Callable[[int, RegistrationResult], None] | None,
    on_attempt: Callable[[int, int, int], None] | None,
    on_prepare_attempt: Callable[[int, RegistrationData, int], None] | None,
    run_one: Callable[[RegistrationData], RegistrationResult],
    retry_backoff_seconds: float,
) -> RegistrationResult:
    result = _process_one(
        row_index,
        data,
        retries,
        stop_event,
        on_attempt,
        on_prepare_attempt,
        run_one,
        retry_backoff_seconds,
    )
    if on_result:
        on_result(row_index, result)
    if position < total_items - 1 and delay_seconds > 0:
        stop_event.wait(delay_seconds)
    return result


def _process_sequential(
    items: Sequence[tuple[int, RegistrationData]],
    run_one: Callable[[RegistrationData], RegistrationResult],
    retries: int,
    delay_seconds: float,
    stop_event: Event,
    on_result: Callable[[int, RegistrationResult], None] | None,
    on_attempt: Callable[[int, int, int], None] | None,
    on_prepare_attempt: Callable[[int, RegistrationData, int], None] | None,
    retry_backoff_seconds: float,
) -> dict[int, RegistrationResult]:
    results = {}
    for position, (row_index, data) in enumerate(items):
        result = _process_one(
            row_index,
            data,
            retries,
            stop_event,
            on_attempt,
            on_prepare_attempt,
            run_one,
            retry_backoff_seconds,
        )
        results[row_index] = result
        if on_result:
            on_result(row_index, result)
        if position < len(items) - 1 and delay_seconds > 0:
            stop_event.wait(delay_seconds)
    return results


def _process_one(
    row_index: int,
    data: RegistrationData,
    retries: int,
    stop_event: Event,
    on_attempt: Callable[[int, int, int], None] | None,
    on_prepare_attempt: Callable[[int, RegistrationData, int], None] | None,
    run_one: Callable[[RegistrationData], RegistrationResult],
    retry_backoff_seconds: float,
) -> RegistrationResult:
    total_attempts = max(1, retries + 1)
    if stop_event.is_set():
        return RegistrationResult.stopped("queued")

    result = RegistrationResult.failed("queued", "尚未执行")
    for attempt in range(1, total_attempts + 1):
        if stop_event.is_set():
            return RegistrationResult.stopped("retry_wait")
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
                return RegistrationResult.stopped("retry_wait")
    return result
