from subprocess import CompletedProcess

import browser_fingerprint
from browser_fingerprint import (
    fingerprint_summary,
    generate_browser_fingerprint,
    load_browser_fingerprint,
)


def test_generated_browser_fingerprints_are_fresh_and_windows_consistent():
    values = [generate_browser_fingerprint() for _ in range(20)]

    assert len({value.fingerprint_id for value in values}) == 20
    assert all(value.timezone_id.startswith("America/") for value in values)


def test_fingerprint_round_trip_and_context_options():
    original = generate_browser_fingerprint()
    restored = load_browser_fingerprint(original.serialized())

    assert restored == original
    assert "user_agent" not in restored.context_options()
    assert restored.context_options()["channel"] == "chromium"
    assert restored.context_options()["viewport"] == {
        "width": original.width,
        "height": original.height,
    }
    assert original.fingerprint_id in fingerprint_summary(original.serialized())
    assert "Chromium 原生" in fingerprint_summary(original.serialized())


def test_legacy_script_injected_fingerprint_is_replaced():
    legacy = (
        '{"fingerprint_id":"old","user_agent":"Chrome/149","width":1366,'
        '"height":768,"timezone_id":"America/Chicago","hardware_concurrency":8,'
        '"device_memory":8,"device_scale_factor":1.0}'
    )

    restored = load_browser_fingerprint(legacy)

    assert restored.fingerprint_id != "old"
    assert "user_agent" not in restored.context_options()


def test_runtime_user_agent_uses_actual_browser_major_and_platform(monkeypatch):
    browser_fingerprint.runtime_user_agent.cache_clear()
    monkeypatch.setattr(
        browser_fingerprint.subprocess,
        "run",
        lambda *_args, **_kwargs: CompletedProcess(
            args=[], returncode=0, stdout="Google Chrome for Testing 149.0.7827.55\n"
        ),
    )

    user_agent = browser_fingerprint.runtime_user_agent("chrome.exe", "win32")

    assert "Windows NT 10.0; Win64; x64" in user_agent
    assert "Chrome/149.0.0.0" in user_agent
    assert "HeadlessChrome" not in user_agent
