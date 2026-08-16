import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

import main
from main import MainWindow
from models import RegistrationData, RegistrationResult
from retry_policy import rotate_manual_retry_addresses, runnable_indices


def app():
    return QApplication.instance() or QApplication([])


def test_start_all_excludes_successful_rows():
    results = [
        RegistrationResult.success("complete", "done"),
        RegistrationResult("stopped", "queued", "stopped"),
    ]
    assert runnable_indices(results) == [1]


def test_start_all_excludes_uncertain_submissions_from_automatic_retry():
    results = [
        RegistrationResult.failed("submission_unknown", "manual review"),
        RegistrationResult.failed("post_submit_mail", "manual review"),
        RegistrationResult.failed("otp_submission_unknown", "manual review"),
        RegistrationResult.failed("mail", "timeout"),
    ]
    assert runnable_indices(results) == [3]


def test_business_stop_before_otp_submit_remains_runnable():
    results = [RegistrationResult.stopped("business_email_verification_pending")]

    assert runnable_indices(results) == [0]


def test_manual_retry_rotates_mailbox_address():
    rows = [RegistrationData(email="usps-a@velydora.com")]
    results = [RegistrationResult.failed("mail", "timeout")]
    changes = rotate_manual_retry_addresses(rows, results, [0])
    assert changes[0][0] == 0
    assert rows[0].email != "usps-a@velydora.com"
    assert rows[0].email.endswith("@velydora.com")


def test_start_preflights_authenticated_mailbox_access(monkeypatch, tmp_path):
    app()
    window = MainWindow()
    window.rows = [RegistrationData(username="user", email="a@b.com")]
    window.results = [RegistrationResult("pending", "queued", "waiting")]
    verified = []

    class Mailbox:
        def verify_access(self):
            verified.append(True)

    class Signal:
        def connect(self, _callback):
            pass

    class Worker:
        def __init__(self, **_kwargs):
            self.row_attempt = Signal()
            self.row_result = Signal()
            self.row_email_changed = Signal()
            self.log = Signal()
            self.finished = Signal()

        def start(self):
            pass

        def isRunning(self):
            return False

    monkeypatch.setattr(window, "_mailbox_client", lambda: Mailbox())
    monkeypatch.setattr(main, "BatchWorker", Worker)
    monkeypatch.setattr(main, "CHECKPOINT_PATH", tmp_path / "checkpoint.json")
    window._start_indices([0])
    assert verified == [True]
    assert not window.start_button.isEnabled()
    assert window.stop_button.isEnabled()
    window.on_batch_finished()
    assert window.start_button.isEnabled()
    assert not window.stop_button.isEnabled()
    window.close()


def test_failed_startup_restores_start_button(monkeypatch):
    app()
    window = MainWindow()
    window.rows = [RegistrationData(username="user", email="a@b.com")]
    window.results = [RegistrationResult("pending", "queued", "waiting")]
    window._set_running(False)

    class Mailbox:
        def verify_access(self):
            raise RuntimeError("offline")

    monkeypatch.setattr(window, "_mailbox_client", lambda: Mailbox())
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args, **_kwargs: None)

    window._start_indices([0])

    assert window.start_button.isEnabled()
    assert not window.stop_button.isEnabled()
    assert window.worker is None
    window.close()


def test_import_confirmation_blocks_unfinished_checkpoint_overwrite(monkeypatch, tmp_path):
    app()
    window = MainWindow()
    path = tmp_path / "checkpoint.json"
    path.write_text("present", encoding="utf-8")
    monkeypatch.setattr(main, "CHECKPOINT_PATH", path)
    monkeypatch.setattr(main.checkpoint, "checkpoint_has_unfinished", lambda _path: True)
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *_args, **_kwargs: QMessageBox.Cancel,
    )
    assert not window._confirm_checkpoint_replacement()
    window.close()


def test_clear_requires_confirmation_for_unfinished_batch(monkeypatch):
    app()
    window = MainWindow()
    window.rows = [RegistrationData(username="user", email="a@b.com")]
    window.results = [RegistrationResult("pending", "queued", "waiting")]
    window._render_rows()
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *_args, **_kwargs: QMessageBox.Cancel,
    )
    window.clear_rows()
    assert len(window.rows) == 1
    window.close()


def test_clear_failure_resaves_checkpoint_and_preserves_rows(monkeypatch):
    app()
    window = MainWindow()
    window.rows = [RegistrationData(username="user", email="a@b.com")]
    window.results = [RegistrationResult("pending", "queued", "waiting")]
    window._render_rows()
    saved = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args, **_kwargs: QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, "critical", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main.checkpoint, "clear_private_data", lambda *_args: False)
    monkeypatch.setattr(window, "_save_checkpoint", lambda: saved.append(True))

    window.clear_rows()

    assert saved == [True]
    assert len(window.rows) == 1
    window.close()


def test_close_merges_worker_results_before_checkpoint_save(monkeypatch, tmp_path):
    app()
    window = MainWindow()
    window.rows = [RegistrationData(username="user", email="a@b.com")]
    window.results = [RegistrationResult("running", "final_submit", "running")]
    window._render_rows()
    monkeypatch.setattr(main, "CHECKPOINT_PATH", tmp_path / "checkpoint.json")

    completed = RegistrationResult.success("complete", "https://reg.usps.com/success")

    class Worker:
        results = {0: completed}

        def isRunning(self):
            return True

        def stop(self):
            pass

        def wait(self, _timeout):
            return True

    class Event:
        accepted = False

        def accept(self):
            self.accepted = True

        def ignore(self):
            raise AssertionError("close should not be ignored")

    window.worker = Worker()
    event = Event()
    window.closeEvent(event)
    assert event.accepted
    assert window.results[0] == completed
    loaded_rows, loaded_results = main.checkpoint.load_checkpoint(main.CHECKPOINT_PATH)
    assert loaded_rows == window.rows
    assert loaded_results == [completed]


def test_started_attempt_is_persisted_as_non_replayable(monkeypatch, tmp_path):
    app()
    window = MainWindow()
    window.rows = [RegistrationData(username="user", email="a@b.com")]
    window.results = [RegistrationResult("pending", "queued", "waiting")]
    window._render_rows()
    monkeypatch.setattr(main, "CHECKPOINT_PATH", tmp_path / "checkpoint.json")

    window.on_row_attempt(0, 1, 2)

    _rows, restored_results = main.checkpoint.load_checkpoint(main.CHECKPOINT_PATH)
    assert restored_results[0].status == "running"
    assert runnable_indices(restored_results) == []
    window.close()
