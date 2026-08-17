import threading
import time
from pathlib import Path

import pytest

import batch_worker
from batch_worker import BatchWorker
from models import RegistrationData, RegistrationResult


def test_final_result_remains_in_memory_when_checkpoint_write_fails(monkeypatch, tmp_path):
    row = RegistrationData(username="user", email="a@b.com")
    pending = RegistrationResult("pending", "queued", "waiting")
    completed = RegistrationResult.success("complete", "https://reg.usps.com/success")
    worker = BatchWorker(
        items=[(0, row)],
        mailbox_base_url="https://mail.example",
        mailbox_token="token",
        mailbox_timeout_seconds=30,
        poll_seconds=1,
        delay_seconds=0,
        retries=0,
        headless=True,
        artifact_dir=Path(tmp_path),
        checkpoint_path=tmp_path / "checkpoint.json",
        all_rows=[row],
        initial_results=[pending],
    )

    def fake_process_batch(*_args, **kwargs):
        kwargs["on_result"](0, completed)
        return {0: completed}

    monkeypatch.setattr(batch_worker, "process_batch", fake_process_batch)
    monkeypatch.setattr(
        batch_worker,
        "save_checkpoint",
        lambda *_args: (_ for _ in ()).throw(OSError("disk full")),
    )

    with pytest.raises(OSError, match="disk full"):
        worker.run()

    assert worker.results == {0: completed}


def test_concurrent_worker_creates_independent_runner_per_row(monkeypatch, tmp_path):
    rows = [
        RegistrationData(username=f"user-{index}", email=f"user-{index}@example.com")
        for index in range(4)
    ]
    pending = [RegistrationResult("pending", "queued", "waiting") for _ in rows]
    runners = []
    threads = set()
    lock = threading.Lock()

    class Runner:
        def __init__(self, config):
            self.config = config
            runners.append(self)

        def run(self, data, _mailbox, _stop, _log):
            with lock:
                threads.add(threading.current_thread().name)
            time.sleep(0.03)
            return RegistrationResult.success("complete", data.username)

    monkeypatch.setattr(batch_worker, "UspsRegistrationRunner", Runner)
    monkeypatch.setattr(batch_worker, "save_checkpoint", lambda *_args: None)
    worker = BatchWorker(
        items=list(enumerate(rows)),
        mailbox_base_url="https://mail.example",
        mailbox_token="token",
        mailbox_timeout_seconds=30,
        poll_seconds=1,
        delay_seconds=0,
        retries=0,
        headless=True,
        artifact_dir=tmp_path,
        checkpoint_path=tmp_path / "checkpoint.json",
        all_rows=rows,
        initial_results=pending,
        execution_mode="concurrent",
        worker_count=2,
    )

    worker.run()

    assert len(runners) == 4
    assert len({id(runner) for runner in runners}) == 4
    assert len(threads) == 2
    assert set(worker.results) == {0, 1, 2, 3}


def test_retry_gets_a_fresh_browser_fingerprint(monkeypatch, tmp_path):
    row = RegistrationData(username="retry-user", email="retry@example.com")
    fingerprints = []

    class Runner:
        def __init__(self, _config):
            pass

        def run(self, data, _mailbox, _stop, _log):
            fingerprints.append(data.browser_fingerprint)
            if len(fingerprints) == 1:
                return RegistrationResult.failed("mail", "retry")
            return RegistrationResult.success("complete", "done")

    monkeypatch.setattr(batch_worker, "UspsRegistrationRunner", Runner)
    monkeypatch.setattr(batch_worker, "save_checkpoint", lambda *_args: None)
    worker = BatchWorker(
        items=[(0, row)],
        mailbox_base_url="https://mail.example",
        mailbox_token="token",
        mailbox_timeout_seconds=30,
        poll_seconds=1,
        delay_seconds=0,
        retries=1,
        headless=True,
        artifact_dir=tmp_path,
        checkpoint_path=tmp_path / "checkpoint.json",
        all_rows=[row],
        initial_results=[RegistrationResult("pending", "queued", "waiting")],
    )

    worker.run()

    assert len(fingerprints) == 2
    assert fingerprints[0] != fingerprints[1]


def test_manual_mailbox_worker_skips_client_and_requests_verification(monkeypatch, tmp_path):
    row = RegistrationData(username="manual-user", email="manual@example.com")
    requested = []

    class Runner:
        def __init__(self, config):
            assert config.manual_mailbox_takeover

        def run(self, data, mailbox, _stop, _log, verification_required):
            assert mailbox is None
            verification_required()
            data.verification_code = "483920"
            return RegistrationResult.success("complete", "done")

    monkeypatch.setattr(batch_worker, "UspsRegistrationRunner", Runner)
    monkeypatch.setattr(batch_worker, "save_checkpoint", lambda *_args: None)
    worker = BatchWorker(
        items=[(0, row)],
        mailbox_base_url="",
        mailbox_token="",
        mailbox_timeout_seconds=30,
        poll_seconds=1,
        delay_seconds=0,
        retries=0,
        headless=True,
        artifact_dir=tmp_path,
        checkpoint_path=tmp_path / "checkpoint.json",
        all_rows=[row],
        initial_results=[RegistrationResult("pending", "queued", "waiting")],
        manual_mailbox_takeover=True,
    )
    worker.row_verification_required.connect(requested.append)

    worker.run()

    assert requested == [0]
    assert worker.results[0].status == "success"
