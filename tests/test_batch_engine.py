import threading

from batch_engine import process_batch
from models import RegistrationData, RegistrationResult


def row(username):
    return RegistrationData(username=username)


def test_failed_row_does_not_stop_following_rows():
    calls = []

    def run_one(data):
        calls.append(data.username)
        if data.username == "bad":
            return RegistrationResult.failed("submit", "rejected")
        return RegistrationResult.success("complete", "https://reg.usps.com/done")

    results = process_batch(
        [(0, row("bad")), (1, row("good"))],
        run_one,
        retries=0,
        delay_seconds=0,
        stop_event=threading.Event(),
    )

    assert calls == ["bad", "good"]
    assert results[0].status == "failed"
    assert results[1].status == "success"


def test_failed_row_retries_only_that_row():
    attempts = {"flaky": 0}

    def run_one(data):
        attempts[data.username] += 1
        if attempts[data.username] == 1:
            return RegistrationResult.failed("mail", "timeout")
        return RegistrationResult.success("complete", "https://reg.usps.com/done")

    results = process_batch(
        [(5, row("flaky"))],
        run_one,
        retries=1,
        delay_seconds=0,
        stop_event=threading.Event(),
        retry_backoff_seconds=0,
    )

    assert attempts["flaky"] == 2
    assert results[5].status == "success"


def test_explicit_usps_service_timeout_is_safely_retried():
    attempts = 0

    def run_one(_data):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return RegistrationResult.failed("usps_service_timeout", "account not created")
        return RegistrationResult.success("complete", "https://reg.usps.com/done")

    results = process_batch(
        [(0, row("flaky-usps"))],
        run_one,
        retries=1,
        delay_seconds=0,
        stop_event=threading.Event(),
        retry_backoff_seconds=0,
    )

    assert attempts == 2
    assert results[0].status == "success"


def test_pre_stopped_batch_does_not_call_runner():
    stop = threading.Event()
    stop.set()
    called = False

    def run_one(_data):
        nonlocal called
        called = True
        return RegistrationResult.success("complete", "")

    results = process_batch([(0, row("unused"))], run_one, 0, 0, stop)
    assert not called
    assert results[0].status == "stopped"


def test_permanent_identity_failure_is_not_retried():
    attempts = 0

    def run_one(_data):
        nonlocal attempts
        attempts += 1
        return RegistrationResult.failed("identity_verification", "rejected")

    result = process_batch(
        [(0, row("business"))], run_one, retries=3, delay_seconds=0, stop_event=threading.Event()
    )
    assert attempts == 1
    assert result[0].status == "failed"


def test_prepare_attempt_runs_before_each_runner_call():
    seen = []

    def prepare(_index, data, attempt):
        data.email = f"attempt-{attempt}@velydora.com"

    def run_one(data):
        seen.append(data.email)
        if len(seen) == 1:
            return RegistrationResult.failed("mail", "timeout")
        return RegistrationResult.success("complete", "done")

    process_batch(
        [(0, row("user"))],
        run_one,
        retries=1,
        delay_seconds=0,
        stop_event=threading.Event(),
        retry_backoff_seconds=0,
        on_prepare_attempt=prepare,
    )
    assert seen == ["attempt-1@velydora.com", "attempt-2@velydora.com"]
