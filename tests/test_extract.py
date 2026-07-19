"""Tests for extraction batching, retry, resume, and ordering."""
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

import pipeline.extract as extract


class FakeRateLimitError(Exception):
    pass


class FakePage:
    def __init__(self, text: str) -> None:
        self.text = text

    def extract_text(self) -> str:
        return self.text

    def extract_tables(self) -> list:
        return []


class FakePdf:
    def __init__(self, pages: list[FakePage]) -> None:
        self.pages = pages

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None


def stub_client(outcomes: list[object]) -> SimpleNamespace:
    def create(**_kwargs):
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )


def observation(source_page: int) -> dict:
    return {
        "indicator": "cpi_index",
        "indicator_label": "IHPC",
        "geography": "Togo",
        "period": "2026-05",
        "value": 100,
        "unit": "index",
        "source_page": source_page,
        "confidence": 1.0,
    }


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


def test_batch_payload_marks_three_pages_and_caps_length() -> None:
    pages = [(1, "a" * 12000), (2, "b" * 12000), (3, "c" * 12000)]

    payload = extract.batch_payload(pages)

    assert len(payload) <= 30000
    assert payload.count("=== SOURCE PAGE") == 3
    assert all(f"=== SOURCE PAGE {page} ===" in payload for page in (1, 2, 3))


def test_process_pdf_batches_three_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    con = sqlite3.connect(":memory:")
    con.executescript(extract.SCHEMA)
    calls = []
    pages = [FakePage(f"page {number} " + "x" * 100) for number in range(1, 8)]
    monkeypatch.setattr(extract.pdfplumber, "open", lambda _path: FakePdf(pages))

    def fake_extract(payload: str) -> list[dict]:
        calls.append(payload)
        tagged = [
            number
            for number in range(1, 8)
            if f"SOURCE PAGE {number} ===" in payload
        ]
        return [observation(number) for number in tagged]

    monkeypatch.setattr(extract, "extract_page", fake_extract)
    count = extract.process_pdf(Path("source.pdf"), con)

    assert count == 7
    assert [call.count("=== SOURCE PAGE") for call in calls] == [3, 3, 1]
    assert all(len(call) <= extract.MAX_BATCH_CHARS for call in calls)
    assert con.execute("SELECT COUNT(*) FROM passages").fetchone()[0] == 7


def test_skip_existing_resumes_at_page_level(monkeypatch: pytest.MonkeyPatch) -> None:
    con = sqlite3.connect(":memory:")
    con.executescript(extract.SCHEMA)
    con.execute(
        "INSERT INTO passages (source_doc, page, text) VALUES ('source.pdf', 2, 'done')"
    )
    pages = [FakePage(f"page {number} " + "x" * 100) for number in range(1, 5)]
    monkeypatch.setattr(extract.pdfplumber, "open", lambda _path: FakePdf(pages))
    calls = []
    monkeypatch.setattr(extract, "extract_page", lambda payload: calls.append(payload) or [])

    extract.process_pdf(Path("source.pdf"), con, skip_existing=True)

    assert len(calls) == 1
    assert "SOURCE PAGE 2 ===" not in calls[0]
    assert all(f"SOURCE PAGE {page} ===" in calls[0] for page in (1, 3, 4))
    stored = con.execute(
        "SELECT page FROM passages WHERE source_doc = 'source.pdf' ORDER BY page"
    ).fetchall()
    assert stored == [(1,), (2,), (3,), (4,)]


def test_observation_page_must_belong_to_batch() -> None:
    con = sqlite3.connect(":memory:")
    con.executescript(extract.SCHEMA)

    count = extract.insert_observations(
        con, "source.pdf", {1, 2, 3}, [observation(2), observation(9)]
    )

    assert count == 1
    assert con.execute("SELECT source_page FROM observations").fetchall() == [(2,)]


def test_priority_order_then_alphabetical_remainder() -> None:
    names = [
        "z.pdf",
        "inseed_pib_estimations_2025.pdf",
        "a.pdf",
        "inseed_ihpc_2026-05.pdf",
        "inseed_bulletin_mensuel_2025-09.pdf",
        "inseed_comptes_trimestriels_2025-T4.pdf",
    ]

    ordered = extract.prioritize_pdfs([Path(name) for name in names])

    assert [path.name for path in ordered] == [*extract.PRIORITY_FILES, "a.pdf", "z.pdf"]


def test_parse_resume_and_priority_flags() -> None:
    args = extract.parse_args(["--skip-existing", "--priority", "data/raw"])

    assert args.skip_existing is True
    assert args.priority is True
    assert args.target == "data/raw"
