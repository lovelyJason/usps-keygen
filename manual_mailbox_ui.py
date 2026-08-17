from __future__ import annotations

import os
import re

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QWidget,
)

from batch_engine import MAX_BATCH_WORKERS
from batch_io import materialize_csv_emails
from manual_verification import verification_value_kind
from models import RegistrationResult
from ui_controls import make_spin
from ui_style import APP_STYLE

DEFAULT_MAILBOX_URL = "https://velydora-mail-otp.hzj1248394650.workers.dev"
TOKEN_SETTINGS_KEY = "mailbox/api_token"
EMAIL_COLUMN = 3
VERIFICATION_COLUMN = 4


class ManualMailboxUiMixin:
    def _build_settings(self) -> QGroupBox:
        box = QGroupBox("邮箱与执行设置")
        self.settings_box = box
        grid = QGridLayout(box)
        grid.setContentsMargins(10, 7, 10, 8)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)

        self.mailbox_url = QLineEdit(DEFAULT_MAILBOX_URL)
        saved_token = str(self.settings.value(TOKEN_SETTINGS_KEY, "") or "")
        self.mailbox_token = QLineEdit(os.getenv("VELYDORA_API_TOKEN", saved_token))
        self.mailbox_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.mailbox_token.setPlaceholderText("Bearer Token，失去焦点后自动保存")
        self.mailbox_token.editingFinished.connect(self._persist_mailbox_token)
        self.mailbox_domain = QLineEdit("velydora.com")
        self.load_token_button = QPushButton("载入 Token 文件")
        self.load_token_button.clicked.connect(self.load_token_file)
        self.test_mail_button = QPushButton("测试邮箱连接")
        self.test_mail_button.clicked.connect(self.test_mail_connection)

        self.manual_mailbox_takeover = QCheckBox("接管邮箱（人工验证）")
        self.manual_mailbox_takeover.setChecked(True)
        self.manual_mailbox_takeover.setToolTip(
            "勾选后不调用线上邮箱 API；每行使用人工邮箱并等待手动输入验证码或验证链接"
        )
        self.execution_mode = QComboBox()
        self.execution_mode.addItem("顺序模式", "sequential")
        self.execution_mode.addItem("并发模式", "concurrent")
        self.execution_mode.currentIndexChanged.connect(self._on_execution_mode_changed)
        self.worker_count = make_spin(1, MAX_BATCH_WORKERS, 2, " 个")
        self.execution_mode.setCurrentIndex(1)
        self.worker_count.setToolTip(
            f"并发模式下同时运行的独立浏览器数量，硬上限 {MAX_BATCH_WORKERS}"
        )
        self.headless = QCheckBox("后台浏览器（无头模式）")
        self.headless.setChecked(True)
        self.headless.setToolTip("勾选后不显示 Chromium 窗口；排查页面问题时取消勾选")

        primary = QHBoxLayout()
        primary.setSpacing(10)
        primary.addWidget(self.manual_mailbox_takeover)
        primary.addSpacing(8)
        primary.addWidget(QLabel("执行模式"))
        primary.addWidget(self.execution_mode)
        primary.addWidget(QLabel("浏览器线程"))
        primary.addWidget(self.worker_count)
        primary.addSpacing(8)
        primary.addWidget(self.headless)
        primary.addStretch()
        grid.addLayout(primary, 0, 0)

        self.mailbox_panel = QWidget()
        mailbox_grid = QGridLayout(self.mailbox_panel)
        mailbox_grid.setContentsMargins(0, 0, 0, 0)
        mailbox_grid.setHorizontalSpacing(8)
        mailbox_grid.setVerticalSpacing(5)
        mailbox_grid.addWidget(QLabel("邮箱 API"), 0, 0)
        mailbox_grid.addWidget(self.mailbox_url, 0, 1, 1, 5)
        mailbox_grid.addWidget(QLabel("API Token"), 1, 0)
        mailbox_grid.addWidget(self.mailbox_token, 1, 1)
        mailbox_grid.addWidget(self.load_token_button, 1, 2)
        mailbox_grid.addWidget(QLabel("邮箱域名"), 1, 3)
        mailbox_grid.addWidget(self.mailbox_domain, 1, 4)
        mailbox_grid.addWidget(self.test_mail_button, 1, 5)
        mailbox_grid.setColumnStretch(1, 3)
        mailbox_grid.setColumnStretch(4, 1)
        grid.addWidget(self.mailbox_panel, 1, 0)

        self.mail_timeout = make_spin(30, 600, 180, " 秒")
        self.poll_interval = make_spin(1, 30, 3, " 秒")
        self.row_delay = make_spin(0, 300, 5, " 秒")
        self.retry_count = make_spin(0, 3, 1, " 次")
        self.failure_hold = make_spin(0, 300, 10, " 秒")
        tuning = QHBoxLayout()
        tuning.setSpacing(7)
        for label, control in (
            ("邮件超时", self.mail_timeout),
            ("轮询", self.poll_interval),
            ("行间延迟", self.row_delay),
            ("额外重试", self.retry_count),
            ("失败停留", self.failure_hold),
        ):
            tuning.addWidget(QLabel(label))
            tuning.addWidget(control)
        tuning.addStretch()
        grid.addLayout(tuning, 2, 0)

        self.manual_mailbox_takeover.stateChanged.connect(self._on_mailbox_mode_changed)
        self._on_mailbox_mode_changed()
        return box

    def _on_mailbox_mode_changed(self) -> None:
        takeover = self.manual_mailbox_takeover.isChecked()
        self.mailbox_panel.setVisible(not takeover)
        enabled = not takeover and not self._is_running
        for control in (
            self.mailbox_url,
            self.mailbox_token,
            self.load_token_button,
            self.mailbox_domain,
            self.test_mail_button,
        ):
            control.setEnabled(enabled)

    def _on_execution_mode_changed(self) -> None:
        concurrent = self.execution_mode.currentData() == "concurrent"
        self.worker_count.setEnabled(concurrent and self.worker is None)

    def _apply_style(self) -> None:
        self.setStyleSheet(APP_STYLE)

    def _manual_mailbox_preflight(self, indices: list[int]) -> bool:
        if not self.manual_mailbox_takeover.isChecked():
            return True
        missing = [index + 1 for index in indices if not self.rows[index].email.strip()]
        if missing:
            self._show_toast(f"所选任务第 {', '.join(map(str, missing))} 行未填写邮箱")
            return False
        invalid = [
            index + 1
            for index in indices
            if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", self.rows[index].email)
        ]
        if invalid:
            self._show_toast(f"所选任务第 {', '.join(map(str, invalid))} 行邮箱格式错误")
            return False
        for index in indices:
            self.rows[index].verification_code = ""
            self._set_verification_cell(index, "")
        return True

    def on_row_verification_required(self, index: int) -> None:
        self.rows[index].verification_code = ""
        self.results[index] = RegistrationResult(
            "running", "manual_verification", "等待输入验证码或验证链接"
        )
        self._render_result(index)
        self._set_verification_cell(index, "双击粘贴验证内容", waiting=True)
        self.table.scrollToItem(self.table.item(index, VERIFICATION_COLUMN))
        self.log_view.append(f"{self.rows[index].username}: 验证邮件已发送，等待人工输入。")
        self._show_toast(
            f"第 {index + 1} 行验证邮件已发送，请输入验证码或粘贴验证链接",
            error=False,
        )

    def _on_table_cell_double_clicked(self, row: int, column: int) -> None:
        if not self.manual_mailbox_takeover.isChecked():
            return
        if column == EMAIL_COLUMN:
            self._edit_manual_email(row)
        elif column == VERIFICATION_COLUMN:
            self._edit_manual_verification_value(row)

    def _edit_manual_email(self, row: int) -> None:
        if self.worker is not None:
            self._show_toast("任务运行中不能修改邮箱")
            return
        value, accepted = QInputDialog.getText(
            self, "填写邮箱", f"第 {row + 1} 行邮箱", text=self.rows[row].email
        )
        if not accepted:
            return
        value = value.strip()
        if value and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
            self._show_toast("邮箱格式不正确")
            return
        if value and any(
            index != row and item.email.casefold() == value.casefold()
            for index, item in enumerate(self.rows)
        ):
            self._show_toast("邮箱不能与其他任务重复")
            return
        self.rows[row].email = value
        self.table.item(row, EMAIL_COLUMN).setText(value)
        if self.current_csv_path:
            try:
                materialize_csv_emails(self.current_csv_path, self.rows)
            except Exception as exc:
                self.log_view.append(f"CSV 邮箱回写失败：{exc}")
        self._save_checkpoint()

    def _edit_manual_verification_value(self, row: int) -> None:
        result = self.results[row]
        if result.status != "running" or result.stage != "manual_verification":
            self._show_toast("该任务尚未进入人工验证阶段")
            return
        value, accepted = QInputDialog.getMultiLineText(
            self,
            "填写验证内容",
            f"第 {row + 1} 行：输入数字验证码，或粘贴 USPS 验证链接",
        )
        if not accepted:
            return
        value = value.strip()
        kind = verification_value_kind(value)
        if kind is None:
            self._show_toast("请输入 4-10 位数字验证码，或有效的 USPS 验证链接")
            return
        self.rows[row].verification_code = value
        self.results[row].message = "验证内容已输入，正在继续"
        self._render_result(row)
        display = value if kind == "otp" else "已输入验证链接"
        self._set_verification_cell(row, display, accepted=True)
        self.log_view.append(f"{self.rows[row].username}: 已输入验证内容，继续执行。")

    def _set_verification_cell(
        self, row: int, text: str, waiting: bool = False, accepted: bool = False
    ) -> None:
        item = self.table.item(row, VERIFICATION_COLUMN)
        if item is None:
            return
        item.setText(text)
        font = item.font()
        font.setBold(waiting or accepted)
        item.setFont(font)
        if waiting:
            item.setBackground(QColor("#ffe08a"))
            item.setForeground(QColor("#7a4600"))
        elif accepted:
            item.setBackground(QColor("#ccebd7"))
            item.setForeground(QColor("#155b34"))
        else:
            item.setData(Qt.ItemDataRole.BackgroundRole, None)
            item.setData(Qt.ItemDataRole.ForegroundRole, None)

    def _show_toast(self, message: str, error: bool = True) -> None:
        toast = QLabel(message, self.centralWidget())
        toast.setObjectName("errorToast" if error else "noticeToast")
        toast.setAlignment(Qt.AlignmentFlag.AlignCenter)
        toast.setWordWrap(True)
        toast.setMaximumWidth(620)
        toast.adjustSize()
        width = max(320, min(620, toast.sizeHint().width() + 36))
        toast.resize(width, max(44, toast.sizeHint().height() + 18))
        toast.move(max(12, (self.centralWidget().width() - width) // 2), 58)
        toast.show()
        toast.raise_()
        QTimer.singleShot(4500, toast.deleteLater)
