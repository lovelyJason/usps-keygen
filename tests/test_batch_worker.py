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
