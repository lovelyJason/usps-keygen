from __future__ import annotations

import csv
import re
import secrets
import string
from collections.abc import Iterable
from pathlib import Path

from models import ACCOUNT_TYPES, RegistrationData, RegistrationResult, validate_data
from proxy_io import parse_proxy

CSV_FIELDS = [
    "account_type",
    "email",
    "proxy",
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
SENSITIVE_FIELDS = {"password", "proxy", "security_answer1", "security_answer2"}
CSV_STATUS_FIELD = "status"
FAILED_CSV_STATUSES = {"failed", "failure", "error", "失败"}


def load_registration_csv(
    path: str | Path,
    mailbox_domain: str,
    mailbox_prefix: str = "usps",
    batch_id: str | None = None,
) -> list[RegistrationData]:
    rows, _skipped = load_registration_csv_with_stats(
        path,
        mailbox_domain,
        mailbox_prefix,
        batch_id,
        skip_failed=False,
    )
    return rows


def load_registration_csv_with_stats(
    path: str | Path,
    mailbox_domain: str,
    mailbox_prefix: str = "usps",
    batch_id: str | None = None,
    skip_failed: bool = True,
) -> tuple[list[RegistrationData], int]:
    domain = _clean_domain(mailbox_domain)
    rows: list[RegistrationData] = []
    usernames: set[str] = set()
    emails: set[str] = set()
    skipped = 0
    source_count = 0

    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError("CSV 缺少表头")
        missing = {"username", "password", "first_name", "last_name"} - set(reader.fieldnames)
        if missing:
            raise ValueError(f"CSV 缺少字段：{', '.join(sorted(missing))}")

        for row_number, source in enumerate(reader, start=1):
            source_count += 1
            source_status = str(source.get(CSV_STATUS_FIELD, "") or "").strip().casefold()
            if skip_failed and source_status in FAILED_CSV_STATUSES:
                skipped += 1
                continue
            values = {field: str(source.get(field, "") or "").strip() for field in CSV_FIELDS}
            values["account_type"] = values["account_type"] or ACCOUNT_TYPES[0]
            if not values["email"]:
                values["email"] = _new_random_email(domain, emails)
            data = RegistrationData(**values)
            if data.proxy:
                try:
                    data.proxy = parse_proxy(data.proxy).serialized()
                except ValueError as exc:
                    raise ValueError(f"第 {row_number} 行代理格式错误：{exc}") from exc
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

    if source_count == 0:
        raise ValueError("CSV 没有可注册的数据行")
    return rows, skipped


def export_results(
    path: str | Path,
    rows: Iterable[tuple[RegistrationData, RegistrationResult]],
    include_sensitive: bool = False,
) -> None:
    fieldnames = CSV_FIELDS + [
        "stage",
        "message",
        "final_url",
        "started_at",
        "finished_at",
        CSV_STATUS_FIELD,
    ]
    with Path(path).open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for data, result in rows:
            exported = data.as_dict() | result.as_dict()
            if not include_sensitive:
                for field in SENSITIVE_FIELDS:
                    exported[field] = ""
            writer.writerow({
                key: (
                    str(exported.get(key) or "")
                    if include_sensitive and key in SENSITIVE_FIELDS
                    else _csv_safe(exported.get(key))
                )
                for key in fieldnames
            })


def write_template(path: str | Path) -> None:
    with Path(path).open("w", newline="", encoding="utf-8-sig") as handle:
        fieldnames = CSV_FIELDS + [CSV_STATUS_FIELD]
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        example = RegistrationData(
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
        writer.writerow({key: example.get(key, "") for key in fieldnames})


def update_csv_status(path: str | Path, data: RegistrationData, status: str) -> bool:
    source = Path(path)
    with source.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError("CSV 缺少表头")
        fieldnames = [field for field in reader.fieldnames if field != CSV_STATUS_FIELD]
        if "email" not in fieldnames:
            fieldnames.insert(1, "email")
        fieldnames.append(CSV_STATUS_FIELD)
        rows = list(reader)
    matched = False
    for row in rows:
        same_username = str(row.get("username") or "").strip() == data.username
        same_email = str(row.get("email") or "").strip().casefold() == data.email.casefold()
        if same_username or (data.email and same_email):
            row["email"] = data.email
            row[CSV_STATUS_FIELD] = status
            matched = True
            break
    if not matched:
        return False
    temporary = source.with_suffix(f"{source.suffix}.tmp")
    with temporary.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(source)
    return True


def materialize_csv_emails(path: str | Path, data_rows: list[RegistrationData]) -> None:
    source = Path(path)
    by_username = {data.username: data for data in data_rows}
    with source.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError("CSV 缺少表头")
        fieldnames = [field for field in reader.fieldnames if field != CSV_STATUS_FIELD]
        if "email" not in fieldnames:
            fieldnames.insert(1, "email")
        fieldnames.append(CSV_STATUS_FIELD)
        rows = list(reader)
    changed = False
    for row in rows:
        data = by_username.get(str(row.get("username") or "").strip())
        if data and not str(row.get("email") or "").strip():
            row["email"] = data.email
            changed = True
    if not changed and reader.fieldnames == fieldnames:
        return
    temporary = source.with_suffix(f"{source.suffix}.tmp")
    with temporary.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(source)


def _csv_safe(value) -> str:
    text = str(value or "")
    return f"'{text}" if text.startswith(("=", "+", "-", "@", "\t", "\r")) else text


def retry_mailbox_address(address: str, attempt: int) -> str:
    _local, separator, domain = address.lower().strip().rpartition("@")
    if not separator or attempt < 2:
        raise ValueError("重试邮箱地址或尝试次数无效")
    return f"{_random_local_part()}@{_clean_domain(domain)}"


def _new_random_email(domain: str, existing: set[str]) -> str:
    while True:
        address = f"{_random_local_part()}@{domain}"
        if address.casefold() not in existing:
            return address


def _random_local_part(length: int = 14) -> str:
    alphabet = string.ascii_lowercase + string.digits
    return secrets.choice(string.ascii_lowercase) + "".join(
        secrets.choice(alphabet) for _ in range(length - 1)
    )


def _clean_domain(value: str) -> str:
    domain = value.strip().lower().removeprefix("@").strip(".")
    if not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", domain):
        raise ValueError("邮箱域名格式不正确")
    return domain
