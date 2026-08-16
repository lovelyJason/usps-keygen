import checkpoint
from checkpoint import checkpoint_has_unfinished, load_checkpoint, save_checkpoint
from models import RegistrationData, RegistrationResult


def test_checkpoint_round_trip_preserves_generated_email_and_result(tmp_path):
    path = tmp_path / "batch.json"
    rows = [
        RegistrationData(
            email="usps-fixed@velydora.com",
            username="user1",
            password="SensitivePass123",
            security_answer1="AnswerOne",
            security_answer2="AnswerTwo",
        )
    ]
    results = [RegistrationResult.failed("mail", "timeout")]

    save_checkpoint(path, rows, results)
    loaded_rows, loaded_results = load_checkpoint(path)

    assert loaded_rows == rows
    assert loaded_results == results
    assert path.stat().st_mode & 0o077 == 0
    raw = path.read_text(encoding="utf-8")
    assert "SensitivePass123" not in raw
    assert "AnswerOne" not in raw
    assert path.with_suffix(".key").stat().st_mode & 0o077 == 0


def test_checkpoint_reports_unfinished_rows(tmp_path):
    path = tmp_path / "batch.json"
    rows = [RegistrationData(email="a@b.com", username="user1")]
    save_checkpoint(path, rows, [RegistrationResult("pending", "queued", "waiting")])
    assert checkpoint_has_unfinished(path)
    save_checkpoint(path, rows, [RegistrationResult.success("complete", "done")])
    assert not checkpoint_has_unfinished(path)


def test_artifact_cleanup_failure_preserves_checkpoint(monkeypatch, tmp_path):
    path = tmp_path / "batch.json"
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    save_checkpoint(
        path,
        [RegistrationData(email="a@b.com", username="user1")],
        [RegistrationResult("pending", "queued", "waiting")],
    )
    monkeypatch.setattr(
        checkpoint.shutil,
        "rmtree",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("busy")),
    )

    assert not checkpoint.clear_private_data(path, artifacts)
    assert path.exists()
    assert path.with_suffix(".key").exists()


def test_successful_clear_removes_temporary_checkpoint(tmp_path):
    path = tmp_path / "batch.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text("encrypted residue", encoding="utf-8")

    assert checkpoint.clear_private_data(path, tmp_path / "missing-artifacts")
    assert not temporary.exists()
