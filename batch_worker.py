from __future__ import annotations

from pathlib import Path
from threading import Event

from PySide6.QtCore import QThread, Signal

from batch_engine import process_batch
from batch_io import retry_mailbox_address
from checkpoint import save_checkpoint
from mailbox_client import MailboxClient
from models import RegistrationData, RegistrationResult
from usps_automation import AutomationConfig, UspsRegistrationRunner


class BatchWorker(QThread):
    row_attempt = Signal(int, int, int)
    row_result = Signal(int, object)
    row_email_changed = Signal(int, str)
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
        self.stop_event = Event()
        self.results: dict[int, RegistrationResult] = {}

    def stop(self) -> None:
        self.stop_event.set()

    def run(self) -> None:
        mailbox = MailboxClient(self.mailbox_base_url, self.mailbox_token)
        original_emails = {index: data.email for index, data in self.items}

        def prepare_attempt(index: int, data: RegistrationData, attempt: int) -> None:
            if attempt < 2:
                return
            data.email = retry_mailbox_address(original_emails[index], attempt)
            self.row_email_changed.emit(index, data.email)

        def run_one(data: RegistrationData) -> RegistrationResult:
            config = AutomationConfig(
                headless=self.headless,
                mailbox_timeout_seconds=self.mailbox_timeout_seconds,
                mailbox_poll_seconds=self.poll_seconds,
                artifact_dir=self.artifact_dir,
            )
            runner = UspsRegistrationRunner(config)
            return runner.run(data, mailbox, self.stop_event, self.log.emit)

        def record_attempt(index: int, attempt: int, total: int) -> None:
            self.checkpoint_results[index] = RegistrationResult(
                "running", "browser_start", f"尝试 {attempt}/{total}"
            )
            save_checkpoint(self.checkpoint_path, self.checkpoint_rows, self.checkpoint_results)
            self.row_attempt.emit(index, attempt, total)

        def record_result(index: int, result: RegistrationResult) -> None:
            self.results[index] = result
            self.checkpoint_results[index] = result
            save_checkpoint(self.checkpoint_path, self.checkpoint_rows, self.checkpoint_results)
            self.row_result.emit(index, result)

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
        )
        self.batch_done.emit()
