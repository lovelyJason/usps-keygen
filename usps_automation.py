from __future__ import annotations

import re
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from urllib.parse import urlsplit

from mailbox_client import MailboxClient, MailboxError, VerificationMessage
from models import RegistrationData, RegistrationResult
from usps_helpers import (
    RegistrationFlowError,
    fill_with_events,
    iso_now,
    now_ms,
    otp_selectors,
    page_url,
    safe_slug,
    save_redacted_screenshot,
    visible_errors,
    visible_otp_input,
)
from usps_page_state import PageState, classify_browser_page

USPS_REGISTER_URL = "https://reg.usps.com/entreg/RegistrationAction_input"


@dataclass(slots=True)
class AutomationConfig:
    headless: bool = False
    page_timeout_ms: int = 60_000
    mailbox_timeout_seconds: int = 180
    mailbox_poll_seconds: float = 3
    profile_root: Path = Path.home() / ".usps-registration-mvp" / "profiles"
    artifact_dir: Path | None = None


class UspsRegistrationRunner:
    def __init__(self, config: AutomationConfig):
        self.config = config
        self.stage = "browser_start"

    def run(
        self,
        data: RegistrationData,
        mailbox: MailboxClient,
        stop_event: Event | None = None,
        log: Callable[[str], None] | None = None,
    ) -> RegistrationResult:
        from playwright.sync_api import sync_playwright

        reporter = log or (lambda _message: None)
        started = iso_now()
        result: RegistrationResult | None = None
        try:
            with sync_playwright() as playwright:
                context = None
                page = None
                try:
                    self._check_stop(stop_event)
                    profile = self.config.profile_root / safe_slug(data.username)
                    profile.mkdir(parents=True, exist_ok=True)
                    context = playwright.chromium.launch_persistent_context(
                        str(profile),
                        headless=self.config.headless,
                        viewport={"width": 1360, "height": 920},
                    )
                    context.set_default_timeout(self.config.page_timeout_ms)
                    page = context.pages[0] if context.pages else context.new_page()
                    if data.account_type == "Business Account":
                        stage = self._run_business(page, data, mailbox, stop_event, reporter)
                    else:
                        stage = self._run_personal(page, data, mailbox, stop_event, reporter)
                    result = (
                        RegistrationResult.success(stage, page_url(page))
                        if stage == "complete"
                        else self._final_result(page, stage)
                    )
                except InterruptedError:
                    result = RegistrationResult.stopped(self.stage)
                except RegistrationFlowError as exc:
                    result = RegistrationResult.failed(exc.stage, str(exc), page_url(page))
                    save_redacted_screenshot(
                        page, self.config.artifact_dir, data.username, exc.stage
                    )
                except Exception as exc:
                    result = RegistrationResult.failed(self.stage, str(exc), page_url(page))
                    save_redacted_screenshot(
                        page, self.config.artifact_dir, data.username, self.stage
                    )
                finally:
                    if context is not None:
                        with suppress(Exception):
                            context.close()
        except Exception as exc:
            result = RegistrationResult.failed(self.stage, str(exc))

        result = result or RegistrationResult.failed(self.stage, "浏览器任务异常结束")
        result.started_at = started
        result.finished_at = iso_now()
        return result

    def _run_business(
        self,
        page,
        data: RegistrationData,
        mailbox: MailboxClient,
        stop_event: Event | None,
        log: Callable[[str], None],
    ) -> str:
        self.stage = "email_request"
        log(f"{data.username}: 打开 USPS 商业账号入口")
        page.goto(USPS_REGISTER_URL, wait_until="domcontentloaded")
        page.locator("#rAccount2").check()
        page.locator("#btn-account-type-business-submit").click()
        page.wait_for_selector("#temail", state="visible")
        page.wait_for_function(
            "() => !!window.jQuery && "
            "!!jQuery._data(document.querySelector('#btn-send-email'), 'events')?.click"
        )
        fill_with_events(page, "#temail", data.email)
        page.locator("#temail").press("Tab")
        page.wait_for_function("() => !document.querySelector('#btn-send-email')?.disabled")
        self._check_stop(stop_event)
        mail_floor_id = mailbox.latest_mail_id(data.email)
        sent_after_ms = now_ms()
        try:
            with page.expect_response(
                lambda response: "EmailValidationGatewayAction" in response.url,
                timeout=min(self.config.page_timeout_ms, 30_000),
            ) as response_info:
                page.locator("#btn-send-email").click()
            email_response = response_info.value
        except Exception as exc:
            raise RegistrationFlowError("email_request", "未收到 USPS 验证邮件接口响应") from exc
        outage_url = email_response.headers.get("location", "")
        if "anyapp_outage_apology" in outage_url:
            raise RegistrationFlowError("email_request", "USPS 验证邮件接口当前返回服务故障页")
        if email_response.status >= 400:
            raise RegistrationFlowError(
                "email_request", f"USPS 验证邮件接口返回 HTTP {email_response.status}"
            )
        self._wait_for_email_request(page)

        self.stage = "mail"
        log(f"{data.username}: 已发送验证邮件，等待邮箱服务")
        verification = mailbox.poll_verification(
            data.email,
            after_ms=sent_after_ms,
            after_mail_id=mail_floor_id,
            timeout_seconds=self.config.mailbox_timeout_seconds,
            poll_interval=self.config.mailbox_poll_seconds,
            stop_event=stop_event,
        )
        self._check_stop(stop_event)
        self.stage = "business_email_verification_pending"
        self._submit_verification(page, data, mailbox, verification, stop_event)
        page.wait_for_selector("#tcompany", state="visible")
        log(f"{data.username}: 邮箱验证完成")

        self.stage = "address_verification"
        self._fill_address(page, data)
        self._complete_address_wizard(page, "#tfName", stop_event, log)
        self.stage = "identity_verification"
        fill_with_events(page, "#tfName", data.first_name)
        fill_with_events(page, "#tlName", data.last_name)
        fill_with_events(page, "#tphone", data.phone)
        self._check_stop(stop_event)
        page.locator("#btn-verify").click()
        page.wait_for_function(
            "() => location.pathname.includes('Failure') || "
            "document.querySelector('#tuserName')?.offsetParent !== null",
            timeout=self.config.page_timeout_ms,
        )
        state = classify_browser_page(page)
        if state is PageState.IDENTITY_REJECTED:
            raise RegistrationFlowError(self.stage, "USPS 商业账号身份校验未通过")

        self.stage = "credentials"
        self._fill_credentials(page, data)
        return self._complete_account_submission(page, data, mailbox, stop_event)

    def _run_personal(
        self,
        page,
        data: RegistrationData,
        mailbox: MailboxClient,
        stop_event: Event | None,
        log: Callable[[str], None],
    ) -> str:
        self.stage = "form_fill"
        self._check_stop(stop_event)
        log(f"{data.username}: 打开 USPS 个人账号入口")
        page.goto(USPS_REGISTER_URL, wait_until="domcontentloaded")
        page.locator("#rAccount1").check()
        page.locator("#btn-account-type-submit").click()
        page.wait_for_selector("#tuserName", state="visible")
        self._fill_credentials(page, data)
        fill_with_events(page, "#tfName", data.first_name)
        fill_with_events(page, "#tlName", data.last_name)
        fill_with_events(page, "#tphone", data.phone)
        fill_with_events(page, "#temail", data.email)
        fill_with_events(page, "#temailRetype", data.email)
        self._fill_address(page, data)
        self.stage = "address_verification"
        self._complete_address_wizard(page, "#btn-submit", stop_event, log)
        return self._complete_account_submission(page, data, mailbox, stop_event)

    def _complete_account_submission(
        self,
        page,
        data: RegistrationData,
        mailbox: MailboxClient,
        stop_event: Event | None,
    ) -> str:
        self.stage = "final_submit"
        self._check_stop(stop_event)
        mail_floor_id = mailbox.latest_mail_id(data.email)
        sent_after_ms = now_ms()
        self._submit_account(page, data, stop_event)
        self.stage = "submission_unknown"
        state = self._wait_for_terminal(page, stop_event)
        if state is PageState.SUCCESS:
            self.stage = "complete"
        if state is PageState.WAITING_FOR_EMAIL or visible_otp_input(page):
            self.stage = "post_submit_mail"
            verification = mailbox.poll_verification(
                data.email,
                after_ms=sent_after_ms,
                after_mail_id=mail_floor_id,
                timeout_seconds=self.config.mailbox_timeout_seconds,
                poll_interval=self.config.mailbox_poll_seconds,
                stop_event=stop_event,
            )
            self._check_stop(stop_event)
            self.stage = "email_verification"
            self._submit_verification(page, data, mailbox, verification, stop_event)
            self.stage = "submission_unknown"
            final_state = self._wait_for_terminal(page, stop_event)
            if final_state is PageState.SUCCESS:
                self.stage = "complete"
            if final_state is PageState.WAITING_FOR_EMAIL:
                raise RegistrationFlowError(
                    "submission_unknown",
                    "邮箱验证后 USPS 再次要求验证邮件；请先人工核对账号状态",
                )
        return self.stage

    def _wait_for_email_request(self, page) -> None:
        try:
            page.wait_for_function(
                "() => document.querySelector('#row-3')?.offsetParent !== null || "
                "!!document.querySelector('#response-msg')?.textContent.trim() || "
                "!!document.querySelector('#error-temail')?.textContent.trim()",
                timeout=min(self.config.page_timeout_ms, 30_000),
            )
        except Exception as exc:
            detail = visible_errors(page)
            raise RegistrationFlowError(
                "email_request", detail or "USPS 验证邮件请求无响应"
            ) from exc
        if not page.locator("#row-3").is_visible():
            detail = visible_errors(page) or page.locator("#response-msg").inner_text().strip()
            raise RegistrationFlowError("email_request", detail or "USPS 未接受验证邮件请求")

    def _fill_address(self, page, data: RegistrationData) -> None:
        fill_with_events(page, "#tcompany", data.company, required=False)
        fill_with_events(page, "#taddress", data.address1)
        fill_with_events(page, "#tapt", data.address2, required=False)
        fill_with_events(page, "#tcity", data.city)
        page.locator("#sstate").select_option(data.state.upper())
        fill_with_events(page, "#tzip", data.zip_code)

    def _complete_address_wizard(
        self,
        page,
        target_selector: str,
        stop_event: Event | None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self._check_stop(stop_event)
        if target_selector == "#tfName":
            self._start_business_address_search(page, stop_event, log)
        else:
            page.locator("#a-address-step1").click()
        page.wait_for_timeout(700)
        actions = [
            "#a-address-step2",
            "#a-address-step3",
            "#a-address-step4",
            "#a-address-step5",
            "#btn-address-wizard-continue",
            "#btn-address-wizard-continue-three",
        ]
        address_confirmed = False
        for _ in range(8):
            address_confirmed = address_confirmed or self._address_finalized(page)
            if page.locator(target_selector).is_visible() and address_confirmed:
                return
            self._check_stop(stop_event)
            visible_address = page.locator("input[name='address']:visible").first
            if visible_address.count() and not visible_address.is_checked():
                visible_address.check()
            clicked = False
            for selector in actions:
                locator = page.locator(selector)
                if locator.count() and locator.is_visible() and locator.is_enabled():
                    locator.click()
                    page.wait_for_timeout(500)
                    clicked = True
                    break
            if not clicked:
                break
        raise RegistrationFlowError(
            "address_verification", visible_errors(page) or "地址确认未完成"
        )

    def _start_business_address_search(
        self,
        page,
        stop_event: Event | None,
        log: Callable[[str], None] | None,
    ) -> None:
        reporter = log or (lambda _message: None)
        search = page.locator("#a-address-step1")
        page.wait_for_function(
            "() => !!window.jQuery && "
            "!!jQuery._data(document.querySelector('#a-address-step1'), 'events')?.click"
        )
        for attempt in range(2):
            self._check_stop(stop_event)
            try:
                with page.expect_response(
                    lambda response: "ValidateAddressAction" in response.url,
                    timeout=min(self.config.page_timeout_ms, 30_000),
                ) as response_info:
                    search.click()
                response = response_info.value
            except Exception:
                detail = visible_errors(page)
                if detail:
                    raise RegistrationFlowError("address_verification", detail) from None
                response = None

            if response is not None and not self._address_service_outage(response):
                return
            if attempt == 0:
                reporter("地址服务暂时无响应，2 秒后自动重试")
                self._reset_address_search(page)
                page.wait_for_timeout(2_000)
                continue

            reporter("USPS 地址服务故障，已按原地址继续")
            self._accept_unverified_business_address(page)
            return

    @staticmethod
    def _address_service_outage(response) -> bool:
        location = response.headers.get("location", "").casefold()
        return (
            300 <= response.status < 400
            and "anyapp_outage_apology" in location
        ) or "anyapp_outage_apology" in response.url.casefold()

    @staticmethod
    def _reset_address_search(page) -> None:
        page.evaluate(
            "() => { "
            "window.jQuery?.unblockUI?.(); "
            "document.querySelector('#a-address-step1')?.classList.remove('disabled'); "
            "}"
        )

    @staticmethod
    def _accept_unverified_business_address(page) -> None:
        page.evaluate(
            "() => { "
            "const get = id => document.querySelector(id)?.value?.trim() || ''; "
            "const text = (selector, value) => { "
            "const element = document.querySelector(selector); "
            "if (element) element.textContent = value; "
            "}; "
            "const ams = document.querySelector('#ams-verified'); "
            "const completed = document.querySelector('#address-c'); "
            "if (ams) ams.value = 'false'; if (completed) completed.value = 'true'; "
            "text('#final-address .company', get('#tcompany')); "
            "text('#final-address .address1', get('#taddress')); "
            "text('#final-address .address2', get('#tapt')); "
            "text('#final-address .city', get('#tcity')); "
            "text('#final-address .state', get('#sstate')); "
            "text('#final-address .zip', get('#tzip')); "
            "document.querySelector('#confirmed-address')?.classList.add('d-none'); "
            "document.querySelector('#unconfirmed-address')?.classList.remove('d-none'); "
            "document.querySelectorAll('div.confirmed-address').forEach(" 
            "element => element.classList.add('d-none')); "
            "document.querySelectorAll('div.unconfirmed-address').forEach(" 
            "element => element.classList.remove('d-none')); "
            "document.querySelector('#btn-address-wizard-continue-two')?.classList.add('d-none'); "
            "document.querySelector('#btn-address-wizard-continue-three')?.classList.remove(" 
            "'d-none'); "
            "document.querySelector('#addressHolderStep1')?.classList.add('d-none'); "
            "document.querySelector('#addressHolderStep6')?.classList.remove('d-none'); "
            "window.jQuery?.unblockUI?.(); "
            "document.querySelector('#a-address-step1')?.classList.remove('disabled'); "
            "}"
        )

    @staticmethod
    def _address_finalized(page) -> bool:
        selectors = (
            "#confirmed-address",
            "#unconfirmed-address",
            "#addressHolderStep6",
            "#zip-confirmed-address",
            "#zip-unconfirmed-address",
        )
        return any(
            page.locator(selector).count() and page.locator(selector).is_visible()
            for selector in selectors
        )

    def _fill_credentials(self, page, data: RegistrationData) -> None:
        fill_with_events(page, "#tuserName", data.username)
        page.locator("#tuserName").press("Tab")
        page.wait_for_timeout(500)
        if page.locator("#form-group-tuserName.has-error").count():
            raise RegistrationFlowError("username", visible_errors(page) or "用户名不可用")
        fill_with_events(page, "#tPassword", data.password)
        fill_with_events(page, "#tPasswordRetype", data.password)
        page.locator("#ssec1").select_option("1")
        page.locator("#ssec2").select_option("2")
        fill_with_events(page, "#tsecAnswer1", data.security_answer1)
        fill_with_events(page, "#tsecAnswer1Match", data.security_answer1)
        fill_with_events(page, "#tsecAnswer2", data.security_answer2)
        fill_with_events(page, "#tsecAnswer2Match", data.security_answer2)

    def _submit_account(self, page, data: RegistrationData, stop_event: Event | None) -> None:
        self._check_stop(stop_event)
        self.stage = "submission_unknown"
        page.locator("#btn-submit").click()
        page.wait_for_timeout(500)
        modal = page.locator("#confirm-modal")
        if modal.count() and modal.is_visible():
            self._check_stop(stop_event)
            fill_with_events(page, "#tEmailVerification", data.email)
            self._check_stop(stop_event)
            page.locator("#btn-submit-verifyEmailDlg").click()

    def _submit_verification(
        self,
        page,
        data: RegistrationData,
        mailbox: MailboxClient,
        message: VerificationMessage,
        stop_event: Event | None,
    ) -> None:
        self._check_stop(stop_event)
        if message.kind == "link":
            self.stage = "email_verification"
            page.goto(message.value, wait_until="domcontentloaded")
            return
        for selector in otp_selectors():
            locator = page.locator(selector).first
            if not locator.count() or not locator.is_visible():
                continue
            locator.fill(message.value)
            submit = page.get_by_role(
                "button", name=re.compile("submit|verify|continue", re.I)
            ).first
            self._check_stop(stop_event)
            self.stage = "email_verification"
            before_url = page.url
            submit.click()
            try:
                self._wait_for_otp_acceptance(page, data, selector, before_url)
            except Exception as exc:
                if isinstance(exc, (InterruptedError, RegistrationFlowError)):
                    raise
                raise RegistrationFlowError(
                    "email_verification", visible_errors(page) or "验证码未被 USPS 接受"
                ) from exc
            self._consume_accepted_otp(mailbox, message, data.email)
            return
        raise RegistrationFlowError("email_verification", "页面没有可填写的验证码输入框")

    def _wait_for_otp_acceptance(
        self,
        page,
        data: RegistrationData,
        selector: str,
        before_url: str,
    ) -> None:
        deadline = time.monotonic() + min(self.config.page_timeout_ms / 1000, 20)
        while time.monotonic() < deadline:
            errors = visible_errors(page)
            if errors:
                raise RegistrationFlowError("email_verification", errors)
            state = classify_browser_page(page)
            if state is PageState.SUCCESS:
                return
            if state is PageState.CAPTCHA_REQUIRED:
                raise RegistrationFlowError("captcha", "USPS 要求完成 CAPTCHA")
            if state is PageState.IDENTITY_REJECTED:
                raise RegistrationFlowError("identity_verification", "USPS 身份校验未通过")
            if data.account_type == "Business Account" and self._business_details_visible(
                page, selector, before_url
            ):
                return
            otp = page.locator(selector).first
            if page.url == before_url or (otp.count() and otp.is_visible()):
                page.wait_for_timeout(250)
                continue
            page.wait_for_timeout(250)
        raise RegistrationFlowError(
            "otp_submission_unknown",
            "验证码已提交，但未检测到 USPS 明确接受结果；请先人工核对账号状态",
        )

    @staticmethod
    def _business_details_visible(page, otp_selector: str, before_url: str) -> bool:
        parsed = urlsplit(page.url)
        path = parsed.path.casefold()
        company = page.locator("#tcompany")
        otp = page.locator(otp_selector).first
        body = page.locator("body").inner_text().casefold()
        return (
            page.url != before_url
            and parsed.hostname == "reg.usps.com"
            and not any(marker in path for marker in ("error", "failure", "outage"))
            and company.count()
            and company.is_visible()
            and not (otp.count() and otp.is_visible())
            and "company information" in body
        )

    @staticmethod
    def _consume_accepted_otp(
        mailbox: MailboxClient, message: VerificationMessage, address: str
    ) -> None:
        last_error: MailboxError | None = None
        for attempt in range(3):
            try:
                mailbox.consume_otp(message.mail_id, address, message.value)
                return
            except MailboxError as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(0.5 * (2**attempt))
        raise RegistrationFlowError(
            "mail_consumption",
            f"USPS 已接受验证码，但邮箱消费状态更新失败：{last_error}",
        )

    def _wait_for_terminal(self, page, stop_event: Event | None) -> PageState:
        deadline = time.monotonic() + min(self.config.page_timeout_ms / 1000, 30)
        while time.monotonic() < deadline:
            state = classify_browser_page(page)
            if state is not PageState.UNKNOWN:
                return state
            if stop_event and stop_event.is_set():
                raise RegistrationFlowError(
                    "submission_unknown",
                    "已请求停止，但 USPS 最终提交结果尚未确认；请先人工核对账号状态",
                )
            errors = visible_errors(page)
            submit = page.locator("#btn-submit")
            if errors and submit.count() and submit.is_visible():
                raise RegistrationFlowError("final_submit", errors)
            page.wait_for_timeout(500)
        raise RegistrationFlowError(
            "submission_unknown",
            "USPS 最终提交已发送，但等待结果超时；请先人工核对账号状态",
        )

    def _final_result(self, page, stage: str) -> RegistrationResult:
        state = classify_browser_page(page)
        if state is PageState.SUCCESS:
            return RegistrationResult.success(stage, page.url)
        if state is PageState.CAPTCHA_REQUIRED:
            return RegistrationResult.failed("captcha", "USPS 要求完成 CAPTCHA", page.url)
        if state is PageState.IDENTITY_REJECTED:
            return RegistrationResult.failed(
                "identity_verification", "USPS 身份校验未通过", page.url
            )
        return RegistrationResult.failed(
            stage, visible_errors(page) or "未检测到 USPS 注册成功页面", page.url
        )

    @staticmethod
    def _check_stop(stop_event: Event | None) -> None:
        if stop_event and stop_event.is_set():
            raise InterruptedError("批量任务已停止")
