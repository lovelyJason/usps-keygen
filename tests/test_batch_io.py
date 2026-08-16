import csv
import re

from batch_io import (
    export_results,
    load_registration_csv,
    load_registration_csv_with_stats,
    retry_mailbox_address,
    update_csv_status,
    write_template,
)
from models import RegistrationResult


def test_csv_import_generates_unique_mailboxes_for_blank_email(tmp_path):
    source = tmp_path / "batch.csv"
    source.write_text(
        "account_type,email,username,password,first_name,last_name,company,address1,address2,city,state,zip_code,phone,security_answer1,security_answer2\n"
        "Business Account,,user1,ValidPass123,Test,One,One LLC,100 Example Blvd,Unit 1,"
        "Testville,VA,22201,2025550100,AnswerOne,AnswerTwo\n"
        "Business Account,,user2,ValidPass123,Test,Two,Two LLC,200 Example Blvd,Unit 2,"
        "Testville,VA,22201,2025550101,AnswerThree,AnswerFour\n",
        encoding="utf-8",
    )

    rows = load_registration_csv(
        source, mailbox_domain="velydora.com", mailbox_prefix="usps", batch_id="batch42"
    )

    assert re.fullmatch(r"[a-z][a-z0-9]{13}@velydora\.com", rows[0].email)
    assert re.fullmatch(r"[a-z][a-z0-9]{13}@velydora\.com", rows[1].email)
    assert "usps" not in rows[0].email
    assert "batch42" not in rows[0].email
    assert rows[0].email != rows[1].email


def test_csv_import_rejects_duplicate_username(tmp_path):
    source = tmp_path / "batch.csv"
    source.write_text(
        "account_type,username,password,first_name,last_name,address1,city,state,zip_code,phone,security_answer1,security_answer2\n"
        "Personal Account,same,ValidPass123,A,B,1 Main St,Austin,TX,78701,5125550100,X,Y\n"
        "Personal Account,same,ValidPass123,C,D,2 Main St,Austin,TX,78701,5125550101,X,Z\n",
        encoding="utf-8",
    )

    try:
        load_registration_csv(source, mailbox_domain="velydora.com")
    except ValueError as exc:
        assert "重复用户名" in str(exc)
    else:
        raise AssertionError("duplicate username should fail")


def test_retry_mailbox_uses_fresh_address_on_same_domain():
    address = retry_mailbox_address("old-address@velydora.com", 2)
    assert re.fullmatch(r"[a-z][a-z0-9]{13}@velydora\.com", address)
    assert "old-address" not in address


def test_csv_import_rejects_email_outside_configured_mailbox_domain(tmp_path):
    source = _single_row_csv(tmp_path)
    content = source.read_text(encoding="utf-8")
    content = content.replace("Personal Account,user1", "Personal Account,other@example.com,user1")
    content = content.replace("account_type,username", "account_type,email,username")
    source.write_text(content, encoding="utf-8")
    try:
        load_registration_csv(source, mailbox_domain="velydora.com")
    except ValueError as exc:
        assert "必须使用已配置的 velydora.com 域名" in str(exc)
    else:
        raise AssertionError("external mailbox should fail")


def test_result_export_contains_per_row_outcome(tmp_path):
    target = tmp_path / "results.csv"
    rows = load_registration_csv(
        _single_row_csv(tmp_path), mailbox_domain="velydora.com", batch_id="batch42"
    )
    export_results(target, [(rows[0], RegistrationResult.failed("mail", "timeout"))])

    with target.open(newline="", encoding="utf-8-sig") as handle:
        exported = list(csv.DictReader(handle))
    assert exported[0]["status"] == "failed"
    assert exported[0]["stage"] == "mail"
    assert exported[0]["message"] == "timeout"
    assert exported[0]["password"] == ""
    assert exported[0]["security_answer1"] == ""


def test_result_export_escapes_spreadsheet_formulas(tmp_path):
    target = tmp_path / "results.csv"
    rows = load_registration_csv(
        _single_row_csv(tmp_path), mailbox_domain="velydora.com", batch_id="batch42"
    )
    rows[0].first_name = '=HYPERLINK("https://example.invalid")'

    export_results(target, [(rows[0], RegistrationResult.failed("mail", "=1+1"))])

    with target.open(newline="", encoding="utf-8-sig") as handle:
        exported = next(csv.DictReader(handle))
    assert exported["first_name"].startswith("'=")
    assert exported["message"] == "'=1+1"


def test_sensitive_export_preserves_exact_credentials(tmp_path):
    target = tmp_path / "results.csv"
    rows = load_registration_csv(
        _single_row_csv(tmp_path), mailbox_domain="velydora.com", batch_id="batch42"
    )
    rows[0].password = "@ValidPass123"
    rows[0].security_answer1 = "=ExactAnswer"

    export_results(
        target,
        [(rows[0], RegistrationResult.failed("mail", "timeout"))],
        include_sensitive=True,
    )

    with target.open(newline="", encoding="utf-8-sig") as handle:
        exported = next(csv.DictReader(handle))
    assert exported["password"] == "@ValidPass123"
    assert exported["security_answer1"] == "=ExactAnswer"


def test_csv_failed_rows_are_skipped_and_status_is_last(tmp_path):
    source = tmp_path / "batch.csv"
    source.write_text(
        "account_type,username,password,first_name,last_name,address1,city,state,zip_code,"
        "phone,security_answer1,security_answer2,status\n"
        "Personal Account,bad,ValidPass123,A,B,1 Main,Austin,TX,78701,5125550100,X,Y,failed\n"
        "Personal Account,good,ValidPass123,C,D,2 Main,Austin,TX,78701,5125550101,X,Z,\n",
        encoding="utf-8",
    )

    rows, skipped = load_registration_csv_with_stats(
        source, "velydora.com", skip_failed=True
    )

    assert [row.username for row in rows] == ["good"]
    assert skipped == 1


def test_status_update_preserves_status_as_last_column(tmp_path):
    source = _single_row_csv(tmp_path)
    rows = load_registration_csv(source, "velydora.com")

    assert update_csv_status(source, rows[0], "failed")

    with source.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        exported = next(reader)
        assert reader.fieldnames[-1] == "status"
    assert exported["status"] == "failed"
    assert exported["email"] == rows[0].email


def test_template_status_column_is_last(tmp_path):
    target = tmp_path / "template.csv"
    write_template(target)
    with target.open(newline="", encoding="utf-8-sig") as handle:
        assert next(csv.reader(handle))[-1] == "status"


def _single_row_csv(tmp_path):
    source = tmp_path / "single.csv"
    source.write_text(
        "account_type,username,password,first_name,last_name,address1,city,state,zip_code,phone,security_answer1,security_answer2\n"
        "Personal Account,user1,ValidPass123,A,B,1 Main St,Austin,TX,78701,5125550100,X,Y\n",
        encoding="utf-8",
    )
    return source
