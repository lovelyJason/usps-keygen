from __future__ import annotations

import tempfile
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any

from browser_fingerprint import generate_browser_fingerprint


class _ProbeHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = b"<!doctype html><title>Browser runtime probe</title>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


@contextmanager
def _local_probe_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ProbeHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def validate_browser_signals(signals: dict[str, Any]) -> None:
    if signals.get("webdriver") is not False:
        raise RuntimeError("浏览器仍暴露 navigator.webdriver")
    if "HeadlessChrome" in str(signals.get("user_agent", "")):
        raise RuntimeError("浏览器仍暴露 HeadlessChrome UA")
    if signals.get("languages") != ["en-US"]:
        raise RuntimeError("浏览器语言指纹不一致")
    if signals.get("chrome_present") is not True:
        raise RuntimeError("浏览器缺少原生 window.chrome 对象")
    user_agent = str(signals.get("user_agent", ""))
    platform = signals.get("platform")
    ua_platform = signals.get("ua_platform")
    if "Windows NT" in user_agent and (platform != "Win32" or ua_platform != "Windows"):
        raise RuntimeError("Windows UA、navigator.platform 与 Client Hints 不一致")
    if "Mac OS X" in user_agent and (platform != "MacIntel" or ua_platform != "macOS"):
        raise RuntimeError("macOS UA、navigator.platform 与 Client Hints 不一致")


def smoke_browser_runtime() -> dict[str, Any]:
    from patchright.sync_api import sync_playwright

    fingerprint = generate_browser_fingerprint()
    with (
        tempfile.TemporaryDirectory(prefix="usps-browser-smoke-") as profile,
        sync_playwright() as playwright,
        _local_probe_url() as probe_url,
    ):
        context = playwright.chromium.launch_persistent_context(
            profile,
            headless=True,
            **fingerprint.context_options(playwright.chromium.executable_path),
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(probe_url, wait_until="domcontentloaded")
            signals = page.evaluate(
                """() => ({
                    webdriver: navigator.webdriver,
                    user_agent: navigator.userAgent,
                    languages: Array.from(navigator.languages),
                    chrome_present: Boolean(window.chrome),
                    platform: navigator.platform,
                    ua_platform: navigator.userAgentData?.platform ?? null,
                })"""
            )
            validate_browser_signals(signals)
            return signals
        finally:
            context.close()
