from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from threading import Event
from urllib.parse import parse_qs, urlsplit

from models import RegistrationData


@dataclass(frozen=True, slots=True)
class ManualVerificationValue:
    kind: str
    value: str


def verification_value_kind(value: str) -> str | None:
    value = value.strip()
    if re.fullmatch(r"\d{4,10}", value):
        return "otp"
    parsed = urlsplit(value)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme == "https"
        and parsed.netloc == "reg.usps.com"
        and parsed.path == "/gateway/complete"
        and not parsed.fragment
        and set(query) == {"etoken"}
        and len(query["etoken"]) == 1
        and bool(query["etoken"][0])
    ):
        return "link"
    return None


def wait_for_verification_value(
    data: RegistrationData,
    timeout_seconds: float,
    poll_seconds: float,
    stop_event: Event | None,
    verification_required: Callable[[], None] | None,
) -> ManualVerificationValue:
    data.verification_code = ""
    if verification_required:
        verification_required()
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if stop_event and stop_event.is_set():
            raise InterruptedError("用户已停止")
        value = data.verification_code.strip()
        kind = verification_value_kind(value)
        if kind:
            data.verification_code = ""
            return ManualVerificationValue(kind, value)
        if stop_event:
            stop_event.wait(poll_seconds)
        else:
            time.sleep(poll_seconds)
    raise TimeoutError("等待人工输入验证码或验证链接超时")
