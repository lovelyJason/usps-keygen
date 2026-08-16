from __future__ import annotations

import csv
import re
import secrets
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from models import ACCOUNT_TYPES, RegistrationData, RegistrationResult, validate_data

CSV_FIELDS = [
    "account_type",
    "email",
    "username",
    "password",
    "first_name",
    "last_name",
    "company",
    "address1",
    "address2",
    "city",
    "state",
    "zip_code",
    "phone",
    "security_answer1",
    "security_answer2",
]
SENSITIVE_FIELDS = {"password", "security_answer1", "security_answer2"}


def load_registration_csv(
    path: str | Path,
    mailbox_domain: str,
    mailbox_prefix: str = "usps",
    batch_id: str | None = None,
) -> list[RegistrationData]:
    domain = _clean_domain(mailbox_domain)
    prefix = _clean_local_part(mailbox_prefix or "usps")
    effective_batch_id = _clean_local_part(batch_id or _new_batch_id())
    rows: list[RegistrationData] = []
    usernames: set[str] = set()
    emails: set[str] = set()

    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError("CSV 缺少表头")
        missing = {"username", "password", "first_name", "last_name"} - set(reader.fieldnames)
        if missing:
            raise ValueError(f"CSV 缺少字段：{', '.join(sorted(missing))}")

        for row_number, source in enumerate(reader, start=1):
            values = {field: str(source.get(field, "") or "").strip() for field in CSV_FIELDS}
            values["account_type"] = values["account_type"] or ACCOUNT_TYPES[0]
            values["email"] = values["email"] or (
                f"{prefix}-{effective_batch_id}-{row_number:04d}@{domain}"
            )
            data = RegistrationData(**values)
            key = data.username.casefold()
            email_key = data.email.casefold()
            if data.email.rsplit("@", 1)[-1].casefold() != domain:
                raise ValueError(f"第 {row_number} 行邮箱必须使用已配置的 {domain} 域名")
            if key in usernames:
                raise ValueError(f"第 {row_number} 行重复用户名：{data.username}")
            if email_key in emails:
                raise ValueError(f"第 {row_number} 行重复邮箱：{data.email}")
            errors = validate_data(data)
            if errors:
                raise ValueError(f"第 {row_number} 行校验失败：{'；'.join(errors)}")
            usernames.add(key)
            emails.add(email_key)
            rows.append(data)

    if not rows:
        raise ValueError("CSV 没有可注册的数据行")
    return rows


def export_results(
    path: str | Path,
    rows: Iterable[tuple[RegistrationData, RegistrationResult]],
    include_sensitive: bool = False,
) -> None:
    fieldnames = CSV_FIELDS + [
        "status",
        "stage",
        "message",
        "final_url",
        "started_at",
        "finished_at",
    ]
    with Path(path).open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for data, result in rows:
            exported = data.as_dict() | result.as_dict()
            if not include_sensitive:
                for field in SENSITIVE_FIELDS:
                    exported[field] = ""
            writer.writerow(
                {
                    key: str(value or "")
                    if include_sensitive and key in SENSITIVE_FIELDS
                    else _csv_safe(value)
                    for key, value in exported.items()
                }
            )


def write_template(path: str | Path) -> None:
    with Path(path).open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerow(
            RegistrationData(
                account_type="Business Account",
                email="",
                username="example-user-001",
                password="ReplaceMe123",
                first_name="Jane",
                last_name="Doe",
                company="Example LLC",
                address1="123 Example Ave",
                address2="",
                city="Example City",
                state="CA",
                zip_code="90000",
                phone="2025550100",
                security_answer1="ReplaceAnswerOne",
                security_answer2="ReplaceAnswerTwo",
            ).as_dict()
        )


def _csv_safe(value) -> str:
    text = str(value or "")
    return f"'{text}" if text.startswith(("=", "+", "-", "@", "\t", "\r")) else text


def retry_mailbox_address(address: str, attempt: int) -> str:
    local, separator, domain = address.lower().strip().rpartition("@")
    if not separator or attempt < 2:
        raise ValueError("重试邮箱地址或尝试次数无效")
    suffix = f"-retry{attempt}-{secrets.token_hex(3)}"
    return f"{local[: max(1, 63 - len(suffix))]}{suffix}@{domain}"


def _new_batch_id() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    return f"{stamp}-{secrets.token_hex(2)}"


def _clean_domain(value: str) -> str:
    domain = value.strip().lower().removeprefix("@").strip(".")
    if not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", domain):
        raise ValueError("邮箱域名格式不正确")
    return domain


def _clean_local_part(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip()).strip("-_").lower()
    if not cleaned:
        raise ValueError("邮箱前缀不能为空")
    return cleaned[:32]
