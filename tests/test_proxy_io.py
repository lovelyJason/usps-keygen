import pytest

from proxy_io import load_proxy_file, parse_proxy, proxy_display, proxy_for_playwright


def test_four_part_proxy_is_normalized_and_credentials_are_separate():
    value = parse_proxy("127.0.0.1:8080:test-user:test-pass")

    assert value.server == "http://127.0.0.1:8080"
    assert value.username == "test-user"
    assert value.password == "test-pass"
    assert value.display() == "127.0.0.1:8080"
    assert value.playwright() == {
        "server": "http://127.0.0.1:8080",
        "username": "test-user",
        "password": "test-pass",
    }


def test_proxy_file_supports_comments_blank_lines_and_gb18030(tmp_path):
    path = tmp_path / "proxies.txt"
    path.write_bytes(
        "# 代理列表\n\n127.0.0.1:8001:user1:pass1\n127.0.0.2:8002\n".encode("gb18030")
    )

    values = load_proxy_file(path)

    assert len(values) == 2
    assert proxy_display(values[0]) == "127.0.0.1:8001"
    assert proxy_for_playwright(values[1]) == {"server": "http://127.0.0.2:8002"}


def test_proxy_file_reports_invalid_line_number(tmp_path):
    path = tmp_path / "bad.txt"
    path.write_text("127.0.0.1:8001\ninvalid\n", encoding="utf-8")

    with pytest.raises(ValueError, match="第 2 行"):
        load_proxy_file(path)
