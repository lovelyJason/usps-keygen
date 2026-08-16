from __future__ import annotations

import re
from enum import Enum
from urllib.parse import urlsplit


class PageState(Enum):
    WAITING_FOR_EMAIL = "waiting_for_email"
    CAPTCHA_REQUIRED = "captcha_required"
    IDENTITY_REJECTED = "identity_rejected"
    SERVICE_TIMEOUT = "service_timeout"
    SUCCESS = "success"
    UNKNOWN = "unknown"


FAILURE_MARKERS = (
    "? no",
    "account was not created",
    "account created successfully? no",
    "cannot confirm that your account",
    "correction:",
    "business account could not be created",
    "could not be confirmed",
    "registration complete failed",
    "verification failed",
    "not been created",
    "error occurred",
    "no account created",
    "registrationgatewayfailure",
    "statement is false",
    "verification is still pending",
    "do not sign in yet",
)

SUCCESS_HEADINGS = (
    "account has been created successfully",
    "your usps.com account has been created successfully",
)

SUCCESS_TITLES = (
    "account created",
    "registration success",
    "usps.com account created",
    "usps registration success",
)


def classify_page(url: str, title: str, body_text: str, primary_heading: str = "") -> PageState:
    combined = f"{url}\n{title}\n{body_text}".casefold()
    if "request timed out" in combined and "account could not be created" in combined:
        return PageState.SERVICE_TIMEOUT
    if "registrationgatewayfailure" in combined or (
        "business account could not be created" in combined
    ):
        return PageState.IDENTITY_REJECTED
    if any(marker in combined for marker in FAILURE_MARKERS):
        return PageState.UNKNOWN
    if "captcha" in combined or "i'm not a robot" in combined or "i am not a robot" in combined:
        return PageState.CAPTCHA_REQUIRED
    if "check your inbox to validate your email" in combined:
        return PageState.WAITING_FOR_EMAIL
    if _has_success_heading(primary_heading) and (
        _has_success_title(title) or _has_success_url(url)
    ):
        return PageState.SUCCESS
    return PageState.UNKNOWN


def classify_browser_page(page) -> PageState:
    heading = ""
    try:
        locator = page.locator("h1:visible").first
        if locator.count() and locator.is_visible():
            heading = locator.inner_text()
    except Exception:
        pass
    return classify_page(page.url, page.title(), page.locator("body").inner_text(), heading)


def _has_success_heading(heading: str) -> bool:
    normalized = re.sub(r"\s+", " ", heading).strip().casefold().rstrip(".! ")
    return normalized in SUCCESS_HEADINGS


def _has_success_title(title: str) -> bool:
    normalized = re.sub(r"\s+", " ", title).strip().casefold().rstrip(".! ")
    return normalized in SUCCESS_TITLES


def _has_success_url(url: str) -> bool:
    parsed = urlsplit(url)
    if parsed.hostname != "reg.usps.com":
        return False
    path = parsed.path.casefold().rstrip("/")
    return path.endswith(("/success", "/confirmation", "/complete"))
