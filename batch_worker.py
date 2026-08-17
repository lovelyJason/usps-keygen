from __future__ import annotations

import threading
from pathlib import Path
from threading import Event

from PySide6.QtCore import QThread, Signal

from batch_engine import MAX_BATCH_WORKERS, process_batch
from batch_io import retry_mailbox_address
from browser_fingerprint import fingerprint_summary, generate_browser_fingerprint
from checkpoint import save_checkpoint
from mailbox_client import MailboxClient
from models import RegistrationData, RegistrationResult
from usps_automation import AutomationConfig, UspsRegistrationRunner


class BatchWorker(QThread):
    row_attempt = Signal(int, int, int)
    row_result = Signal(int, object)
    row_email_changed = Signal(int, str)
    row_fingerprint_changed = Signal(int, str)
    row_verification_required = Signal(int)
    log = Signal(str)
    batch_done = Signal()

    def __init__(
        self,
        items: list[tuple[int, RegistrationData]],
        mailbox_base_url: str,
        mailbox_token: str,
        mailbox_timeout_seconds: int,
        poll_seconds: float,
        delay_seconds: float,
        retries: int,
        headless: bool,
        artifact_dir: Path,
        checkpoint_path: Path,
        all_rows: list[RegistrationData],
        initial_results: list[RegistrationResult],
        failure_hold_seconds: int = 30,
        execution_mode: str = "sequential",
        worker_count: int = 1,
        manual_mailbox_takeover: bool = False,
    ):
        super().__init__()
        self.items = items
        self.mailbox_base_url = mailbox_base_url
        self.mailbox_token = mailbox_token
        self.mailbox_timeout_seconds = mailbox_timeout_seconds
        self.poll_seconds = poll_seconds
        self.delay_seconds = delay_seconds
        self.retries = retries
        self.headless = headless
        self.artifact_dir = artifact_dir
        self.checkpoint_path = checkpoint_path
        self.checkpoint_rows = all_rows
        self.checkpoint_results = list(initial_results)
        self.failure_hold_seconds = failure_hold_seconds
        self.execution_mode = execution_mode if execution_mode == "concurrent" else "sequential"
        self.worker_count = max(1, min(worker_count, MAX_BATCH_WORKERS))
        self.manual_mailbox_takeover = manual_mailbox_takeover
        self.stop_event = Event()
        self.results: dict[int, RegistrationResult] = {}
        self._checkpoint_lock = threading.Lock()

    def stop(self) -> None:
        self.stop_event.set()

    def run(self) -> None:
        original_emails = {index: data.email for index, data in self.items}

        def prepare_attempt(index: int, data: RegistrationData, attempt: int) -> None:
            data.browser_fingerprint = generate_browser_fingerprint().serialized()
            self.row_fingerprint_changed.emit(index, fingerprint_summary(data.browser_fingerprint))
            if attempt < 2:
                return
            if self.manual_mailbox_takeover:
                return
            data.email = retry_mailbox_address(original_emails[index], attempt)
            self.row_email_changed.emit(index, data.email)

        def run_one(data: RegistrationData) -> RegistrationResult:
            index = next(index for index, item in self.items if item is data)
            mailbox = (
                None
                if self.manual_mailbox_takeover
                else MailboxClient(self.mailbox_base_url, self.mailbox_token)
            )
            config = AutomationConfig(
                headless=self.headless,
                mailbox_timeout_seconds=self.mailbox_timeout_seconds,
                mailbox_poll_seconds=self.poll_seconds,
                artifact_dir=self.artifact_dir,
                failure_hold_seconds=self.failure_hold_seconds,
                manual_mailbox_takeover=self.manual_mailbox_takeover,
            )
            runner = UspsRegistrationRunner(config)

            def request_verification() -> None:
                with self._checkpoint_lock:
                    self.checkpoint_results[index] = RegistrationResult(
                        "running", "manual_verification", "等待输入验证码"
                    )
                    save_checkpoint(
                        self.checkpoint_path, self.checkpoint_rows, self.checkpoint_results
                    )
                self.row_verification_required.emit(index)

            if self.manual_mailbox_takeover:
                return runner.run(
                    data,
                    mailbox,
                    self.stop_event,
                    self.log.emit,
                    request_verification,
                )
            return runner.run(data, mailbox, self.stop_event, self.log.emit)

        def record_attempt(index: int, attempt: int, total: int) -> None:
            self.log.emit(
                f"{self.checkpoint_rows[index].username}: 开始第 {attempt}/{total} 次执行"
            )
            with self._checkpoint_lock:
                self.checkpoint_results[index] = RegistrationResult(
                    "running", "browser_start", f"尝试 {attempt}/{total}"
                )
                save_checkpoint(self.checkpoint_path, self.checkpoint_rows, self.checkpoint_results)
            self.row_attempt.emit(index, attempt, total)

        def record_result(index: int, result: RegistrationResult) -> None:
            with self._checkpoint_lock:
                self.results[index] = result
                self.checkpoint_results[index] = result
                save_checkpoint(self.checkpoint_path, self.checkpoint_rows, self.checkpoint_results)
            self.row_result.emit(index, result)

        effective_workers = (
            min(self.worker_count, len(self.items)) if self.execution_mode == "concurrent" else 1
        )
        mode_label = "并发" if effective_workers > 1 else "顺序"
        self.log.emit(f"执行模式：{mode_label}，浏览器线程数：{effective_workers}")

        self.results = process_batch(
            self.items,
            run_one,
            retries=self.retries,
            delay_seconds=self.delay_seconds,
            stop_event=self.stop_event,
            on_result=record_result,
            on_attempt=record_attempt,
            on_prepare_attempt=prepare_attempt,
            retry_backoff_seconds=2,
            max_workers=effective_workers,
        )
        self.batch_done.emit()
