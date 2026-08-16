from browser_fingerprint import (
    fingerprint_summary,
    generate_browser_fingerprint,
    load_browser_fingerprint,
)


def test_generated_browser_fingerprints_are_fresh_and_windows_consistent():
    values = [generate_browser_fingerprint() for _ in range(20)]

    assert len({value.fingerprint_id for value in values}) == 20
    assert all("Windows NT 10.0" in value.user_agent for value in values)
    assert all("Chrome/" in value.user_agent for value in values)
    assert all(value.timezone_id.startswith("America/") for value in values)


def test_fingerprint_round_trip_and_context_options():
    original = generate_browser_fingerprint()
    restored = load_browser_fingerprint(original.serialized())

    assert restored == original
    assert restored.context_options()["user_agent"] == original.user_agent
    assert restored.context_options()["viewport"] == {
        "width": original.width,
        "height": original.height,
    }
    assert original.fingerprint_id in fingerprint_summary(original.serialized())
    assert "webdriver" in original.init_script()
