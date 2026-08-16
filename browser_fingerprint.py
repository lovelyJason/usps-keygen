from __future__ import annotations

import json
import secrets
import string
from dataclasses import asdict, dataclass

WINDOWS_USER_AGENTS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
)
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
    user_agent: str
    width: int
    height: int
    timezone_id: str
    hardware_concurrency: int
    device_memory: int
    device_scale_factor: float

    def serialized(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))

    def summary(self) -> str:
        chrome = self.user_agent.split("Chrome/", 1)[1].split(".", 1)[0]
        return f"{self.fingerprint_id} · Chrome {chrome} · Win10"

    def context_options(self) -> dict:
        return {
            "user_agent": self.user_agent,
            "viewport": {"width": self.width, "height": self.height},
            "screen": {"width": self.width, "height": self.height},
            "locale": "en-US",
            "timezone_id": self.timezone_id,
            "device_scale_factor": self.device_scale_factor,
            "color_scheme": "light",
        }

    def init_script(self) -> str:
        values = json.dumps(
            {
                "hardwareConcurrency": self.hardware_concurrency,
                "deviceMemory": self.device_memory,
            }
        )
        return (
            f"const fp={values};"
            "Object.defineProperty(navigator,'hardwareConcurrency',{get:()=>fp.hardwareConcurrency});"
            "Object.defineProperty(navigator,'deviceMemory',{get:()=>fp.deviceMemory});"
            "Object.defineProperty(navigator,'platform',{get:()=> 'Win32'});"
            "Object.defineProperty(navigator,'webdriver',{get:()=> undefined});"
        )


def generate_browser_fingerprint() -> BrowserFingerprint:
    width, height = secrets.choice(VIEWPORTS)
    return BrowserFingerprint(
        fingerprint_id=_random_id(),
        user_agent=secrets.choice(WINDOWS_USER_AGENTS),
        width=width,
        height=height,
        timezone_id=secrets.choice(US_TIMEZONES),
        hardware_concurrency=secrets.choice((4, 8, 12, 16)),
        device_memory=secrets.choice((4, 8, 16)),
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
