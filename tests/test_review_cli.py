"""Administrative commands never accept a caller-authored reviewer identity."""

import subprocess

import pytest
import sqlalchemy as sa

from sika import publication, review


def test_identity_only_reports_os_principal(monkeypatch, capsys):
    monkeypatch.delenv("SIKA_REVIEWER_IDS", raising=False)
    monkeypatch.setattr(publication, "operator_identity", lambda: "test:principal")
    assert review.main(["identity"]) == 0
    assert capsys.readouterr().out.strip() == "test:principal"
    assert publication._reviewers() == set()


def test_failed_os_lookup_is_a_readable_cli_refusal(monkeypatch, capsys):
    def unavailable():
        raise subprocess.SubprocessError("OS identity lookup unavailable")
    monkeypatch.setattr(publication, "operator_identity", unavailable)
    assert review.main(["identity"]) == 1
    assert "REFUSED:" in capsys.readouterr().out


def test_approve_default_deny_never_creates_target(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("SIKA_REVIEWER_IDS", raising=False)
    monkeypatch.setattr(publication, "operator_identity", lambda: "test:principal")
    evidence = tmp_path / "evidence.json"
    evidence.write_text('{"reviewer": "claimed:admin"}', encoding="utf-8")
    target = tmp_path / "missing.db"
    assert review.main(["approve", "--target", str(target), "--release-id", "1", "--quality-run-id", "1",
                        "--reason", "No authority claimed", "--evidence", str(evidence)]) == 1
    assert not target.exists()
    assert "not allowlisted" in capsys.readouterr().out


def test_cli_has_no_reviewer_override():
    with pytest.raises(SystemExit) as error:
        review.main(["identity", "--reviewer", "claimed:admin"])
    assert error.value.code == 2


def test_database_errors_do_not_echo_review_parameters(tmp_path, monkeypatch, capsys):
    evidence = tmp_path / "evidence.json"
    evidence.write_text('{}', encoding="utf-8")
    def unavailable(*args, **kwargs):
        raise sa.exc.StatementError("failure", "INSERT", {"evidence": "PRIVATE_REVIEW_TEXT"}, ValueError())
    monkeypatch.setattr(publication, "approve", unavailable)
    assert review.main(["approve", "--target", str(tmp_path / "target.db"), "--release-id", "1",
                        "--quality-run-id", "1", "--reason", "Review", "--evidence", str(evidence)]) == 1
    output = capsys.readouterr().out
    assert "PRIVATE_REVIEW_TEXT" not in output
    assert "Database constraint" in output
