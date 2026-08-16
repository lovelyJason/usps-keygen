from __future__ import annotations

import html
import http.client
import json
import re
import ssl
import time
from dataclasses import dataclass
from threading import Event
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen


class MailboxError(RuntimeError):
    pass


class MailboxAuthError(MailboxError):
    pass


class MailboxProtocolError(MailboxError):
    pass


class MailboxConnectionError(MailboxError):
    pass


class MailboxTimeoutError(MailboxError):
    pass


@dataclass(frozen=True, slots=True)
class VerificationMessage:
    kind: str
    value: str
    mail_id: int
    sender: str
    subject: str
    received_at: int


class MailboxClient:
    def __init__(
        self,
        base_url: str,
        api_token: str,
        request_timeout: float = 15,
        request_attempts: int = 3,
        retry_backoff: float = 0.5,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_token = api_token.strip()
        self.request_timeout = request_timeout
        self.request_attempts = max(1, request_attempts)
        self.retry_backoff = max(0, retry_backoff)
        if not self.base_url.startswith("https://"):
            raise ValueError("邮箱服务地址必须使用 HTTPS")
        if not self.api_token:
            raise ValueError("邮箱 API Token 不能为空")

    def health(self) -> dict[str, Any]:
        return self._request_json("/health")

    def verify_access(self) -> None:
        payload = self._request_json("/api/list", {"limit": 1})
        if not isinstance(payload.get("items"), list):
            raise MailboxProtocolError("邮箱鉴权检查返回值缺少 items 数组")

    def latest_mail_id(self, address: str) -> int:
        payload = self._request_json("/api/list", {"address": address.lower().strip(), "limit": 1})
        items = payload.get("items")
        if not isinstance(items, list):
            raise MailboxProtocolError("邮箱接口缺少 items 数组")
        return max((int(item.get("id", 0)) for item in items), default=0)

    def poll_verification(
        self,
        address: str,
        after_ms: int,
        timeout_seconds: float,
        poll_interval: float = 3,
        stop_event: Event | None = None,
        after_mail_id: int | None = None,
    ) -> VerificationMessage:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() <= deadline:
            if stop_event and stop_event.is_set():
                raise InterruptedError("邮箱轮询已停止")
            try:
                payload = self._request_json(
                    "/api/list", {"address": address.lower().strip(), "limit": 20}
                )
                message = self._find_verification(payload, after_ms, after_mail_id)
                if message:
                    return message
            except MailboxConnectionError:
                pass
            self._poll_wait(poll_interval, stop_event)
        raise MailboxTimeoutError(f"等待 {address} 的验证邮件超时")

    def _find_verification(
        self, payload: dict[str, Any], after_ms: int, after_mail_id: int | None
    ) -> VerificationMessage | None:
        items = payload.get("items")
        if not isinstance(items, list):
            raise MailboxProtocolError("邮箱接口缺少 items 数组")
        for item in sorted(items, key=lambda value: int(value.get("received_at", 0)), reverse=True):
            mail_id = int(item.get("id", 0))
            received_at = int(item.get("received_at", 0))
            expires_at = int(item.get("expires_at", 2**63 - 1))
            consumed = int(item.get("consumed", 0))
            sender = str(item.get("sender") or "")
            stale = (
                mail_id <= after_mail_id
                if after_mail_id is not None
                else received_at < after_ms
            )
            if stale or expires_at <= int(time.time() * 1000) or consumed:
                continue
            if not _is_usps_sender(sender):
                continue
            code = str(item.get("code") or "").strip()
            common = {
                "mail_id": mail_id,
                "sender": sender,
                "subject": str(item.get("subject") or ""),
                "received_at": received_at,
            }
            if code:
                return VerificationMessage("otp", code, **common)
            mail = self._request_json("/api/mail", {"id": mail_id})
            action_url = extract_usps_validation_url(
                f"{mail.get('html') or ''}\n{mail.get('body') or ''}"
            )
            if action_url:
                return VerificationMessage("link", action_url, **common)
        return None

    @staticmethod
    def _poll_wait(poll_interval: float, stop_event: Event | None) -> None:
        if not poll_interval:
            return
        if stop_event and stop_event.wait(poll_interval):
            raise InterruptedError("邮箱轮询已停止")
        if not stop_event:
            time.sleep(poll_interval)

    def consume_otp(self, mail_id: int, address: str, expected_code: str) -> None:
        payload = self._request_json(
            "/api/otp/consume",
            method="POST",
            body={
                "mail_id": mail_id,
                "address": address.lower().strip(),
                "code": expected_code,
            },
        )
        consumed = str(payload.get("code") or "")
        if consumed != expected_code or payload.get("consumed") is not True:
            raise MailboxProtocolError("邮箱服务消费到的验证码与已提交验证码不一致")

    def _request_json(
        self,
        path: str,
        query: dict[str, Any] | None = None,
        method: str = "GET",
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{urlencode(query)}"
        encoded_body = json.dumps(body).encode("utf-8") if body is not None else None
        request = Request(
            url,
            data=encoded_body,
            method=method,
            headers={
                "Authorization": f"Bearer {self.api_token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "usps-registration-mvp/2.0",
            },
        )
        raw = self._read_with_retry(request, method)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise MailboxProtocolError("邮箱接口返回了无效 JSON") from exc
        if not isinstance(payload, dict):
            raise MailboxProtocolError("邮箱接口返回值不是 JSON 对象")
        return payload

    def _read_with_retry(self, request: Request, method: str) -> str:
        attempts = self.request_attempts if method == "GET" else 1
        for attempt in range(1, attempts + 1):
            try:
                with urlopen(request, timeout=self.request_timeout) as response:
                    return response.read().decode("utf-8")
            except HTTPError as exc:
                if exc.code in (401, 403):
                    raise MailboxAuthError("邮箱 API Token 无效或无访问权限") from exc
                try:
                    detail = exc.read().decode("utf-8")
                except Exception:
                    detail = ""
                raise MailboxError(f"邮箱接口返回 HTTP {exc.code}: {detail[:200]}") from exc
            except (
                URLError,
                TimeoutError,
                ConnectionError,
                ssl.SSLError,
                http.client.IncompleteRead,
                http.client.RemoteDisconnected,
            ) as exc:
                if attempt < attempts:
                    time.sleep(self.retry_backoff * (2 ** (attempt - 1)))
                    continue
                raise MailboxConnectionError(f"邮箱接口连接失败：{exc}") from exc
        raise AssertionError("unreachable")


def extract_usps_validation_url(content: str) -> str | None:
    decoded = html.unescape(content or "")
    boundary = r"(?=\?|[\s\"'<>),]|$)"
    pattern = rf"https://reg\.usps\.com/gateway/complete{boundary}(?:\?[^\s\"'<>]*)?"
    for match in re.finditer(pattern, decoded, re.IGNORECASE):
        candidate = match.group(0).rstrip(".,);")
        parsed = urlparse(candidate)
        if parsed.hostname == "reg.usps.com" and parsed.path == "/gateway/complete":
            return candidate
    return None


def _is_usps_sender(sender: str) -> bool:
    address = sender.rsplit("@", 1)[-1].strip().strip(">").casefold()
    return address == "usps.com" or address.endswith(".usps.com")
