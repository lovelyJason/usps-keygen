import pytest

from browser_runtime import validate_browser_signals


def test_browser_runtime_accepts_consistent_native_signals():
    validate_browser_signals(
        {
            "webdriver": False,
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "Chrome/149.0.0.0 Safari/537.36"
            ),
            "languages": ["en-US"],
            "chrome_present": True,
            "platform": "Win32",
            "ua_platform": "Windows",
        }
    )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("webdriver", True, "navigator.webdriver"),
        ("user_agent", "HeadlessChrome/149.0.0.0", "HeadlessChrome"),
        ("languages", [], "语言指纹"),
        ("chrome_present", False, "window.chrome"),
        ("platform", "MacIntel", "Client Hints"),
        ("ua_platform", "macOS", "Client Hints"),
    ],
)
def test_browser_runtime_rejects_detectable_signals(field, value, message):
    signals = {
        "webdriver": False,
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/149.0.0.0",
        "languages": ["en-US"],
        "chrome_present": True,
        "platform": "Win32",
        "ua_platform": "Windows",
    }
    signals[field] = value

    with pytest.raises(RuntimeError, match=message):
        validate_browser_signals(signals)
