import json
import ssl
import threading
from urllib.error import URLError

import pytest

import mailbox_client
from mailbox_client import (
    MailboxClient,
    MailboxConnectionError,
    MailboxProtocolError,
    VerificationMessage,
    extract_usps_validation_url,
)


class Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


def test_extract_usps_validation_url_accepts_only_expected_host():
    good = "https://reg.usps.com/gateway/complete?etoken=abc%2B123"
    html = f'<a href="{good}">Continue</a><a href="https://evil.example/gateway/complete?etoken=x">bad</a>'
    assert extract_usps_validation_url(html) == good


def test_extract_usps_validation_url_decodes_html_entities():
    url = extract_usps_validation_url(
        '<a href="https://reg.usps.com/gateway/complete?etoken=a&amp;source=mail">Continue</a>'
    )
    assert url == "https://reg.usps.com/gateway/complete?etoken=a&source=mail"


def test_extracts_validation_link_from_natural_language_text():
    content = "Complete registration: https://reg.usps.com/gateway/complete?etoken=abc&source=mail"
    assert extract_usps_validation_url(content) == (
        "https://reg.usps.com/gateway/complete?etoken=abc&source=mail"
    )


def test_rejects_longer_paths_that_start_like_validation_link():
    for suffix in ("-help?topic=registration", ".html?topic=registration", ";jsessionid=x"):
        assert extract_usps_validation_url(f"https://reg.usps.com/gateway/complete{suffix}") is None


def test_poll_ignores_old_mail_and_returns_new_link(monkeypatch):
    client = MailboxClient("https://mail.example", "secret")
    responses = {
        "/api/list": {
            "items": [
                {"id": 1, "received_at": 900, "code": "111111", "sender": "old", "subject": "old"},
                {
                    "id": 2,
                    "received_at": 1100,
                    "code": "",
                    "sender": "USPostalService@usps.com",
                    "subject": "Validate",
                },
            ]
        },
        "/api/mail": {
            "id": 2,
            "html": '<a href="https://reg.usps.com/gateway/complete?etoken=fresh">Continue</a>',
            "body": "",
        },
    }

    def fake_request(path, query=None):
        return responses[path]

    monkeypatch.setattr(client, "_request_json", fake_request)
    message = client.poll_verification(
        "new@velydora.com", after_ms=1000, timeout_seconds=1, poll_interval=0
    )

    assert message == VerificationMessage(
        kind="link",
        value="https://reg.usps.com/gateway/complete?etoken=fresh",
        mail_id=2,
        sender="USPostalService@usps.com",
        subject="Validate",
        received_at=1100,
    )


def test_poll_returns_new_numeric_code(monkeypatch):
    client = MailboxClient("https://mail.example", "secret")
    monkeypatch.setattr(
        client,
        "_request_json",
        lambda path, query=None: {
            "items": [
                {
                    "id": 3,
                    "received_at": 1200,
                    "code": "483920",
                    "sender": "USPostalService@usps.com",
                    "subject": "Code",
                }
            ]
        },
    )
    message = client.poll_verification("a@b.com", 1000, 1, 0)
    assert message.kind == "otp"
    assert message.value == "483920"


def test_poll_requires_mail_id_newer_than_attempt_baseline(monkeypatch):
    client = MailboxClient("https://mail.example", "secret")
    now = int(__import__("time").time() * 1000)
    monkeypatch.setattr(
        client,
        "_request_json",
        lambda path, query=None: {
            "items": [
                {
                    "id": 7,
                    "received_at": now,
                    "expires_at": now + 60_000,
                    "consumed": 0,
                    "code": "111111",
                    "sender": "USPostalService@usps.com",
                    "subject": "Old attempt",
                },
                {
                    "id": 8,
                    "received_at": now,
                    "expires_at": now + 60_000,
                    "consumed": 0,
                    "code": "222222",
                    "sender": "USPostalService@usps.com",
                    "subject": "Current attempt",
                },
            ]
        },
    )

    message = client.poll_verification("a@b.com", now, 1, 0, after_mail_id=7)
    assert message.mail_id == 8
    assert message.value == "222222"


def test_mail_id_baseline_tolerates_worker_clock_skew(monkeypatch):
    client = MailboxClient("https://mail.example", "secret")
    local_now = int(__import__("time").time() * 1000)
    monkeypatch.setattr(
        client,
        "_request_json",
        lambda path, query=None: {
            "items": [
                {
                    "id": 8,
                    "received_at": local_now - 1,
                    "expires_at": local_now + 60_000,
                    "consumed": 0,
                    "code": "222222",
                    "sender": "USPostalService@usps.com",
                    "subject": "Current attempt",
                }
            ]
        },
    )
    message = client.poll_verification("a@b.com", local_now, 1, 0, after_mail_id=7)
    assert message.mail_id == 8


def test_latest_mail_id_returns_zero_for_empty_mailbox(monkeypatch):
    client = MailboxClient("https://mail.example", "secret")
    monkeypatch.setattr(client, "_request_json", lambda path, query=None: {"items": []})
    assert client.latest_mail_id("a@b.com") == 0


def test_consume_otp_detects_a_different_code(monkeypatch):
    client = MailboxClient("https://mail.example", "secret")
    monkeypatch.setattr(
        client,
        "_request_json",
        lambda path, query=None, method="GET", body=None: {"code": "999999", "consumed": True},
    )
    with pytest.raises(MailboxProtocolError):
        client.consume_otp(7, "a@b.com", expected_code="111111")


def test_poll_ignores_consumed_expired_and_non_usps_messages(monkeypatch):
    client = MailboxClient("https://mail.example", "secret")
    now = int(__import__("time").time() * 1000)
    monkeypatch.setattr(
        client,
        "_request_json",
        lambda path, query=None: {
            "items": [
                {
                    "id": 1,
                    "received_at": now,
                    "expires_at": now + 60_000,
                    "consumed": 0,
                    "code": "999999",
                    "sender": "other@example.com",
                    "subject": "Other",
                },
                {
                    "id": 2,
                    "received_at": now,
                    "expires_at": now - 1,
                    "consumed": 0,
                    "code": "888888",
                    "sender": "USPostalService@usps.com",
                    "subject": "Expired",
                },
                {
                    "id": 3,
                    "received_at": now,
                    "expires_at": now + 60_000,
                    "consumed": 1,
                    "code": "777777",
                    "sender": "USPostalService@usps.com",
                    "subject": "Consumed",
                },
                {
                    "id": 4,
                    "received_at": now,
                    "expires_at": now + 60_000,
                    "consumed": 0,
                    "code": "483920",
                    "sender": "USPostalService@usps.com",
                    "subject": "USPS Code",
                },
            ]
        },
    )
    message = client.poll_verification("a@b.com", now - 1, 1, 0)
    assert message.mail_id == 4
    assert message.value == "483920"


def test_poll_can_be_stopped(monkeypatch):
    client = MailboxClient("https://mail.example", "secret")
    monkeypatch.setattr(client, "_request_json", lambda path, query=None: {"items": []})
    stop = threading.Event()
    stop.set()
    with pytest.raises(InterruptedError):
        client.poll_verification("a@b.com", 0, 60, 1, stop_event=stop)


def test_get_retries_tls_unexpected_eof(monkeypatch):
    calls = 0

    def flaky_urlopen(_request, timeout):
        nonlocal calls
        calls += 1
        assert timeout == 15
        if calls < 3:
            raise URLError(ssl.SSLError("UNEXPECTED_EOF_WHILE_READING"))
        return Response({"status": "ok"})

    monkeypatch.setattr(mailbox_client, "urlopen", flaky_urlopen)
    client = MailboxClient("https://mail.example", "secret", retry_backoff=0)

    assert client.health() == {"status": "ok"}
    assert calls == 3


def test_poll_continues_after_exhausted_transient_connection_error(monkeypatch):
    client = MailboxClient("https://mail.example", "secret", retry_backoff=0)
    responses = [
        MailboxConnectionError("temporary TLS EOF"),
        {
            "items": [
                {
                    "id": 9,
                    "received_at": 1200,
                    "code": "483920",
                    "sender": "USPostalService@usps.com",
                    "subject": "Code",
                }
            ]
        },
    ]

    def request(*_args, **_kwargs):
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(client, "_request_json", request)

    message = client.poll_verification("a@b.com", 1000, 1, 0)
    assert message.value == "483920"
