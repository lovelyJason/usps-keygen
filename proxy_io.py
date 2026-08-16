from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit


@dataclass(frozen=True, slots=True)
class ProxyConfig:
    server: str
    username: str = ""
    password: str = ""

    def serialized(self) -> str:
        if not self.username:
            return self.server
        parsed = urlsplit(self.server)
        credentials = f"{quote(self.username, safe='')}:{quote(self.password, safe='')}@"
        return f"{parsed.scheme}://{credentials}{parsed.hostname}:{parsed.port}"

    def display(self) -> str:
        parsed = urlsplit(self.server)
        return f"{parsed.hostname}:{parsed.port}"

    def playwright(self) -> dict[str, str]:
        value = {"server": self.server}
        if self.username:
            value["username"] = self.username
            value["password"] = self.password
        return value


def load_proxy_file(path: str | Path) -> list[str]:
    source = Path(path)
    try:
        content = source.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        content = source.read_text(encoding="gb18030")
    proxies = []
    for line_number, raw in enumerate(content.splitlines(), start=1):
        value = raw.strip()
        if not value or value.startswith("#"):
            continue
        try:
            proxies.append(parse_proxy(value).serialized())
        except ValueError as exc:
            raise ValueError(f"代理文件第 {line_number} 行格式错误：{exc}") from exc
    if not proxies:
        raise ValueError("代理文件没有有效代理")
    return proxies


def parse_proxy(value: str) -> ProxyConfig:
    raw = value.strip()
    if not raw:
        raise ValueError("代理不能为空")
    if "://" not in raw:
        parts = raw.split(":", 3)
        if len(parts) not in (2, 4):
            raise ValueError("仅支持 host:port 或 host:port:user:password")
        host, port = parts[:2]
        username, password = parts[2:] if len(parts) == 4 else ("", "")
        raw = f"http://{host}:{port}"
        parsed = _validate_server(raw)
        return ProxyConfig(
            f"{parsed.scheme}://{parsed.hostname}:{parsed.port}", username, password
        )

    parsed = _validate_server(raw)
    username = unquote(parsed.username or "")
    password = unquote(parsed.password or "")
    return ProxyConfig(
        f"{parsed.scheme}://{parsed.hostname}:{parsed.port}", username, password
    )


def proxy_display(value: str) -> str:
    return parse_proxy(value).display() if value.strip() else "直连"


def proxy_for_playwright(value: str) -> dict[str, str] | None:
    return parse_proxy(value).playwright() if value.strip() else None


def _validate_server(value: str):
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https", "socks5"}:
        raise ValueError("代理协议必须是 http、https 或 socks5")
    if not parsed.hostname:
        raise ValueError("代理主机不能为空")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("代理端口无效") from exc
    if not port or not 1 <= port <= 65535:
        raise ValueError("代理端口必须在 1-65535 之间")
    return parsed
