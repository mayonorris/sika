"""Tests for extraction retry and resume behavior."""
import sqlite3
from types import SimpleNamespace

import pytest

import pipeline.extract as extract


class FakeRateLimitError(Exception):
    pass


def stub_client(outcomes: list[object]) -> SimpleNamespace:
    def create(**_kwargs):
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )


def test_rate_limit_backoff_retries_same_request(monkeypatch: pytest.MonkeyPatch) -> None:
    success = object()
    outcomes = [FakeRateLimitError(), FakeRateLimitError(), success]
    sleeps = []
    monkeypatch.setattr(extract, "client", stub_client(outcomes))
    monkeypatch.setattr(extract, "RateLimitError", FakeRateLimitError)
    monkeypatch.setattr(extract.time, "sleep", sleeps.append)

    result = extract.create_completion({"model": "test"}, json_mode=True)

    assert result is success
    assert sleeps == [15, 30]


def test_rate_limit_stops_after_five_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    outcomes = [FakeRateLimitError() for _ in range(6)]
    sleeps = []
    monkeypatch.setattr(extract, "client", stub_client(outcomes))
    monkeypatch.setattr(extract, "RateLimitError", FakeRateLimitError)
    monkeypatch.setattr(extract.time, "sleep", sleeps.append)

    with pytest.raises(FakeRateLimitError):
        extract.create_completion({"model": "test"}, json_mode=False)

    assert sleeps == [15, 30, 60, 120, 120]


def test_source_has_observations() -> None:
    con = sqlite3.connect(":memory:")
    con.executescript(extract.SCHEMA)
    assert not extract.source_has_observations(con, "source.pdf")
    con.execute(
        """INSERT INTO observations
           (indicator, indicator_label, geography, period, value, unit,
            source_doc, source_page, confidence)
           VALUES ('cpi_index', 'IHPC', 'Togo', '2026-06', 100, 'index',
                   'source.pdf', 1, 1.0)"""
    )
    assert extract.source_has_observations(con, "source.pdf")


def test_parse_skip_existing_flag() -> None:
    args = extract.parse_args(["--skip-existing", "data/raw/source.pdf"])
    assert args.skip_existing is True
    assert args.target == "data/raw/source.pdf"


def test_main_skips_existing_file(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    database = tmp_path / "sika.db"
    source = tmp_path / "existing.pdf"
    source.touch()
    with sqlite3.connect(database) as con:
        con.executescript(extract.SCHEMA)
        con.execute(
            """INSERT INTO observations
               (indicator, indicator_label, geography, period, value, unit,
                source_doc, source_page, confidence)
               VALUES ('cpi_index', 'IHPC', 'Togo', '2026-06', 100, 'index',
                       ?, 1, 1.0)""",
            (source.name,),
        )
    monkeypatch.setattr(extract, "DB_PATH", str(database))

    def fail_if_processed(*_args):
        raise AssertionError("existing source should not be processed")

    monkeypatch.setattr(extract, "process_pdf", fail_if_processed)
    extract.main(["--skip-existing", str(source)])

    assert f"Skipping {source.name}" in capsys.readouterr().out
