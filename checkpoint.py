from __future__ import annotations

import ctypes
import json
import os
import shutil
from ctypes import wintypes
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from models import RegistrationData, RegistrationResult


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def save_checkpoint(
    path: str | Path,
    rows: list[RegistrationData],
    results: list[RegistrationResult],
) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    plaintext = json.dumps(
        {
            "rows": [row.as_dict() for row in rows],
            "results": [result.as_dict() for result in results],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    token = Fernet(_checkpoint_key(target)).encrypt(plaintext).decode("ascii")
    _atomic_private_write(
        target,
        json.dumps({"version": 2, "ciphertext": token}, separators=(",", ":")).encode(),
    )


def load_checkpoint(
    path: str | Path,
) -> tuple[list[RegistrationData], list[RegistrationResult]]:
    target = Path(path)
    payload = json.loads(target.read_text(encoding="utf-8"))
    version = payload.get("version")
    if version == 1:
        rows, results = _decode_rows(payload)
        save_checkpoint(target, rows, results)
        return rows, results
    if version != 2 or not isinstance(payload.get("ciphertext"), str):
        raise ValueError("不支持的检查点版本")
    try:
        raw = Fernet(_checkpoint_key(target)).decrypt(payload["ciphertext"].encode("ascii"))
        decrypted = json.loads(raw.decode("utf-8"))
    except (InvalidToken, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("检查点解密失败，可能已损坏或来自其他 Windows 用户") from exc
    return _decode_rows(decrypted)


def checkpoint_has_unfinished(path: str | Path) -> bool:
    target = Path(path)
    if not target.exists():
        return False
    _rows, results = load_checkpoint(target)
    return any(result.status != "success" for result in results)


def restore_previous_session(window, checkpoint_path: Path, last_csv_path_key: str) -> None:
    csv_path = str(window.settings.value(last_csv_path_key, "") or "").strip()
    if checkpoint_path.exists():
        try:
            window.rows, window.results = load_checkpoint(checkpoint_path)
        except Exception as exc:
            window.log_view.append(f"上次表格检查点恢复失败：{exc}")
        else:
            window.current_csv_path = (
                Path(csv_path) if csv_path and Path(csv_path).is_file() else None
            )
            window._render_rows()
            window._set_running(False)
            window.log_view.append(f"已恢复上次表格 {len(window.rows)} 条注册数据。")
            return
    if not csv_path:
        return
    if not Path(csv_path).is_file():
        window.log_view.append(f"上次 CSV 路径已不存在：{csv_path}")
        return
    window._load_csv_path(csv_path, startup=True)


def clear_private_data(checkpoint_path: Path, artifact_dir: Path) -> bool:
    try:
        if artifact_dir.exists():
            shutil.rmtree(artifact_dir, ignore_errors=False)
        checkpoint_path.with_suffix(f"{checkpoint_path.suffix}.tmp").unlink(missing_ok=True)
        checkpoint_path.unlink(missing_ok=True)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return True


def _decode_rows(payload) -> tuple[list[RegistrationData], list[RegistrationResult]]:
    rows = [RegistrationData(**item) for item in payload.get("rows", [])]
    results = [RegistrationResult(**item) for item in payload.get("results", [])]
    if not rows or len(rows) != len(results):
        raise ValueError("检查点中的注册行与结果数量不一致")
    return rows, results


def _checkpoint_key(target: Path) -> bytes:
    key_path = target.with_suffix(".key")
    if key_path.exists():
        stored = key_path.read_bytes()
        return _dpapi_unprotect(stored) if os.name == "nt" else stored
    key = Fernet.generate_key()
    stored = _dpapi_protect(key) if os.name == "nt" else key
    _atomic_private_write(key_path, stored)
    return key


def _atomic_private_write(target: Path, data: bytes) -> None:
    temporary = target.with_suffix(f"{target.suffix}.tmp")
    temporary.write_bytes(data)
    os.chmod(temporary, 0o600)
    temporary.replace(target)


def _dpapi_protect(data: bytes) -> bytes:
    return _dpapi_call("CryptProtectData", data)


def _dpapi_unprotect(data: bytes) -> bytes:
    return _dpapi_call("CryptUnprotectData", data)


def _dpapi_call(function_name: str, data: bytes) -> bytes:
    if os.name != "nt":
        return data
    buffer = ctypes.create_string_buffer(data)
    source = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = _DataBlob()
    function = getattr(ctypes.windll.crypt32, function_name)
    if not function(ctypes.byref(source), None, None, None, None, 0x01, ctypes.byref(output)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(output.pbData)
