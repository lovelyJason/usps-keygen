from __future__ import annotations

import re
import time
from collections.abc import Callable
from threading import Event

from models import RegistrationData


def wait_for_verification_code(
    data: RegistrationData,
    timeout_seconds: float,
    poll_seconds: float,
    stop_event: Event | None,
    verification_required: Callable[[], None] | None,
) -> str:
    data.verification_code = ""
    if verification_required:
        verification_required()
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if stop_event and stop_event.is_set():
            raise InterruptedError("用户已停止")
        code = data.verification_code.strip()
        if re.fullmatch(r"\d{4,10}", code):
            return code
        if stop_event:
            stop_event.wait(poll_seconds)
        else:
            time.sleep(poll_seconds)
    raise TimeoutError("等待人工输入验证码超时")
