from __future__ import annotations

import json
import re
import secrets
import string
import subprocess
import sys
from dataclasses import asdict, dataclass
from functools import lru_cache

VIEWPORTS = ((1366, 768), (1440, 900), (1536, 864), (1600, 900), (1920, 1080))
US_TIMEZONES = (
    "America/New_York",
    "America/Chicago",
    "America/Denver",
    "America/Los_Angeles",
    "America/Phoenix",
)


@dataclass(frozen=True, slots=True)
class BrowserFingerprint:
    fingerprint_id: str
    width: int
    height: int
    timezone_id: str
    device_scale_factor: float

    def serialized(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))

    def summary(self) -> str:
        return f"{self.fingerprint_id} · Chromium 原生 · Win10"

    def context_options(self, browser_executable: str | None = None) -> dict:
        options = {
            "channel": "chromium",
            "viewport": {"width": self.width, "height": self.height},
            "screen": {"width": self.width, "height": self.height},
            "locale": "en-US",
            "timezone_id": self.timezone_id,
            "device_scale_factor": self.device_scale_factor,
            "color_scheme": "light",
            "reduced_motion": "no-preference",
            "forced_colors": "none",
            "has_touch": False,
            "is_mobile": False,
        }
        user_agent = runtime_user_agent(browser_executable)
        if user_agent:
            options["user_agent"] = user_agent
        return options


def generate_browser_fingerprint() -> BrowserFingerprint:
    width, height = secrets.choice(VIEWPORTS)
    return BrowserFingerprint(
        fingerprint_id=_random_id(),
        width=width,
        height=height,
        timezone_id=secrets.choice(US_TIMEZONES),
        device_scale_factor=secrets.choice((1.0, 1.25)),
    )


def load_browser_fingerprint(value: str) -> BrowserFingerprint:
    if not value:
        return generate_browser_fingerprint()
    try:
        return BrowserFingerprint(**json.loads(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return generate_browser_fingerprint()


def fingerprint_summary(value: str) -> str:
    return load_browser_fingerprint(value).summary() if value else "待生成"


def _random_id(length: int = 10) -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "fp-" + "".join(secrets.choice(alphabet) for _ in range(length))


@lru_cache(maxsize=8)
def runtime_user_agent(
    browser_executable: str | None, platform_name: str | None = None
) -> str | None:
    if not browser_executable:
        return None
    try:
        result = subprocess.run(
            [browser_executable, "--version"],
            capture_output=True,
            check=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"\b(\d{2,3})\.\d+\.\d+\.\d+\b", result.stdout)
    if not match:
        return None
    major = match.group(1)
    platform_name = platform_name or sys.platform
    if platform_name == "win32":
        platform_token = "Windows NT 10.0; Win64; x64"
    elif platform_name == "darwin":
        platform_token = "Macintosh; Intel Mac OS X 10_15_7"
    else:
        platform_token = "X11; Linux x86_64"
    return (
        f"Mozilla/5.0 ({platform_token}) AppleWebKit/537.36 "
        f"(KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"
    )
