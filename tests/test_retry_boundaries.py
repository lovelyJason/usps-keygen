import threading

from batch_engine import process_batch
from models import RegistrationData, RegistrationResult
from retry_policy import runnable_indices


def test_email_verification_failure_is_not_retried_after_submit():
    attempts = 0

    def run_one(_data):
        nonlocal attempts
        attempts += 1
        return RegistrationResult.failed("email_verification", "rejected")

    process_batch(
        [(0, RegistrationData(username="user"))],
        run_one,
        retries=3,
        delay_seconds=0,
        stop_event=threading.Event(),
    )
    assert attempts == 1


def test_mail_consumption_failure_is_not_retried_as_registration():
    attempts = 0

    def run_one(_data):
        nonlocal attempts
        attempts += 1
        return RegistrationResult.failed("mail_consumption", "consume failed")

    process_batch(
        [(0, RegistrationData(username="user"))],
        run_one,
        retries=3,
        delay_seconds=0,
        stop_event=threading.Event(),
    )
    assert attempts == 1


def test_post_submit_mail_timeout_is_not_retried_as_registration():
    attempts = 0

    def run_one(_data):
        nonlocal attempts
        attempts += 1
        return RegistrationResult.failed("post_submit_mail", "timeout")

    process_batch(
        [(0, RegistrationData(username="user"))],
        run_one,
        retries=3,
        delay_seconds=0,
        stop_event=threading.Event(),
    )
    assert attempts == 1


def test_post_submit_mail_stop_is_not_runnable_after_restart():
    results = [RegistrationResult.stopped("post_submit_mail")]

    assert runnable_indices(results) == []


def test_email_verification_stop_is_not_runnable_after_restart():
    results = [RegistrationResult.stopped("email_verification")]

    assert runnable_indices(results) == []


def test_post_submit_browser_failure_is_not_runnable_after_restart():
    results = [RegistrationResult.failed("submission_unknown", "browser closed")]

    assert runnable_indices(results) == []


def test_failed_complete_state_is_not_runnable_after_restart():
    results = [RegistrationResult.failed("complete", "page closed")]

    assert runnable_indices(results) == []
