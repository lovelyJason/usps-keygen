from __future__ import annotations

import re
from dataclasses import asdict, dataclass

ACCOUNT_TYPES = ("Business Account", "Personal Account")


@dataclass(slots=True)
class RegistrationData:
    account_type: str = "Business Account"
    email: str = ""
    verification_code: str = ""
    proxy: str = ""
    browser_fingerprint: str = ""
    fingerprint_locked: bool = False
    username: str = ""
    password: str = ""
    first_name: str = ""
    last_name: str = ""
    company: str = ""
    address1: str = ""
    address2: str = ""
    city: str = ""
    state: str = ""
    zip_code: str = ""
    phone: str = ""
    security_answer1: str = ""
    security_answer2: str = ""

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(slots=True)
class RegistrationResult:
    status: str
    stage: str
    message: str = ""
    final_url: str = ""
    started_at: str = ""
    finished_at: str = ""

    @classmethod
    def success(cls, stage: str, final_url: str, message: str = "注册完成") -> RegistrationResult:
        return cls("success", stage, message, final_url)

    @classmethod
    def failed(cls, stage: str, message: str, final_url: str = "") -> RegistrationResult:
        return cls("failed", stage, message, final_url)

    @classmethod
    def stopped(cls, stage: str, message: str = "用户已停止") -> RegistrationResult:
        return cls("stopped", stage, message)

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


def validate_data(data: RegistrationData, require_email: bool = True) -> list[str]:
    errors: list[str] = []
    required = {
        "用户名": data.username,
        "密码": data.password,
        "名": data.first_name,
        "姓": data.last_name,
        "地址": data.address1,
        "城市": data.city,
        "州": data.state,
        "邮编": data.zip_code,
        "电话": data.phone,
        "安全问题答案 1": data.security_answer1,
        "安全问题答案 2": data.security_answer2,
    }
    if require_email:
        required["邮箱"] = data.email
    errors.extend(f"{name}不能为空" for name, value in required.items() if not value.strip())
    if data.account_type not in ACCOUNT_TYPES:
        errors.append("账号类型必须是 Business Account 或 Personal Account")
    if data.email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", data.email):
        errors.append("邮箱格式不正确")
    if not 8 <= len(data.password) <= 50:
        errors.append("密码长度必须为 8 到 50 位")
    if not all(re.search(pattern, data.password) for pattern in (r"[A-Z]", r"[a-z]", r"[0-9]")):
        errors.append("密码必须同时包含大写字母、小写字母和数字")
    if data.username and data.username.lower() in data.password.lower():
        errors.append("密码不能包含用户名")
    if re.search(r"(.)\1\1", data.password):
        errors.append("密码不能包含三个连续相同字符")
    if data.account_type == "Business Account" and not data.company.strip():
        errors.append("商业账号必须填写公司名称")
    if data.security_answer1.strip().casefold() == data.security_answer2.strip().casefold():
        errors.append("两个安全问题答案不能相同")
    if data.state and not re.fullmatch(r"[A-Za-z]{2}", data.state):
        errors.append("州必须使用两位缩写")
    if data.zip_code and not re.fullmatch(r"\d{5}(?:-\d{4})?", data.zip_code):
        errors.append("邮编格式不正确")
    return errors
