import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("USPS_DISABLE_AUTO_RESTORE", "1")

from PySide6.QtCore import QMimeData, QPoint, Qt, QUrl
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

import main
import manual_mailbox_ui
from main import EMAIL_COLUMN, PROXY_COLUMN, MainWindow
from manual_mailbox_ui import VERIFICATION_COLUMN
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
    window.manual_mailbox_takeover.setChecked(False)
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
            self.row_fingerprint_changed = Signal()
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
    window.manual_mailbox_takeover.setChecked(False)
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
    assert not window._launch_in_progress
    window.close()


def test_start_is_visually_disabled_before_preflight_and_reentrant_click_is_ignored(
    monkeypatch, tmp_path
):
    app()
    window = MainWindow()
    window.rows = [RegistrationData(username="user", email="a@b.com")]
    window.results = [RegistrationResult("pending", "queued", "waiting")]
    window.manual_mailbox_takeover.setChecked(False)
    window._render_rows()
    window._set_running(False)
    verified = []
    workers = []

    class Mailbox:
        def verify_access(self):
            verified.append(True)
            assert not window.start_button.isEnabled()
            assert window._launch_in_progress
            window._start_indices([0])

    class Signal:
        def connect(self, _callback):
            pass

    class Worker:
        def __init__(self, **_kwargs):
            workers.append(self)
            self.row_attempt = Signal()
            self.row_result = Signal()
            self.row_email_changed = Signal()
            self.row_fingerprint_changed = Signal()
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
    assert len(workers) == 1
    assert not window.start_button.isEnabled()
    assert not window._launch_in_progress
    window.close()


def test_imported_rows_are_selected_by_default_and_start_only_checked(monkeypatch):
    app()
    window = MainWindow()
    window.rows = [
        RegistrationData(username="first", email="first@example.com"),
        RegistrationData(username="second", email="second@example.com"),
        RegistrationData(username="third", email="third@example.com"),
    ]
    window.results = [RegistrationResult("pending", "queued", "waiting") for _ in window.rows]
    window._render_rows()

    assert all(
        window.table.item(index, 0).checkState() == Qt.CheckState.Checked
        for index in range(3)
    )

    window.table.item(0, 0).setCheckState(Qt.CheckState.Unchecked)
    window.table.item(2, 0).setCheckState(Qt.CheckState.Unchecked)
    started = []
    monkeypatch.setattr(window, "_start_indices", lambda indices: started.extend(indices))

    window.start_all()

    assert started == [1]
    window.close()


def test_row_selection_is_locked_while_batch_is_running():
    app()
    window = MainWindow()
    window.rows = [RegistrationData(username="user", email="a@b.com")]
    window.results = [RegistrationResult("pending", "queued", "waiting")]
    window._render_rows()

    window._set_running(True)
    assert not (window.table.item(0, 0).flags() & Qt.ItemFlag.ItemIsEnabled)

    window._set_running(False)
    assert window.table.item(0, 0).flags() & Qt.ItemFlag.ItemIsEnabled
    window.close()


def test_header_checkbox_selects_and_clears_all_rows():
    app()
    window = MainWindow()
    window.rows = [
        RegistrationData(username="first", email="first@example.com"),
        RegistrationData(username="second", email="second@example.com"),
    ]
    window.results = [RegistrationResult("pending", "queued", "waiting") for _ in window.rows]
    window._render_rows()
    window.show()
    QApplication.processEvents()
    header = window.selection_header
    click_point = QPoint(header.sectionSize(0) // 2, header.height() // 2)

    assert window.selection_header._check_state == Qt.CheckState.Checked
    QTest.mouseClick(header.viewport(), Qt.MouseButton.LeftButton, pos=click_point)
    assert all(
        window.table.item(index, 0).checkState() == Qt.CheckState.Unchecked
        for index in range(2)
    )
    assert window.selection_header._check_state == Qt.CheckState.Unchecked

    QTest.mouseClick(header.viewport(), Qt.MouseButton.LeftButton, pos=click_point)
    assert all(
        window.table.item(index, 0).checkState() == Qt.CheckState.Checked
        for index in range(2)
    )
    window.table.item(0, 0).setCheckState(Qt.CheckState.Unchecked)
    assert window.selection_header._check_state == Qt.CheckState.PartiallyChecked
    window.close()


def test_api_token_is_persisted_on_focus_loss(monkeypatch):
    stored = {}

    class Settings:
        def __init__(self, *_args):
            pass

        def value(self, key, default=""):
            return stored.get(key, default)

        def setValue(self, key, value):
            stored[key] = value

        def remove(self, key):
            stored.pop(key, None)

        def sync(self):
            pass

    monkeypatch.setattr(main, "QSettings", Settings)
    monkeypatch.delenv("VELYDORA_API_TOKEN", raising=False)
    app()
    window = MainWindow()
    window.mailbox_token.setText(" persisted-token ")

    window.mailbox_token.editingFinished.emit()

    assert stored[main.TOKEN_SETTINGS_KEY] == "persisted-token"
    window.close()

    restored = MainWindow()
    assert restored.mailbox_token.text() == "persisted-token"
    restored.mailbox_token.clear()
    restored.mailbox_token.editingFinished.emit()
    assert main.TOKEN_SETTINGS_KEY not in stored
    restored.close()


def test_execution_mode_controls_have_hard_browser_limit():
    app()
    window = MainWindow()

    assert window.execution_mode.currentData() == "sequential"
    assert not window.worker_count.isEnabled()
    assert window.worker_count.maximum() == 5
    assert "v2.2.2" in window.windowTitle()
    assert window.skip_failed.isChecked()
    assert window.headless.isChecked()
    assert window.manual_mailbox_takeover.isChecked()

    window.execution_mode.setCurrentIndex(1)
    assert window.execution_mode.currentData() == "concurrent"
    assert window.worker_count.isEnabled()
    window.close()


def test_default_manual_mailbox_layout_is_compact_and_keeps_table_space():
    app()
    window = MainWindow()
    window.show()
    QApplication.processEvents()

    manual_height = window.settings_box.sizeHint().height()
    assert window.manual_mailbox_takeover.isChecked()
    assert window.mailbox_panel.isHidden()
    assert manual_height <= 120
    assert window.table.minimumHeight() >= 280

    window.manual_mailbox_takeover.setChecked(False)
    QApplication.processEvents()
    assert not window.mailbox_panel.isHidden()
    assert window.settings_box.sizeHint().height() > manual_height
    window.close()


def test_manual_mailbox_start_rejects_selected_row_without_email(monkeypatch):
    app()
    window = MainWindow()
    window.rows = [RegistrationData(username="missing-email", email="")]
    window.results = [RegistrationResult("pending", "queued", "waiting")]
    window._render_rows()
    window._set_running(False)
    messages = []
    monkeypatch.setattr(window, "_show_toast", lambda message, **_kwargs: messages.append(message))

    window.start_all()

    assert messages == ["所选任务第 1 行未填写邮箱"]
    assert window.worker is None
    assert window.start_button.isEnabled()
    window.close()


def test_manual_verification_row_is_highlighted_and_accepts_code(monkeypatch):
    app()
    window = MainWindow()
    window.rows = [RegistrationData(username="manual", email="manual@example.com")]
    window.results = [RegistrationResult("running", "browser_start", "running")]
    window._render_rows()
    monkeypatch.setattr(window, "_show_toast", lambda *_args, **_kwargs: None)

    window.on_row_verification_required(0)

    assert window.results[0].stage == "manual_verification"
    assert window.table.item(0, VERIFICATION_COLUMN).text() == "双击输入验证码"
    assert window.table.item(0, VERIFICATION_COLUMN).font().bold()

    monkeypatch.setattr(
        manual_mailbox_ui.QInputDialog,
        "getText",
        lambda *_args, **_kwargs: ("483920", True),
    )
    window._on_table_cell_double_clicked(0, VERIFICATION_COLUMN)

    assert window.rows[0].verification_code == "483920"
    assert window.table.item(0, VERIFICATION_COLUMN).text() == "483920"
    assert window.results[0].message == "验证码已输入，正在继续"
    window.close()


def test_manual_email_is_edited_from_double_click_dialog(monkeypatch):
    app()
    window = MainWindow()
    window.rows = [RegistrationData(username="manual", email="")]
    window.results = [RegistrationResult("pending", "queued", "waiting")]
    window._render_rows()
    monkeypatch.setattr(
        manual_mailbox_ui.QInputDialog,
        "getText",
        lambda *_args, **_kwargs: ("customer@example.com", True),
    )
    monkeypatch.setattr(window, "_save_checkpoint", lambda: None)

    window._on_table_cell_double_clicked(0, EMAIL_COLUMN)

    assert window.rows[0].email == "customer@example.com"
    assert window.table.item(0, EMAIL_COLUMN).text() == "customer@example.com"
    window.close()


def test_proxy_file_is_bound_in_row_order_and_masked_in_table(monkeypatch, tmp_path):
    app()
    window = MainWindow()
    window.rows = [
        RegistrationData(username="first", email="first@example.com"),
        RegistrationData(username="second", email="second@example.com"),
    ]
    window.results = [RegistrationResult("pending", "queued", "waiting") for _ in window.rows]
    window._render_rows()
    monkeypatch.setattr(window, "_save_checkpoint", lambda: None)
    path = tmp_path / "proxies.txt"
    path.write_text(
        "127.0.0.1:8001:user1:secret1\n127.0.0.2:8002:user2:secret2\n",
        encoding="utf-8",
    )

    window.load_proxy_path(str(path))

    assert window.table.item(0, PROXY_COLUMN).text() == "127.0.0.1:8001"
    assert window.table.item(1, PROXY_COLUMN).text() == "127.0.0.2:8002"
    assert "secret" not in window.table.item(0, PROXY_COLUMN).text()
    assert "user1" in window.rows[0].proxy
    assert "user2" in window.rows[1].proxy
    window.close()


def test_proxy_table_accepts_txt_file_urls(tmp_path):
    app()
    window = MainWindow()
    path = tmp_path / "proxies.txt"
    path.write_text("127.0.0.1:8080", encoding="utf-8")
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path))])

    assert Path(window.table._first_proxy_path(mime)) == path
    window.close()


def test_last_csv_path_is_automatically_restored(monkeypatch, tmp_path):
    source = tmp_path / "last.csv"
    source.write_text(
        "account_type,username,password,first_name,last_name,address1,city,state,zip_code,"
        "phone,security_answer1,security_answer2,status\n"
        "Personal Account,failed-user,ValidPass123,A,B,1 Main,Austin,TX,78701,"
        "5125550100,X,Y,failed\n"
        "Personal Account,ready-user,ValidPass123,C,D,2 Main,Austin,TX,78701,"
        "5125550101,X,Z,\n",
        encoding="utf-8",
    )
    stored = {main.LAST_CSV_PATH_KEY: str(source)}

    class Settings:
        def __init__(self, *_args):
            pass

        def value(self, key, default=""):
            return stored.get(key, default)

        def setValue(self, key, value):
            stored[key] = value

        def remove(self, key):
            stored.pop(key, None)

        def sync(self):
            pass

    monkeypatch.setattr(main, "QSettings", Settings)
    monkeypatch.setattr(main, "CHECKPOINT_PATH", tmp_path / "checkpoint.json")
    monkeypatch.delenv("USPS_DISABLE_AUTO_RESTORE", raising=False)

    window = MainWindow()

    assert [row.username for row in window.rows] == ["ready-user"]
    assert window.current_csv_path == source
    assert "跳过失败项 1 条" in window.log_view.toPlainText()
    window.close()


def test_startup_restores_failed_rows_from_checkpoint_before_skip_filter(monkeypatch, tmp_path):
    source = tmp_path / "last.csv"
    source.write_text(
        "account_type,email,username,password,first_name,last_name,address1,city,state,"
        "zip_code,phone,security_answer1,security_answer2,status\n"
        "Personal Account,failed@example.com,failed-user,ValidPass123,A,B,1 Main,Austin,"
        "TX,78701,5125550100,X,Y,failed\n",
        encoding="utf-8",
    )
    checkpoint_path = tmp_path / "checkpoint.json"
    saved_row = RegistrationData(
        account_type="Personal Account",
        email="failed@example.com",
        username="failed-user",
    )
    main.checkpoint.save_checkpoint(
        checkpoint_path,
        [saved_row],
        [RegistrationResult.failed("identity_verification", "rejected")],
    )
    stored = {main.LAST_CSV_PATH_KEY: str(source)}

    class Settings:
        def __init__(self, *_args):
            pass

        def value(self, key, default=""):
            return stored.get(key, default)

        def setValue(self, key, value):
            stored[key] = value

        def remove(self, key):
            stored.pop(key, None)

        def sync(self):
            pass

    monkeypatch.setattr(main, "QSettings", Settings)
    monkeypatch.setattr(main, "CHECKPOINT_PATH", checkpoint_path)
    monkeypatch.delenv("USPS_DISABLE_AUTO_RESTORE", raising=False)

    window = MainWindow()

    assert window.skip_failed.isChecked()
    assert [row.username for row in window.rows] == ["failed-user"]
    assert window.results[0].status == "failed"
    assert window.current_csv_path == source
    assert "已恢复上次表格 1 条注册数据" in window.log_view.toPlainText()
    assert "跳过失败项" not in window.log_view.toPlainText()
    window.close()


def test_corrupt_checkpoint_falls_back_to_last_csv(monkeypatch, tmp_path):
    source = tmp_path / "last.csv"
    source.write_text(
        "account_type,email,username,password,first_name,last_name,address1,city,state,"
        "zip_code,phone,security_answer1,security_answer2,status\n"
        "Personal Account,ready@example.com,ready-user,ValidPass123,A,B,1 Main,Austin,"
        "TX,78701,5125550100,X,Y,\n",
        encoding="utf-8",
    )
    checkpoint_path = tmp_path / "checkpoint.json"
    checkpoint_path.write_text("broken", encoding="utf-8")
    stored = {main.LAST_CSV_PATH_KEY: str(source)}

    class Settings:
        def __init__(self, *_args):
            pass

        def value(self, key, default=""):
            return stored.get(key, default)

        def setValue(self, key, value):
            stored[key] = value

        def remove(self, key):
            stored.pop(key, None)

        def sync(self):
            pass

    monkeypatch.setattr(main, "QSettings", Settings)
    monkeypatch.setattr(main, "CHECKPOINT_PATH", checkpoint_path)
    monkeypatch.delenv("USPS_DISABLE_AUTO_RESTORE", raising=False)

    window = MainWindow()

    assert [row.username for row in window.rows] == ["ready-user"]
    assert "上次表格检查点恢复失败" in window.log_view.toPlainText()
    window.close()


def test_final_failure_is_written_back_to_source_csv(monkeypatch, tmp_path):
    app()
    window = MainWindow()
    source = tmp_path / "source.csv"
    source.write_text(
        "account_type,username,password,first_name,last_name,address1,city,state,zip_code,"
        "phone,security_answer1,security_answer2,status\n"
        "Personal Account,user1,ValidPass123,A,B,1 Main,Austin,TX,78701,"
        "5125550100,X,Y,\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "CHECKPOINT_PATH", tmp_path / "checkpoint.json")
    window._load_csv_path(str(source))

    window.on_row_result(0, RegistrationResult.failed("identity_verification", "rejected"))

    lines = source.read_text(encoding="utf-8-sig").splitlines()
    assert lines[0].split(",")[-1] == "status"
    assert lines[1].split(",")[-1] == "failed"
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
