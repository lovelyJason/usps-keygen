from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
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
from batch_io import export_results, load_registration_csv, write_template
from batch_worker import BatchWorker
from mailbox_client import MailboxClient
from models import RegistrationData, RegistrationResult
from retry_policy import rotate_manual_retry_addresses, runnable_indices
from ui_controls import make_spin
from ui_style import APP_STYLE

DEFAULT_MAILBOX_URL = "https://velydora-mail-otp.hzj1248394650.workers.dev"
ARTIFACT_DIR = Path.home() / ".usps-registration-mvp" / "run-artifacts"
CHECKPOINT_PATH = Path.home() / ".usps-registration-mvp" / "batch-checkpoint.json"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("USPS 批量注册助手")
        self.resize(1180, 820)
        self.rows: list[RegistrationData] = []
        self.results: list[RegistrationResult] = []
        self.worker: BatchWorker | None = None

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        title = QLabel("USPS 批量注册工作台")
        title.setObjectName("pageTitle")
        root.addWidget(title)
        root.addWidget(self._build_settings())
        root.addWidget(self._build_actions())
        root.addWidget(self._build_table(), 1)
        root.addWidget(QLabel("运行日志"))
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumHeight(160)
        root.addWidget(self.log_view)

        self.status_label = QLabel("尚未导入数据")
        self.statusBar().addPermanentWidget(self.status_label)
        self._set_running(False)
        self._apply_style()

    def _build_settings(self) -> QGroupBox:
        box = QGroupBox("邮箱与执行设置")
        grid = QGridLayout(box)

        self.mailbox_url = QLineEdit(DEFAULT_MAILBOX_URL)
        self.mailbox_token = QLineEdit(os.getenv("VELYDORA_API_TOKEN", ""))
        self.mailbox_token.setEchoMode(QLineEdit.Password)
        self.mailbox_token.setPlaceholderText("Bearer Token，仅保存在当前进程")
        self.mailbox_domain = QLineEdit("velydora.com")
        self.mailbox_prefix = QLineEdit("usps")

        grid.addWidget(QLabel("邮箱 API"), 0, 0)
        grid.addWidget(self.mailbox_url, 0, 1, 1, 3)
        grid.addWidget(QLabel("API Token"), 1, 0)
        grid.addWidget(self.mailbox_token, 1, 1, 1, 2)
        self.load_token_button = QPushButton("载入 Token 文件")
        self.load_token_button.clicked.connect(self.load_token_file)
        grid.addWidget(self.load_token_button, 1, 3)
        grid.addWidget(QLabel("邮箱域名"), 2, 0)
        grid.addWidget(self.mailbox_domain, 2, 1)
        grid.addWidget(QLabel("地址前缀"), 2, 2)
        grid.addWidget(self.mailbox_prefix, 2, 3)

        controls = QHBoxLayout()
        self.mail_timeout = make_spin(30, 600, 180, " 秒")
        self.poll_interval = make_spin(1, 30, 3, " 秒")
        self.row_delay = make_spin(0, 300, 5, " 秒")
        self.retry_count = make_spin(0, 3, 1, " 次")
        self.headless = QCheckBox("后台浏览器")
        for label, control in (
            ("邮件超时", self.mail_timeout),
            ("轮询间隔", self.poll_interval),
            ("行间延迟", self.row_delay),
            ("失败重试", self.retry_count),
        ):
            form = QFormLayout()
            form.addRow(label, control)
            controls.addLayout(form)
        controls.addWidget(self.headless)
        controls.addStretch()
        self.test_mail_button = QPushButton("测试邮箱连接")
        self.test_mail_button.clicked.connect(self.test_mail_connection)
        controls.addWidget(self.test_mail_button)
        grid.addLayout(controls, 3, 0, 1, 4)
        return box

    def _build_actions(self) -> QGroupBox:
        box = QGroupBox("批量任务")
        layout = QHBoxLayout(box)
        self.template_button = QPushButton("生成 CSV 模板")
        self.template_button.clicked.connect(self.save_template)
        self.import_button = QPushButton("导入 CSV")
        self.import_button.clicked.connect(self.import_csv)
        self.checkpoint_button = QPushButton("载入检查点")
        self.checkpoint_button.clicked.connect(self.restore_checkpoint)
        self.start_button = QPushButton("开始全部")
        self.start_button.setObjectName("primaryButton")
        self.start_button.clicked.connect(self.start_all)
        self.stop_button = QPushButton("停止")
        self.stop_button.clicked.connect(self.stop_batch)
        self.retry_button = QPushButton("重试未完成项")
        self.retry_button.clicked.connect(self.retry_failed)
        self.export_button = QPushButton("导出结果")
        self.export_button.clicked.connect(self.export_csv)
        self.export_sensitive = QCheckBox("含密码与安全答案")
        self.clear_button = QPushButton("清空")
        self.clear_button.clicked.connect(self.clear_rows)
        for button in (
            self.template_button,
            self.import_button,
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
        table = QTableWidget(0, 8)
        table.setHorizontalHeaderLabels(
            ["序号", "账号类型", "邮箱", "用户名", "姓名", "状态", "阶段", "结果说明"]
        )
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setAlternatingRowColors(True)
        header = table.horizontalHeader()
        for column in (0, 1, 3, 4, 5, 6):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(7, QHeaderView.Stretch)
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
        self.log_view.append("已载入 Token 文件，内容未写入日志。")

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
        try:
            rows = load_registration_csv(
                path,
                mailbox_domain=self.mailbox_domain.text(),
                mailbox_prefix=self.mailbox_prefix.text(),
            )
        except Exception as exc:
            QMessageBox.critical(self, "导入失败", str(exc))
            return
        self.rows = rows
        self.results = [RegistrationResult("pending", "queued", "等待执行") for _ in rows]
        self._render_rows()
        self._set_running(False)
        self._save_checkpoint()
        self.log_view.append(f"已导入 {len(rows)} 条注册数据。")

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
        indices = runnable_indices(self.results)
        if not indices:
            QMessageBox.information(
                self, "没有可执行项", "所有任务均已成功或需要人工核对，未重复提交。"
            )
            return
        self._start_indices(indices)

    def retry_failed(self) -> None:
        indices = runnable_indices(self.results)
        if not indices:
            QMessageBox.information(self, "无需重试", "当前没有未完成项。")
            return
        self._start_indices(indices)

    def _start_indices(self, indices: list[int]) -> None:
        if not self.rows:
            QMessageBox.warning(self, "没有任务", "请先导入 CSV。")
            return
        if not indices or self.worker:
            return
        self._set_running(True)
        QApplication.processEvents()
        try:
            self._mailbox_client().verify_access()
        except Exception as exc:
            self._set_running(False)
            QMessageBox.warning(self, "邮箱预检失败", str(exc))
            return
        for index, email in rotate_manual_retry_addresses(self.rows, self.results, indices):
            self.table.item(index, 2).setText(email)
            self.log_view.append(f"{self.rows[index].username}: 已为重试切换邮箱地址。")
        self._save_checkpoint()
        items = [(index, self.rows[index]) for index in indices]
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
        )
        self.worker.row_attempt.connect(self.on_row_attempt)
        self.worker.row_result.connect(self.on_row_result)
        self.worker.row_email_changed.connect(self.on_row_email_changed)
        self.worker.log.connect(self.log_view.append)
        self.worker.finished.connect(self.on_batch_finished)
        self.worker.start()

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

    def on_row_email_changed(self, index: int, email: str) -> None:
        self.rows[index].email = email
        self.table.item(index, 2).setText(email)
        self.log_view.append(f"{self.rows[index].username}: 重试已切换到新的邮箱地址。")

    def on_batch_finished(self) -> None:
        if self.worker:
            self._merge_worker_results(self.worker)
        self.worker = None
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
                "文件将包含账号密码和安全问题答案，请只保存到受控目录。",
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
        self.table.setRowCount(0)
        self._update_summary()
        self._set_running(False)

    def _render_rows(self) -> None:
        self.table.setRowCount(len(self.rows))
        for index, data in enumerate(self.rows):
            values = [
                str(index + 1),
                data.account_type.replace(" Account", ""),
                data.email,
                data.username,
                f"{data.first_name} {data.last_name}",
                "",
                "",
                "",
            ]
            for column, value in enumerate(values):
                self.table.setItem(index, column, QTableWidgetItem(value))
            self._render_result(index)
        self._update_summary()

    def _render_result(self, index: int) -> None:
        result = self.results[index]
        labels = {
            "pending": "等待",
            "running": "运行中",
            "success": "成功",
            "failed": "失败",
            "stopped": "已停止",
        }
        self.table.item(index, 5).setText(labels.get(result.status, result.status))
        self.table.item(index, 6).setText(result.stage)
        self.table.item(index, 7).setText(result.message)

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
                self.table.item(index, 2).setText(self.rows[index].email)
                if self.table.item(index, 5):
                    self._render_result(index)
        if changed:
            self._save_checkpoint()

    def _set_running(self, running: bool) -> None:
        self.start_button.setEnabled(not running and bool(self.rows))
        self.stop_button.setEnabled(running)
        self.retry_button.setEnabled(not running and bool(self.rows))
        self.import_button.setEnabled(not running)
        self.checkpoint_button.setEnabled(not running)
        self.clear_button.setEnabled(not running and bool(self.rows))
        self.export_button.setEnabled(not running and bool(self.rows))
        self.test_mail_button.setEnabled(not running)

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

    def _apply_style(self) -> None:
        self.setStyleSheet(APP_STYLE)


def main() -> int:
    if getattr(sys, "frozen", False):
        browser_dir = Path(sys.executable).resolve().parent / "playwright-browsers"
        if browser_dir.is_dir():
            os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(browser_dir))
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
