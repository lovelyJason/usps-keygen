from threading import Event

import pytest

from mailbox_client import MailboxError, VerificationMessage
from models import RegistrationData
from usps_automation import AutomationConfig, UspsRegistrationRunner
from usps_helpers import RegistrationFlowError
from usps_page_state import PageState, classify_page


def test_classify_business_identity_failure():
    state = classify_page(
        "https://reg.usps.com/entreg/RegistrationGatewayFailureAction_input",
        "USPS.com Registration Failure",
        "Sorry, Your USPS.com Business Account Could Not Be Created Online",
    )
    assert state is PageState.IDENTITY_REJECTED


def test_classify_email_link_wait_page():
    state = classify_page(
        "https://reg.usps.com/entreg/RegistrationGatewayAction_input",
        "USPS Registration",
        "Check Your Inbox to Validate Your Email",
    )
    assert state is PageState.WAITING_FOR_EMAIL


def test_classify_success_page():
    state = classify_page(
        "https://reg.usps.com/success",
        "Account Created",
        "Your USPS.com account has been created successfully",
        "Your USPS.com account has been created successfully",
    )
    assert state is PageState.SUCCESS


def test_classify_success_with_support_footer():
    state = classify_page(
        "https://reg.usps.com/success",
        "Account Created",
        "Your USPS.com account has been created successfully\n"
        "If you are unable to sign in, reset your password.",
        "Your USPS.com account has been created successfully",
    )
    assert state is PageState.SUCCESS


def test_classify_rejects_negated_body_despite_success_url_and_title():
    state = classify_page(
        "https://reg.usps.com/success",
        "Account Created",
        "We cannot confirm that your account has been created successfully.",
        "Your USPS.com account has been created successfully",
    )
    assert state is PageState.UNKNOWN


def test_classify_rejects_cross_block_false_statement_with_terminal_url():
    state = classify_page(
        "https://reg.usps.com/confirmation",
        "USPS Registration",
        "The following statement is false:\nYour USPS.com account has been created successfully",
        "Your USPS.com account has been created successfully",
    )
    assert state is PageState.UNKNOWN


def test_classify_rejects_corrected_pending_page_with_success_heading():
    state = classify_page(
        "https://reg.usps.com/success",
        "Account Created",
        "Your USPS.com account has been created successfully\n"
        "Correction: verification is still pending; do not sign in yet.",
        "Your USPS.com account has been created successfully",
    )
    assert state is PageState.UNKNOWN


def test_classify_success_with_previous_failure_support_footer():
    state = classify_page(
        "https://reg.usps.com/success",
        "Account Created",
        "Your USPS.com account has been created successfully\n"
        "If your previous sign-in failed, reset your password.",
        "Your USPS.com account has been created successfully",
    )
    assert state is PageState.SUCCESS


def test_classify_rejects_help_title_as_success_signal():
    state = classify_page(
        "https://reg.usps.com/register",
        "Account Created Help",
        "Account has been created successfully",
    )
    assert state is PageState.UNKNOWN


def test_classify_does_not_treat_negated_account_created_text_as_success():
    state = classify_page(
        "https://reg.usps.com/register",
        "Registration Error",
        "No account created. Please try again.",
    )
    assert state is PageState.UNKNOWN


def test_classify_rejects_success_words_inside_failure_messages():
    messages = (
        "Registration complete failed. Please retry.",
        "Registration complete could not be confirmed.",
        "Account created successfully? No, an error occurred.",
        "Your USPS.com account has been created successfully? No.",
        "Account has been created successfully only after verification; verification failed.",
        "Your USPS.com account has been created successfully? Never.",
        "Account has been created successfully only when verification passes; "
        "verification is pending.",
        "When your account has been created successfully, you can sign in. "
        "Verification is pending.",
        "Help: if your account has been created successfully, continue to Sign In.",
    )
    for message in messages:
        assert (
            classify_page("https://reg.usps.com/register", "Registration", message)
            is PageState.UNKNOWN
        )


def test_classify_rejects_cross_block_help_and_conditional_text():
    messages = (
        "Help center\nAccount has been created successfully\nLearn how registration works.",
        "If the following condition is true:\nAccount has been created successfully\nthen sign in.",
        "The following statement is false:\nAccount has been created successfully",
    )
    for message in messages:
        assert (
            classify_page("https://reg.usps.com/register", "Registration Help", message)
            is PageState.UNKNOWN
        )


def test_classify_captcha_page():
    state = classify_page("https://reg.usps.com/register", "Verify", "Complete the CAPTCHA")
    assert state is PageState.CAPTCHA_REQUIRED


class FakeLocator:
    def __init__(self, page, selector, visible=True):
        self.page = page
        self.selector = selector
        self.visible = visible
        self.first = self

    def count(self):
        return 1

    def is_visible(self):
        return self.visible

    def is_enabled(self):
        return True

    def fill(self, value):
        self.page.fills[self.selector] = value

    def evaluate(self, _script):
        return None

    def click(self):
        self.page.clicks.append(self.selector)
        if self.selector == "role-button":
            self.page.url = "https://reg.usps.com/success"
            self.page.body = self.page.after_submit_body
            self.page.otp_visible = False

    def inner_text(self):
        if self.selector.startswith("h1"):
            return self.page.body.splitlines()[0]
        return self.page.body

    def all(self):
        return []


class FakePage:
    def __init__(self):
        self.url = "https://reg.usps.com/register"
        self.body = "Verify code"
        self.clicks = []
        self.fills = {}
        self.otp_visible = True
        self.company_visible = False
        self.stop_on_wait = None
        self.after_submit_body = "Your USPS.com account has been created successfully"

    def locator(self, selector):
        if selector == "#confirm-modal":
            return FakeLocator(self, selector, True)
        if selector == "body":
            return FakeLocator(self, selector, True)
        if selector.startswith(".error-txt"):
            return FakeLocator(self, selector, False)
        if selector == "#tcompany":
            return FakeLocator(self, selector, self.company_visible)
        if selector.startswith("input["):
            return FakeLocator(self, selector, self.otp_visible)
        return FakeLocator(self, selector, True)

    def get_by_role(self, _role, name=None):
        return FakeLocator(self, "role-button", True)

    def wait_for_timeout(self, _milliseconds):
        if self.stop_on_wait:
            self.stop_on_wait.set()

    def title(self):
        return "USPS Registration"


def test_stop_between_submit_and_confirmation_blocks_final_click():
    stop = Event()
    page = FakePage()
    page.stop_on_wait = stop
    runner = UspsRegistrationRunner(AutomationConfig(page_timeout_ms=20))

    with pytest.raises(InterruptedError):
        runner._submit_account(page, RegistrationData(email="a@b.com"), stop)

    assert page.clicks == ["#btn-submit"]


def test_final_submit_click_failure_keeps_unknown_stage():
    runner = UspsRegistrationRunner(AutomationConfig())
    page = FakePage()
    original_locator = page.locator

    def locator(selector):
        value = original_locator(selector)
        if selector == "#btn-submit":
            value.click = lambda: (_ for _ in ()).throw(RuntimeError("page closed"))
        return value

    page.locator = locator

    with pytest.raises(RuntimeError, match="page closed"):
        runner._submit_account(page, RegistrationData(email="a@b.com"), Event())

    assert runner.stage == "submission_unknown"


def test_terminal_wait_checks_stop_event_before_polling():
    stop = Event()
    stop.set()
    runner = UspsRegistrationRunner(AutomationConfig(page_timeout_ms=20))
    with pytest.raises(RegistrationFlowError) as error:
        runner._wait_for_terminal(FakePage(), stop)
    assert error.value.stage == "submission_unknown"


def test_terminal_timeout_becomes_manual_review_state():
    runner = UspsRegistrationRunner(AutomationConfig(page_timeout_ms=5))
    with pytest.raises(RegistrationFlowError) as error:
        runner._wait_for_terminal(FakePage(), Event())
    assert error.value.stage == "submission_unknown"


def test_otp_is_consumed_only_after_positive_usps_success():
    page = FakePage()
    consumed = []

    class Mailbox:
        def consume_otp(self, mail_id, address, code):
            consumed.append((mail_id, address, code))

    runner = UspsRegistrationRunner(AutomationConfig(page_timeout_ms=20))
    data = RegistrationData(account_type="Personal Account", email="a@b.com")
    message = VerificationMessage("otp", "483920", 7, "sender", "subject", 1)
    runner._submit_verification(page, data, Mailbox(), message, Event())
    assert consumed == [(7, "a@b.com", "483920")]


def test_otp_is_not_consumed_when_redirect_contains_failure_text():
    page = FakePage()
    page.after_submit_body = "Registration complete failed. Please retry."
    consumed = []

    class Mailbox:
        def consume_otp(self, *_args):
            consumed.append(True)

    runner = UspsRegistrationRunner(AutomationConfig(page_timeout_ms=5))
    data = RegistrationData(account_type="Personal Account", email="a@b.com")
    message = VerificationMessage("otp", "483920", 7, "sender", "subject", 1)
    with pytest.raises(RegistrationFlowError) as error:
        runner._submit_verification(page, data, Mailbox(), message, Event())
    assert error.value.stage == "otp_submission_unknown"
    assert consumed == []


def test_business_form_visibility_does_not_accept_unknown_otp_result():
    page = FakePage()
    page.url = "https://reg.usps.com/verification-error"
    page.body = "Verification pending"
    page.otp_visible = False
    page.company_visible = True
    runner = UspsRegistrationRunner(AutomationConfig(page_timeout_ms=5))

    with pytest.raises(RegistrationFlowError) as error:
        runner._wait_for_otp_acceptance(
            page,
            RegistrationData(account_type="Business Account"),
            "input[name='verificationCode']",
            "https://reg.usps.com/register",
        )

    assert error.value.stage == "otp_submission_unknown"


def test_business_company_page_is_explicit_otp_acceptance_state():
    page = FakePage()
    page.url = "https://reg.usps.com/entreg/CompanyInformationAction_input"
    page.body = "Company Information"
    page.otp_visible = False
    page.company_visible = True
    runner = UspsRegistrationRunner(AutomationConfig(page_timeout_ms=5))

    runner._wait_for_otp_acceptance(
        page,
        RegistrationData(account_type="Business Account"),
        "input[name='verificationCode']",
        "https://reg.usps.com/entreg/EmailVerificationAction_input",
    )


def test_mail_consumption_failure_becomes_non_retryable_stage():
    class Mailbox:
        def consume_otp(self, *_args):
            raise MailboxError("offline")

    runner = UspsRegistrationRunner(AutomationConfig())
    message = VerificationMessage("otp", "483920", 7, "sender", "subject", 1)
    with pytest.raises(RegistrationFlowError) as error:
        runner._consume_accepted_otp(Mailbox(), message, "a@b.com")
    assert error.value.stage == "mail_consumption"


def test_personal_submission_polls_and_submits_email_verification(monkeypatch):
    runner = UspsRegistrationRunner(AutomationConfig(page_timeout_ms=20))
    states = iter((PageState.WAITING_FOR_EMAIL, PageState.SUCCESS))
    submitted = []
    message = VerificationMessage("otp", "483920", 8, "sender", "subject", 1)

    class Mailbox:
        def latest_mail_id(self, _address):
            return 7

        def poll_verification(self, *_args, **kwargs):
            assert kwargs["after_mail_id"] == 7
            return message

    monkeypatch.setattr(runner, "_submit_account", lambda *_args: None)
    monkeypatch.setattr(runner, "_wait_for_terminal", lambda *_args: next(states))
    monkeypatch.setattr(
        runner,
        "_submit_verification",
        lambda _page, data, _mailbox, value, _stop: submitted.append((data.email, value)),
    )
    data = RegistrationData(account_type="Personal Account", email="a@b.com")
    runner._complete_account_submission(FakePage(), data, Mailbox(), Event())
    assert submitted == [("a@b.com", message)]


def test_second_email_wait_after_verification_requires_manual_review(monkeypatch):
    runner = UspsRegistrationRunner(AutomationConfig(page_timeout_ms=20))
    states = iter((PageState.WAITING_FOR_EMAIL, PageState.WAITING_FOR_EMAIL))
    message = VerificationMessage("otp", "483920", 8, "sender", "subject", 1)

    class Mailbox:
        def latest_mail_id(self, _address):
            return 7

        def poll_verification(self, *_args, **_kwargs):
            return message

    monkeypatch.setattr(runner, "_submit_account", lambda *_args: None)
    monkeypatch.setattr(runner, "_wait_for_terminal", lambda *_args: next(states))
    monkeypatch.setattr(runner, "_submit_verification", lambda *_args: None)
    data = RegistrationData(account_type="Personal Account", email="a@b.com")

    with pytest.raises(RegistrationFlowError) as error:
        runner._complete_account_submission(FakePage(), data, Mailbox(), Event())

    assert error.value.stage == "submission_unknown"


def test_browser_failure_after_final_submit_keeps_unknown_stage(monkeypatch):
    runner = UspsRegistrationRunner(AutomationConfig(page_timeout_ms=20))

    class Mailbox:
        def latest_mail_id(self, _address):
            return 7

    monkeypatch.setattr(runner, "_submit_account", lambda *_args: None)
    monkeypatch.setattr(
        runner,
        "_wait_for_terminal",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("page closed")),
    )
    data = RegistrationData(account_type="Personal Account", email="a@b.com")

    with pytest.raises(RuntimeError, match="page closed"):
        runner._complete_account_submission(FakePage(), data, Mailbox(), Event())

    assert runner.stage == "submission_unknown"


def test_business_address_confirmation_survives_wizard_page_transition():
    class AddressLocator:
        def __init__(self, page, selector):
            self.page = page
            self.selector = selector
            self.first = self

        def count(self):
            return 0 if self.selector == "input[name='address']:visible" else 1

        def is_visible(self):
            if self.selector == "#tfName":
                return self.page.phase == "identity"
            if self.selector in ("#addressHolderStep6", "#confirmed-address"):
                return self.page.phase == "confirmed"
            if self.selector == "#btn-address-wizard-continue":
                return self.page.phase == "confirmed"
            return self.selector == "#a-address-step1"

        def is_enabled(self):
            return True

        def click(self):
            self.page.clicks.append(self.selector)
            if self.selector == "#a-address-step1":
                self.page.phase = "confirmed"
            elif self.selector == "#btn-address-wizard-continue":
                self.page.phase = "identity"

    class AddressPage:
        def __init__(self):
            self.phase = "input"
            self.clicks = []

        def locator(self, selector):
            return AddressLocator(self, selector)

        def wait_for_timeout(self, _milliseconds):
            return None

    page = AddressPage()
    runner = UspsRegistrationRunner(AutomationConfig())
    runner._start_business_address_search = (
        lambda current_page, _stop, _log: current_page.locator("#a-address-step1").click()
    )

    runner._complete_address_wizard(page, "#tfName", Event())

    assert page.phase == "identity"
    assert page.clicks == ["#a-address-step1", "#btn-address-wizard-continue"]


def test_business_address_outage_retries_then_uses_original_address(monkeypatch):
    class Response:
        status = 302
        url = "https://reg.usps.com/entreg/json/ValidateAddressAction"
        headers = {
            "location": "https://www.usps.com/root/global/server_responses/"
            "anyapp_outage_apology.htm"
        }

    class ResponseInfo:
        value = Response()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class Search:
        def __init__(self, page):
            self.page = page

        def click(self):
            self.page.clicks += 1

        def all(self):
            return []

    class Page:
        def __init__(self):
            self.clicks = 0
            self.waits = []
            self.fallback = False

        def locator(self, _selector):
            return Search(self)

        def wait_for_function(self, _script):
            return None

        def expect_response(self, _predicate, timeout):
            assert timeout == 30_000
            return ResponseInfo()

        def evaluate(self, _script):
            return None

        def wait_for_timeout(self, milliseconds):
            self.waits.append(milliseconds)

    runner = UspsRegistrationRunner(AutomationConfig())
    page = Page()
    logs = []
    monkeypatch.setattr(
        runner,
        "_accept_unverified_business_address",
        lambda current_page: setattr(current_page, "fallback", True),
    )

    runner._start_business_address_search(page, Event(), logs.append)

    assert page.clicks == 2
    assert page.waits == [2_000]
    assert page.fallback is True
    assert logs[-1] == "USPS 地址验证链路不可用，已按原地址继续"


def test_business_address_200_without_page_progress_uses_original_address(monkeypatch):
    class Response:
        status = 200
        url = "https://reg.usps.com/entreg/json/ValidateAddressAction"
        headers = {}

    class ResponseInfo:
        value = Response()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class Locator:
        def __init__(self, page):
            self.page = page

        def click(self):
            self.page.clicks += 1

        def is_visible(self):
            return False

        def all(self):
            return []

    class Page:
        def __init__(self):
            self.clicks = 0
            self.waits = []
            self.fallback = False

        def locator(self, _selector):
            return Locator(self)

        def wait_for_function(self, script, **_kwargs):
            if "jQuery._data" in script:
                return None
            raise RuntimeError("address page did not advance")

        def expect_response(self, _predicate, timeout):
            assert timeout == 30_000
            return ResponseInfo()

        def evaluate(self, _script):
            return None

        def wait_for_timeout(self, milliseconds):
            self.waits.append(milliseconds)

    runner = UspsRegistrationRunner(AutomationConfig())
    page = Page()
    logs = []
    monkeypatch.setattr(
        runner,
        "_accept_unverified_business_address",
        lambda current_page: setattr(current_page, "fallback", True),
    )

    runner._start_business_address_search(page, Event(), logs.append)

    assert page.clicks == 2
    assert page.waits == [2_000]
    assert page.fallback is True
    assert logs[-1] == "USPS 地址验证链路不可用，已按原地址继续"
