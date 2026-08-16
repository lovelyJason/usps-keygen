from models import RegistrationData, RegistrationResult, validate_data


def base_data(**changes):
    values = dict(
        account_type="Business Account",
        email="owner@example.com",
        username="usps-owner",
        password="ValidPass123",
        first_name="TestFirst",
        last_name="TestLast",
        company="Example LLC",
        address1="100 Example Blvd",
        address2="Unit 1",
        city="Testville",
        state="VA",
        zip_code="22201",
        phone="2025550100",
        security_answer1="AnswerOne",
        security_answer2="AnswerTwo",
    )
    values.update(changes)
    return RegistrationData(**values)


def test_business_account_requires_company():
    assert "商业账号必须填写公司名称" in validate_data(base_data(company=""))


def test_invalid_email_and_password_rules_are_rejected():
    errors = validate_data(base_data(email="bad", password="alllowercase"))
    assert "邮箱格式不正确" in errors
    assert "密码必须同时包含大写字母、小写字母和数字" in errors


def test_security_answers_must_be_different_and_present():
    errors = validate_data(base_data(security_answer1="same", security_answer2="same"))
    assert "两个安全问题答案不能相同" in errors


def test_valid_registration_data_has_no_errors():
    assert validate_data(base_data()) == []


def test_result_serializes_without_api_credentials():
    result = RegistrationResult.success("complete", "https://reg.usps.com/done")
    row = result.as_dict()
    assert row["status"] == "success"
    assert "token" not in row
