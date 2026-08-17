from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QEventLoop, QSettings, Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

import checkpoint
from batch_io import (
    export_results,
    load_registration_csv_with_stats,
    materialize_csv_emails,
    update_csv_status,
    write_template,
)
from batch_worker import BatchWorker
from browser_fingerprint import fingerprint_summary
from mailbox_client import MailboxClient
from manual_mailbox_ui import EMAIL_COLUMN, TOKEN_SETTINGS_KEY, ManualMailboxUiMixin
from models import RegistrationData, RegistrationResult
from proxy_io import load_proxy_file, proxy_display
from retry_policy import rotate_manual_retry_addresses, runnable_indices
from ui_controls import CheckBoxHeader, ProxyDropTable
from version import APP_TITLE, WORKBENCH_TITLE

ARTIFACT_DIR = Path.home() / ".usps-registration-mvp" / "run-artifacts"
CHECKPOINT_PATH = Path.home() / ".usps-registration-mvp" / "batch-checkpoint.json"
SELECT_COLUMN = 0
PROXY_COLUMN = 5
FINGERPRINT_COLUMN = 6
STATUS_COLUMN = 9
STAGE_COLUMN = 10
MESSAGE_COLUMN = 11
LAST_CSV_PATH_KEY = "files/last_csv_path"


class MainWindow(ManualMailboxUiMixin, QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1180, 820)
        self.rows: list[RegistrationData] = []
        self.results: list[RegistrationResult] = []
        self.worker: BatchWorker | None = None
        self._is_running = False
        self._launch_in_progress = False
        self.pending_proxies: list[str] = []
        self.current_csv_path: Path | None = None
        self.settings = QSettings("JasonHuang", "USPSBatchRegistration")

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(7)

        title = QLabel(WORKBENCH_TITLE)
        title.setObjectName("pageTitle")
        root.addWidget(title)
        root.addWidget(self._build_settings())
        root.addWidget(self._build_actions())
        root.addWidget(self._build_table(), 1)
        root.addWidget(QLabel("运行日志"))
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumHeight(105)
        root.addWidget(self.log_view)

        self.status_label = QLabel("尚未导入数据")
        self.statusBar().addPermanentWidget(self.status_label)
        self._set_running(False)
        self._apply_style()
        if os.getenv("USPS_DISABLE_AUTO_RESTORE") != "1":
            checkpoint.restore_previous_session(self, CHECKPOINT_PATH, LAST_CSV_PATH_KEY)

    def _build_actions(self) -> QGroupBox:
        box = QGroupBox("批量任务")
        layout = QHBoxLayout(box)
        self.template_button = QPushButton("生成 CSV 模板")
        self.template_button.clicked.connect(self.save_template)
        self.import_button = QPushButton("导入 CSV")
        self.import_button.clicked.connect(self.import_csv)
        self.proxy_button = QPushButton("导入代理 TXT")
        self.proxy_button.setToolTip("也可以把代理 TXT 文件直接拖入下方表格")
        self.proxy_button.clicked.connect(self.import_proxy_file)
        self.skip_failed = QCheckBox("跳过失败项")
        self.skip_failed.setChecked(True)
        self.skip_failed.setToolTip("导入 CSV 时不加载最后一列 status 为 failed/失败的行")
        self.checkpoint_button = QPushButton("载入检查点")
        self.checkpoint_button.clicked.connect(self.restore_checkpoint)
        self.start_button = QPushButton("开始所选")
        self.start_button.setObjectName("primaryButton")
        self.start_button.clicked.connect(self.start_all)
        self.stop_button = QPushButton("停止")
        self.stop_button.clicked.connect(self.stop_batch)
        self.retry_button = QPushButton("重试未完成项")
        self.retry_button.clicked.connect(self.retry_failed)
        self.export_button = QPushButton("导出结果")
        self.export_button.clicked.connect(self.export_csv)
        self.export_sensitive = QCheckBox("含密码、代理凭据与安全答案")
        self.clear_button = QPushButton("清空")
        self.clear_button.clicked.connect(self.clear_rows)
        for button in (
            self.template_button,
            self.import_button,
            self.proxy_button,
            self.skip_failed,
            self.checkpoint_button,
            self.start_button,
            self.stop_button,
            self.retry_button,
            self.export_button,
            self.export_sensitive,
            self.clear_button,
        ):
            layout.addWidget(button)
        layout.addStretch()
        return box

    def _build_table(self) -> QTableWidget:
        table = ProxyDropTable(0, 12)
        table.setHorizontalHeaderLabels(
            [
                "",
                "序号",
                "账号类型",
                "邮箱",
                "验证码 / 验证链接",
                "代理 IP",
                "浏览器指纹",
                "用户名",
                "姓名",
                "状态",
                "阶段",
                "结果说明",
            ]
        )
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.setMinimumHeight(280)
        header = CheckBoxHeader(Qt.Orientation.Horizontal, table)
        table.setHorizontalHeader(header)
        self.selection_header = header
        header.toggle_all_requested.connect(self._set_all_selected)
        for column in (0, 1, 2, 4, 5, 6, 7, 8, 9, 10):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        header.setSectionResizeMode(11, QHeaderView.Stretch)
        table.itemChanged.connect(self._on_table_item_changed)
        table.cellDoubleClicked.connect(self._on_table_cell_double_clicked)
        table.proxy_file_dropped.connect(self.load_proxy_path)
        self.table = table
        return table

    def load_token_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 Token 文件", "", "Text files (*.txt);;All files (*)"
        )
        if not path:
            return
        try:
            token = Path(path).read_text(encoding="utf-8").strip()
        except OSError as exc:
            QMessageBox.critical(self, "读取失败", str(exc))
            return
        self.mailbox_token.setText(token)
        self._persist_mailbox_token()
        self.log_view.append("已载入 Token 文件，内容未写入日志。")

    def _persist_mailbox_token(self) -> None:
        token = self.mailbox_token.text().strip()
        if token:
            self.settings.setValue(TOKEN_SETTINGS_KEY, token)
        else:
            self.settings.remove(TOKEN_SETTINGS_KEY)
        self.settings.sync()

    def test_mail_connection(self) -> None:
        try:
            self._mailbox_client().verify_access()
        except Exception as exc:
            QMessageBox.critical(self, "连接失败", str(exc))
            return
        QMessageBox.information(self, "连接成功", "邮箱服务地址和 API Token 均验证通过。")

    def save_template(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "保存 CSV 模板",
            str(Path(__file__).resolve().parent / "registrations_template.csv"),
            "CSV (*.csv)",
        )
        if not path:
            return
        write_template(path)
        self.log_view.append(f"已生成模板：{path}")

    def import_csv(self) -> None:
        if not self._confirm_checkpoint_replacement():
            return
        path, _ = QFileDialog.getOpenFileName(self, "导入注册 CSV", "", "CSV (*.csv)")
        if not path:
            return
        self._load_csv_path(path)

    def _load_csv_path(self, path: str, startup: bool = False) -> None:
        try:
            rows, skipped = load_registration_csv_with_stats(
                path,
                mailbox_domain=self.mailbox_domain.text(),
                skip_failed=self.skip_failed.isChecked(),
                manual_mailbox_takeover=self.manual_mailbox_takeover.isChecked(),
            )
        except Exception as exc:
            if startup:
                self.log_view.append(f"上次 CSV 自动加载失败：{exc}")
            else:
                QMessageBox.critical(self, "导入失败", str(exc))
            return
        self.current_csv_path = Path(path)
        self.settings.setValue(LAST_CSV_PATH_KEY, str(self.current_csv_path))
        self.settings.sync()
        self.rows = rows
        self._assign_pending_proxies()
        self.results = [RegistrationResult("pending", "queued", "等待执行") for _ in rows]
        self._render_rows()
        self._set_running(False)
        try:
            materialize_csv_emails(self.current_csv_path, rows)
        except Exception as exc:
            self.log_view.append(f"CSV 随机邮箱/status 列回写失败：{exc}")
        if rows:
            self._save_checkpoint()
        message = (
            f"已从上次 CSV 路径自动加载 {len(rows)} 条注册数据"
            if startup
            else f"已导入 {len(rows)} 条注册数据"
        )
        if skipped:
            message += f"，跳过失败项 {skipped} 条"
        self.log_view.append(message + "。")

    def import_proxy_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "导入代理 TXT", "", "Text files (*.txt)")
        if path:
            self.load_proxy_path(path)

    def load_proxy_path(self, path: str) -> None:
        try:
            self.pending_proxies = load_proxy_file(path)
        except Exception as exc:
            QMessageBox.critical(self, "代理导入失败", str(exc))
            return
        self._assign_pending_proxies()
        if self.rows:
            for index, row in enumerate(self.rows):
                item = self.table.item(index, PROXY_COLUMN)
                if item:
                    item.setText(proxy_display(row.proxy))
            self._save_checkpoint()
        assigned = min(len(self.rows), len(self.pending_proxies))
        unused = max(0, len(self.pending_proxies) - len(self.rows))
        waiting = max(0, len(self.rows) - len(self.pending_proxies))
        if not self.rows:
            self.log_view.append(
                f"已载入 {len(self.pending_proxies)} 个代理，导入 CSV 后将按顺序绑定。"
            )
        else:
            detail = f"已按顺序绑定 {assigned} 个代理"
            if unused:
                detail += f"，剩余 {unused} 个未使用"
            if waiting:
                detail += f"，后 {waiting} 条任务保持直连"
            self.log_view.append(detail + "。")

    def _assign_pending_proxies(self) -> None:
        if not self.pending_proxies:
            return
        for index, row in enumerate(self.rows):
            row.proxy = self.pending_proxies[index] if index < len(self.pending_proxies) else ""

    def restore_checkpoint(self) -> None:
        if not CHECKPOINT_PATH.exists():
            QMessageBox.information(self, "没有检查点", "尚未保存过批量任务检查点。")
            return
        try:
            self.rows, self.results = checkpoint.load_checkpoint(CHECKPOINT_PATH)
        except Exception as exc:
            QMessageBox.critical(self, "检查点读取失败", str(exc))
            return
        self._render_rows()
        self._set_running(False)
        self.log_view.append(f"已恢复 {len(self.rows)} 条任务及原邮箱地址。")

    def start_all(self) -> None:
        indices = self._selected_runnable_indices()
        if not indices:
            QMessageBox.information(
                self, "没有可执行项", "请至少勾选一条尚未完成且可安全执行的任务。"
            )
            return
        self._start_indices(indices)

    def retry_failed(self) -> None:
        indices = self._selected_runnable_indices()
        if not indices:
            QMessageBox.information(self, "无需重试", "勾选范围内没有可安全重试的任务。")
            return
        self._start_indices(indices)

    def _start_indices(self, indices: list[int]) -> None:
        if not self.rows:
            QMessageBox.warning(self, "没有任务", "请先导入 CSV。")
            return
        if not indices or self.worker or self._launch_in_progress:
            return
        if not self._manual_mailbox_preflight(indices):
            return
        self._launch_in_progress = True
        self._set_running(True)
        self.start_button.repaint()
        QApplication.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
        if not self.manual_mailbox_takeover.isChecked():
            try:
                self._mailbox_client().verify_access()
            except Exception as exc:
                self._launch_in_progress = False
                self._set_running(False)
                QMessageBox.warning(self, "邮箱预检失败", str(exc))
                return
            for index, email in rotate_manual_retry_addresses(self.rows, self.results, indices):
                self.table.item(index, EMAIL_COLUMN).setText(email)
                self.log_view.append(f"{self.rows[index].username}: 已为重试切换邮箱地址。")
        self._save_checkpoint()
        items = [(index, self.rows[index]) for index in indices]
        try:
            self.worker = BatchWorker(
                items=items,
                mailbox_base_url=self.mailbox_url.text().strip(),
                mailbox_token=self.mailbox_token.text().strip(),
                mailbox_timeout_seconds=self.mail_timeout.value(),
                poll_seconds=float(self.poll_interval.value()),
                delay_seconds=float(self.row_delay.value()),
                retries=self.retry_count.value(),
                headless=self.headless.isChecked(),
                artifact_dir=ARTIFACT_DIR,
                checkpoint_path=CHECKPOINT_PATH,
                all_rows=self.rows,
                initial_results=self.results,
                failure_hold_seconds=self.failure_hold.value(),
                execution_mode=str(self.execution_mode.currentData()),
                worker_count=self.worker_count.value(),
                manual_mailbox_takeover=self.manual_mailbox_takeover.isChecked(),
            )
        except Exception as exc:
            self._launch_in_progress = False
            self._set_running(False)
            QMessageBox.warning(self, "任务启动失败", str(exc))
            return
        self.worker.row_attempt.connect(self.on_row_attempt)
        self.worker.row_result.connect(self.on_row_result)
        self.worker.row_email_changed.connect(self.on_row_email_changed)
        self.worker.row_fingerprint_changed.connect(self.on_row_fingerprint_changed)
        verification_signal = getattr(self.worker, "row_verification_required", None)
        if verification_signal is not None:
            verification_signal.connect(self.on_row_verification_required)
        self.worker.log.connect(self.log_view.append)
        self.worker.finished.connect(self.on_batch_finished)
        try:
            self.worker.start()
        except Exception as exc:
            self.worker = None
            self._launch_in_progress = False
            self._set_running(False)
            QMessageBox.warning(self, "任务启动失败", str(exc))
            return
        self._launch_in_progress = False

    def stop_batch(self) -> None:
        if self.worker:
            self.worker.stop()
            self.stop_button.setEnabled(False)
            self.log_view.append("已请求停止，将在当前安全边界退出。")

    def on_row_attempt(self, index: int, attempt: int, total: int) -> None:
        self.results[index] = RegistrationResult(
            "running", "browser_start", f"尝试 {attempt}/{total}"
        )
        self._render_result(index)
        if self.worker is None:
            self._save_checkpoint()

    def on_row_result(self, index: int, result: RegistrationResult) -> None:
        self.results[index] = result
        self._render_result(index)
        self.log_view.append(f"{self.rows[index].username}: {result.status} - {result.message}")
        if self.current_csv_path:
            try:
                update_csv_status(self.current_csv_path, self.rows[index], result.status)
            except Exception as exc:
                self.log_view.append(f"CSV 状态回写失败：{exc}")

    def on_row_email_changed(self, index: int, email: str) -> None:
        self.rows[index].email = email
        self.table.item(index, EMAIL_COLUMN).setText(email)
        self.log_view.append(f"{self.rows[index].username}: 重试已切换到新的邮箱地址。")

    def on_row_fingerprint_changed(self, index: int, summary: str) -> None:
        self.table.item(index, FINGERPRINT_COLUMN).setText(summary)
        self.log_view.append(f"{self.rows[index].username}: 新浏览器指纹 {summary}")

    def on_batch_finished(self) -> None:
        if self.worker:
            self._merge_worker_results(self.worker)
        self.worker = None
        self._launch_in_progress = False
        self._set_running(False)
        self._update_summary()
        self.log_view.append("本轮批量任务已结束。")

    def export_csv(self) -> None:
        if not self.rows:
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出注册结果",
            str(Path(__file__).resolve().parent / "registration_results.csv"),
            "CSV (*.csv)",
        )
        if not path:
            return
        include_sensitive = self.export_sensitive.isChecked()
        if include_sensitive:
            choice = QMessageBox.warning(
                self,
                "确认导出敏感凭据",
                "文件将包含账号密码、代理凭据和安全问题答案，请只保存到受控目录。",
                QMessageBox.Yes | QMessageBox.Cancel,
                QMessageBox.Cancel,
            )
            if choice != QMessageBox.Yes:
                return
        export_results(
            path,
            zip(self.rows, self.results, strict=True),
            include_sensitive=include_sensitive,
        )
        self.log_view.append(f"已导出结果：{path}")

    def clear_rows(self) -> None:
        if any(result.status != "success" for result in self.results):
            choice = QMessageBox.warning(
                self,
                "确认清空未完成批次",
                "当前仍有未完成或待人工核对的任务。清空会删除加密检查点，是否继续？",
                QMessageBox.Yes | QMessageBox.Cancel,
                QMessageBox.Cancel,
            )
            if choice != QMessageBox.Yes:
                return
        if not checkpoint.clear_private_data(CHECKPOINT_PATH, ARTIFACT_DIR):
            self._save_checkpoint()
            QMessageBox.critical(self, "清理失败", "私有文件正在被占用，未清空当前批次。")
            return
        self.rows.clear()
        self.results.clear()
        self.current_csv_path = None
        self.settings.remove(LAST_CSV_PATH_KEY)
        self.settings.sync()
        self.table.setRowCount(0)
        self._update_header_check_state()
        self._update_summary()
        self._set_running(False)

    def _render_rows(self) -> None:
        self.table.setRowCount(len(self.rows))
        for index, data in enumerate(self.rows):
            selected = QTableWidgetItem()
            selected.setFlags(
                Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
            )
            selected.setCheckState(Qt.CheckState.Checked)
            self.table.setItem(index, SELECT_COLUMN, selected)
            values = [
                str(index + 1),
                data.account_type.replace(" Account", ""),
                data.email,
                data.verification_code,
                proxy_display(data.proxy),
                fingerprint_summary(data.browser_fingerprint),
                data.username,
                f"{data.first_name} {data.last_name}",
                "",
                "",
                "",
            ]
            for column, value in enumerate(values, start=1):
                self.table.setItem(index, column, QTableWidgetItem(value))
            self._render_result(index)
        self._update_header_check_state()
        self._update_summary()

    def _set_all_selected(self, checked: bool) -> None:
        self.table.blockSignals(True)
        try:
            state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
            for index in range(self.table.rowCount()):
                item = self.table.item(index, SELECT_COLUMN)
                if item is not None:
                    item.setCheckState(state)
        finally:
            self.table.blockSignals(False)
        self._update_header_check_state()

    def _on_table_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() == SELECT_COLUMN:
            self._update_header_check_state()

    def _update_header_check_state(self) -> None:
        states = [
            self.table.item(index, SELECT_COLUMN).checkState()
            for index in range(self.table.rowCount())
            if self.table.item(index, SELECT_COLUMN) is not None
        ]
        if states and all(state == Qt.CheckState.Checked for state in states):
            header_state = Qt.CheckState.Checked
        elif any(state == Qt.CheckState.Checked for state in states):
            header_state = Qt.CheckState.PartiallyChecked
        else:
            header_state = Qt.CheckState.Unchecked
        self.selection_header.set_check_state(header_state)

    def _selected_runnable_indices(self) -> list[int]:
        runnable = set(runnable_indices(self.results))
        return [
            index
            for index in range(len(self.rows))
            if index in runnable
            and self.table.item(index, SELECT_COLUMN) is not None
            and self.table.item(index, SELECT_COLUMN).checkState() == Qt.CheckState.Checked
        ]

    def _set_selection_enabled(self, enabled: bool) -> None:
        self.selection_header.set_checkbox_enabled(enabled)
        for index in range(self.table.rowCount()):
            item = self.table.item(index, SELECT_COLUMN)
            if item is None:
                continue
            flags = item.flags()
            if enabled:
                item.setFlags(flags | Qt.ItemFlag.ItemIsEnabled)
            else:
                item.setFlags(flags & ~Qt.ItemFlag.ItemIsEnabled)

    def _render_result(self, index: int) -> None:
        result = self.results[index]
        labels = {
            "pending": "等待",
            "running": "运行中",
            "success": "成功",
            "failed": "失败",
            "stopped": "已停止",
        }
        self.table.item(index, STATUS_COLUMN).setText(labels.get(result.status, result.status))
        self.table.item(index, STAGE_COLUMN).setText(result.stage)
        self.table.item(index, MESSAGE_COLUMN).setText(result.message)

    def _update_summary(self) -> None:
        statuses = ("pending", "running", "success", "failed", "stopped")
        counts = {
            status: sum(result.status == status for result in self.results) for status in statuses
        }
        self.status_label.setText(
            f"共 {len(self.rows)} 条 | 成功 {counts['success']} | 失败 {counts['failed']} | "
            f"运行中 {counts['running']} | 等待 {counts['pending']}"
        )

    def _mailbox_client(self) -> MailboxClient:
        return MailboxClient(self.mailbox_url.text().strip(), self.mailbox_token.text().strip())

    def _save_checkpoint(self) -> None:
        if not self.rows:
            return
        try:
            checkpoint.save_checkpoint(CHECKPOINT_PATH, self.rows, self.results)
        except Exception as exc:
            self.log_view.append(f"检查点保存失败：{exc}")

    def _confirm_checkpoint_replacement(self) -> bool:
        if not CHECKPOINT_PATH.exists():
            return True
        try:
            unfinished = checkpoint.checkpoint_has_unfinished(CHECKPOINT_PATH)
        except Exception as exc:
            message = f"现有检查点无法读取：{exc}\n继续会覆盖该文件，是否继续？"
        else:
            if not unfinished:
                return True
            message = "检测到尚未完成的批次。导入新 CSV 会覆盖其恢复数据，是否继续？"
        choice = QMessageBox.warning(
            self,
            "确认覆盖检查点",
            message,
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        return choice == QMessageBox.Yes

    def _merge_worker_results(self, worker: BatchWorker) -> None:
        changed = False
        for index, result in getattr(worker, "results", {}).items():
            if 0 <= index < len(self.results):
                self.results[index] = result
                changed = True
                self.table.item(index, EMAIL_COLUMN).setText(self.rows[index].email)
                if self.table.item(index, STATUS_COLUMN):
                    self._render_result(index)
        if changed:
            self._save_checkpoint()

    def _set_running(self, running: bool) -> None:
        self._is_running = running
        self.start_button.setEnabled(not running and bool(self.rows))
        self._set_selection_enabled(not running)
        self.stop_button.setEnabled(running)
        self.retry_button.setEnabled(not running and bool(self.rows))
        self.import_button.setEnabled(not running)
        self.proxy_button.setEnabled(not running)
        self.skip_failed.setEnabled(not running)
        self.checkpoint_button.setEnabled(not running)
        self.clear_button.setEnabled(not running and bool(self.rows))
        self.export_button.setEnabled(not running and bool(self.rows))
        self.test_mail_button.setEnabled(not running)
        self.manual_mailbox_takeover.setEnabled(not running)
        self.execution_mode.setEnabled(not running)
        self.worker_count.setEnabled(
            not running and self.execution_mode.currentData() == "concurrent"
        )
        self._on_mailbox_mode_changed()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.worker:
            worker = self.worker
            if worker.isRunning():
                worker.stop()
                if not worker.wait(10_000):
                    QMessageBox.warning(
                        self, "任务仍在退出", "浏览器任务尚未结束，请稍后再关闭窗口。"
                    )
                    event.ignore()
                    return
            self._merge_worker_results(worker)
            self.worker = None
        event.accept()

def main() -> int:
    if getattr(sys, "frozen", False):
        browser_dir = Path(sys.executable).resolve().parent / "playwright-browsers"
        if browser_dir.is_dir():
            os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(browser_dir))
    if "--browser-smoke" in sys.argv:
        from browser_runtime import smoke_browser_runtime

        smoke_browser_runtime()
        return 0
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
