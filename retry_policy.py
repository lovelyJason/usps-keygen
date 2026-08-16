from batch_io import retry_mailbox_address
from models import RegistrationData, RegistrationResult

MANUAL_REVIEW_STAGES = {
    "captcha",
    "complete",
    "email_verification",
    "identity_verification",
    "mail_consumption",
    "post_submit_mail",
    "otp_submission_unknown",
    "submission_unknown",
}


def runnable_indices(results: list[RegistrationResult]) -> list[int]:
    return [
        index
        for index, result in enumerate(results)
        if result.status == "pending"
        or (result.status in {"failed", "stopped"} and result.stage not in MANUAL_REVIEW_STAGES)
    ]


def rotate_manual_retry_addresses(
    rows: list[RegistrationData], results: list[RegistrationResult], indices: list[int]
) -> list[tuple[int, str]]:
    changed = []
    for index in indices:
        if results[index].status not in {"failed", "stopped"}:
            continue
        rows[index].email = retry_mailbox_address(rows[index].email, 2)
        changed.append((index, rows[index].email))
    return changed
