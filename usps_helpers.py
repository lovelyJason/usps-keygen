from __future__ import annotations

import os
import re
from datetime import UTC, datetime
from pathlib import Path


class RegistrationFlowError(RuntimeError):
    def __init__(self, stage: str, message: str):
        super().__init__(message)
        self.stage = stage


def require_mailbox(mailbox):
    if mailbox is None:
        raise RegistrationFlowError("mail", "自动邮箱服务未初始化")
    return mailbox


def otp_selectors() -> tuple[str, ...]:
    return (
        "input[autocomplete='one-time-code']",
        "input[name*='code' i]",
        "input[id*='code' i]",
    )


def visible_otp_input(page) -> bool:
    return any(
        page.locator(selector).count() and page.locator(selector).first.is_visible()
        for selector in otp_selectors()
    )


def fill_with_events(page, selector: str, value: str, required: bool = True) -> None:
    locator = page.locator(selector)
    if not locator.count() or not locator.is_visible():
        if required:
            raise RegistrationFlowError("form_fill", f"页面缺少字段 {selector}")
        return
    locator.fill(value)
    locator.evaluate(
        "element => { "
        "for (const type of ['input','keyup','change','blur']) "
        "element.dispatchEvent(new Event(type, {bubbles:true})); "
        "}"
    )


def visible_errors(page) -> str:
    messages = []
    selector = ".error-txt:visible, .error:visible, #validate-form-action-errors:visible"
    for locator in page.locator(selector).all():
        text = locator.inner_text().strip()
        if text and text not in messages:
            messages.append(text)
    return "；".join(messages)[:1_000]


def page_url(page) -> str:
    if page is None or page.is_closed():
        return ""
    return page.url


def safe_slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "-", value).strip("-.")[:64] or "record"


def iso_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


def save_redacted_screenshot(
    page, artifact_dir: Path | None, username: str, stage: str
) -> Path | None:
    if not page or page.is_closed() or not artifact_dir:
        return None
    try:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(artifact_dir, 0o700)
        page.add_style_tag(
            content="input,select,textarea,[contenteditable='true']{filter:blur(10px)!important}"
        )
        target = artifact_dir / f"{safe_slug(username)}-{safe_slug(stage)}.png"
        page.screenshot(path=str(target), full_page=True)
        os.chmod(target, 0o600)
        return target
    except Exception:
        return None
