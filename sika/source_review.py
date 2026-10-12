"""Export a read-only source review packet. Never approve or publish observations."""

import argparse
from collections import Counter
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sqlite3
from urllib.parse import urlparse
from urllib.request import url2pathname

import sqlalchemy as sa

from sika import quality
from sika.migrate_inflation import checksum, read_snapshot
from sika.source_facts import REVIEW_VERSION, SPOT_CHECKS
from sika.source_tables import SUPPORTED_HASHES, TABLE_PARSER_VERSION, read_source_facts

FACT_FIELDS = ("source_doc", "content_sha256", "source_page", "concept", "geography", "unit",
               "frequency", "category", "sector", "aggregation_level", "price_basis",
               "seasonal_adjustment", "source_variant", "period", "value")
IDENTITY_FIELDS = tuple(k for k in FACT_FIELDS if k not in {"source_page", "value"})


def _hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def compare_source_fact(observation: dict, fact: dict) -> list[str]:
    """Compare every source/identity field and exact numeric value, never page text.

    A number occurring somewhere on a page does not establish a matching cell.
    Missing fields differ, including when both inputs omit a required field.
    """
    differences = []
    for field in FACT_FIELDS:
        if field not in observation or field not in fact:
            differences.append(field)
        elif field == "value":
            value, expected = quality.numeric(observation[field]), quality.numeric(fact[field])
            if value is None or expected is None or value != expected:
                differences.append(field)
        elif observation[field] != fact[field]:
            differences.append(field)
    return differences


def _observations(snapshot: dict) -> list[dict]:
    catalogs = {name: {r["id"]: r for r in snapshot[name]} for name in
                ("series", "concepts", "geographies", "units", "dimensions", "releases")}
    result = []
    # Do not use inner joins: even an orphan must remain visible for review.
    for row in snapshot["observations"]:
        series = catalogs["series"].get(row["series_id"], {})
        release = catalogs["releases"].get(row["release_id"], {})
        dimensions = catalogs["dimensions"].get(series.get("dimensions_id"), {})
        result.append(dict(row, source_doc=release.get("source_doc"),
                           content_sha256=release.get("content_sha256"), series_key=series.get("series_key"),
                           concept=catalogs["concepts"].get(series.get("concept_id"), {}).get("code"),
                           geography=catalogs["geographies"].get(series.get("geography_id"), {}).get("code"),
                           unit=catalogs["units"].get(series.get("unit_id"), {}).get("code"),
                           frequency=series.get("frequency"), **{k: dimensions.get(k) for k in quality.DIMENSIONS},
                           legacy_ids=[link["legacy_id"] for link in snapshot["legacy_observation_links"]
                                       if link["observation_id"] == row["id"]], source_check="pending"))
    return result


def _spot_checks(observations: list[dict], releases: list[dict], artifacts: dict) -> list[dict]:
    checks = []
    for release in releases:
        # These anchors are valid only for the exact inspected, verified source bytes.
        artifact = artifacts[release["id"]]
        if artifact.get("error") or artifact.get("sha256") != release["content_sha256"]:
            continue
        facts = [f for f in SPOT_CHECKS if f["source_doc"] == release["source_doc"]
                 and f["content_sha256"] == release["content_sha256"]]
        rows = [r for r in observations if r["release_id"] == release["id"]]
        for fact in facts:
            candidates = [r for r in rows if all(r[k] == fact[k] for k in IDENTITY_FIELDS)]
            differences = {str(r["id"]): compare_source_fact(r, fact) for r in candidates}
            status = ("missing" if not candidates else "ambiguous" if len(candidates) != 1 else
                      "mismatch" if differences[str(candidates[0]["id"])] else "matched")
            for row in candidates:
                row["source_check"] = "matched" if status == "matched" else "mismatch"
                if status == "matched":
                    row["source_check_method"] = fact.get("method", "selected_anchor")
            checks.append(dict(release_id=release["id"], fact_id=fact["fact_id"], expected=dict(fact),
                               observation_ids=[r["id"] for r in candidates], status=status,
                               differences=differences))
    return checks


def compare_source_cells(observations: list[dict], cells: list[dict]) -> tuple[list[dict], list]:
    """Check canonical rows against independently parsed, fully scoped PDF cells.

    Repeated printed evidence is acceptable only when every candidate has the
    same exact value. No candidate is chosen by confidence or nearest number.
    Rows outside the parsed source cohort remain pending, never implicitly pass.
    """
    checks, findings = [], []
    sources = {(cell["source_doc"], cell["content_sha256"]) for cell in cells}
    scope = tuple(field for field in FACT_FIELDS if field != "value")
    for row in observations:
        if (row["source_doc"], row["content_sha256"]) not in sources:
            continue
        candidates = [cell for cell in cells if all(row.get(key) == cell.get(key) for key in scope)]
        if not candidates and row.get("source_check_method") == "inspected_narrative" and row["source_check"] == "matched":
            continue  # An explicit checksum-bound prose check preserves its own page citation.
        differences = [compare_source_fact(row, cell) for cell in candidates]
        status = "missing" if not candidates else "mismatch" if any(differences) else "matched"
        if status != "matched":
            findings.append(quality.Finding("FAIL", "source_cell_" + status,
                                            "Canonical observation lacks fully agreeing source cells",
                                            row["release_id"], row["id"]))
            row["source_check"] = "mismatch"
        elif row["source_check"] != "mismatch":
            row["source_check"] = "matched"
            row["source_check_method"] = "deterministic_table"
        checks.append(dict(observation_id=row["id"], release_id=row["release_id"], status=status,
                           cells=candidates, differences=differences))
    return checks, findings


def _read_source_cells(releases: list[dict], artifacts: dict) -> tuple[list[dict], list]:
    cells, findings = [], []
    for release in releases:
        doc, digest = release["source_doc"], release["content_sha256"]
        if SUPPORTED_HASHES.get(doc) != digest:
            continue  # No source-table review is transferred to unknown/synthetic bytes.
        artifact = artifacts[release["id"]]
        if artifact.get("error") or artifact.get("sha256") != digest:
            continue
        uri = urlparse(release["storage_uri"])
        try:
            parsed = read_source_facts(Path(url2pathname(uri.path)), doc, digest)
            if not parsed:
                raise ValueError("Empty supported source-cell cohort")
            cells.extend(parsed)
        except (ValueError, OSError):
            findings.append(quality.Finding("FAIL", "source_table_parse_failed",
                                            "The checksum-bound source-table parser refused the artifact",
                                            release["id"]))
    return cells, findings


def _packet(snapshot: dict, artifacts: dict, originals: tuple | None, findings: list,
            source_cells: list[dict] | None = None) -> dict:
    findings = list(findings)
    observations = _observations(snapshot)
    checks = _spot_checks(observations, snapshot["releases"], artifacts)
    for check in checks:
        if check["status"] != "matched":
            findings.append(quality.Finding("FAIL", "source_spot_check_" + check["status"],
                                            "Source anchor does not have one exact canonical match: " + check["fact_id"],
                                            check["release_id"]))
    table_checks, table_findings = compare_source_cells(observations, source_cells or [])
    findings.extend(table_findings)
    quarantine = [dict(row, original_row_sha256=_hash(row["original_row"]), review_status="pending")
                  for row in snapshot["inflation_migration_rows"] if row["disposition"] == "quarantined"]
    sources = {r["id"]: r for r in snapshot["sources"]}
    releases = []
    for release in snapshot["releases"]:
        rows = [r for r in observations if r["release_id"] == release["id"]]
        source = sources.get(release["source_id"], {})
        relevant = [asdict(f) for f in findings if f.release_id in {None, release["id"]}]
        blockers = ["source_values_review_pending"]  # Spot checks never certify an entire cohort.
        license_url, attribution = source.get("license_url"), source.get("attribution")
        if (source.get("reuse_status") != "allowed" or not isinstance(license_url, str) or
            urlparse(license_url.strip()).scheme not in {"http", "https"} or not urlparse(license_url.strip()).netloc or
            not isinstance(attribution, str) or not attribution.strip()):
            blockers.append("source_rights_unverified")
        if release["published_on"] is None:
            blockers.append("publication_date_unverified")
        if any(r["revision_status"] == "unknown" for r in rows):
            blockers.append("revision_marks_unverified")
        if any(r["source_doc"] == release["source_doc"] for r in quarantine):
            blockers.append("quarantine_review_pending")
        if any(f["severity"] == "FAIL" for f in relevant):
            blockers.append("technical_checks_failed")
        releases.append(dict(release_id=release["id"], source_doc=release["source_doc"],
                             source_url=release["source_url"], source_status=release["status"],
                             storage_uri=release["storage_uri"],
                             content_sha256=release["content_sha256"], published_on=release["published_on"],
                             reuse_status=source.get("reuse_status"), license_url=source.get("license_url"),
                             attribution=source.get("attribution"), artifact=artifacts[release["id"]],
                             observation_count=len(rows), observations=rows, publication_blockers=blockers,
                             findings=relevant))
    matched = sum(r["source_check"] == "matched" for r in observations)
    failed = sum(f.severity == "FAIL" for f in findings)
    result = dict(review_version=REVIEW_VERSION, schema_version=quality.SCHEMA_VERSION,
                  source_table_parser_version=TABLE_PARSER_VERSION,
                  validator_version=quality.VALIDATOR_VERSION,
                  snapshot_sha256=quality._fingerprint(snapshot, artifacts, originals),
                  source_facts_sha256=_hash(SPOT_CHECKS),
                  source_cells_sha256=_hash(source_cells or []),
                  legacy_snapshot_sha256=originals[0] if originals else None,
                  publication_authorized=False, certification_status="pending",
                  status="failed" if failed else "needs_review", hard_failure_count=failed,
                  observation_count=len(observations), spot_check_count=len(checks),
                  matched_observation_count=matched, pending_observation_count=len(observations) - matched,
                  source_comparison_status="complete" if matched == len(observations) and not failed else "incomplete",
                  quarantine_count=len(quarantine), quarantine=quarantine,
                  legacy_links=[dict(row, original_row_sha256=_hash(row["original_row"]))
                                for row in snapshot["legacy_observation_links"]],
                  source_table_cell_count=len(source_cells or []), source_table_checks=table_checks,
                  spot_checks=checks, releases=releases, findings=[asdict(f) for f in findings],
                  orphan_observations=[r for r in observations if r["release_id"] not in {x["id"] for x in snapshot["releases"]}])
    result["review_packet_sha256"] = _hash(result)
    return result


def build_review_packet(target: Path, *, legacy: Path | None = None, source_root: Path | None = None) -> dict:
    """Read a consistent SQLite snapshot and its source bytes, without any DB writes.

    This is a review aid, not an input to approve(): no operator is resolved or
    authorized, no quality/review/event record is inserted, and no state changes.
    """
    target = target.resolve(strict=True)
    if not target.is_file() or (legacy is not None and target.samefile(legacy)):
        raise ValueError("Choose an existing separate Trust Core database")
    root = source_root or target.parent / "trust_core_sources"

    def connect():
        connection = sqlite3.connect(target.as_uri() + "?mode=ro", uri=True)
        connection.execute("PRAGMA query_only=ON")
        return connection

    engine = sa.create_engine("sqlite://", creator=connect)
    try:
        with engine.connect() as con:
            con.exec_driver_sql("BEGIN")
            names = set(sa.inspect(con).get_table_names())
            required = quality.SNAPSHOT_TABLES + ("alembic_version", "quality_runs", "review_decisions", "publication_events")
            if not set(required).issubset(names):
                raise ValueError("Target must be an existing versioned Trust Core database, not the MVP")
            if con.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one() != quality.SCHEMA_VERSION:
                raise ValueError("Unsupported Trust Core schema revision")
            tables = sa.MetaData()
            tables.reflect(con)
            snapshot = quality._read_tables(con, tables.tables)
            if not snapshot["releases"] or not snapshot["observations"]:
                raise ValueError("Source review requires a nonempty migrated corpus")
            originals = read_snapshot(legacy) if legacy is not None else None
            artifacts = {r["id"]: quality._artifact(r, root) for r in snapshot["releases"]}
            findings = quality.examine(snapshot, artifacts, originals)
            source_cells, parser_findings = _read_source_cells(snapshot["releases"], artifacts)
            findings.extend(parser_findings)
            result = _packet(snapshot, artifacts, originals, findings, source_cells)
            # Recheck after comparison too. A packet never silently claims that
            # inspected anchors still refer to bytes which changed during the read.
            final_artifacts = {r["id"]: quality._artifact(r, root) for r in snapshot["releases"]}
            changed = [release_id for release_id in artifacts if artifacts[release_id] != final_artifacts[release_id]]
            for release_id in changed:
                findings.append(quality.Finding("FAIL", "source_changed_during_review", "Source bytes changed during review", release_id))
                artifacts[release_id] = dict(artifacts[release_id], error="source_changed_during_review",
                                             final_check=final_artifacts[release_id])
            legacy_changed = originals and checksum(legacy) != originals[0].removeprefix("sha256:")
            if legacy_changed:
                findings.append(quality.Finding("FAIL", "legacy_changed_during_review", "Legacy snapshot changed during review"))
            if changed or legacy_changed:
                unaffected_cells = [cell for cell in source_cells if not any(
                    release["id"] in changed and release["source_doc"] == cell["source_doc"]
                    for release in snapshot["releases"])]
                result = _packet(snapshot, artifacts, originals, findings, unaffected_cells)
            return result
    finally:
        engine.dispose()


def _check_report_path(report: Path, protected: list[Path]) -> None:
    if report.suffix.lower() != ".json" or any(
            report.resolve() == path.resolve() or (report.exists() and path.exists() and report.samefile(path))
            for path in protected):
        raise ValueError("Report must be a separate .json file, never a database or source artifact")


def public_summary(packet: dict) -> dict:
    """Redact draft observations and source text before sharing an audit summary.

    Keep ID/count/hash coverage, not values, original labels, raw originals,
    source-cell payloads, archive paths or rights/attribution text. A public audit
    summary is never an alternate published-data endpoint or approval document.
    """
    result = {key: packet[key] for key in (
        "review_version", "schema_version", "validator_version", "source_table_parser_version",
        "snapshot_sha256", "source_facts_sha256", "source_cells_sha256", "legacy_snapshot_sha256",
        "publication_authorized", "certification_status", "status", "hard_failure_count",
        "observation_count", "spot_check_count", "matched_observation_count", "pending_observation_count",
        "source_comparison_status", "quarantine_count", "source_table_cell_count", "review_packet_sha256")}
    result["summary_data_redacted"] = True
    result["releases"] = [
        {key: release[key] for key in ("release_id", "source_doc", "source_url", "source_status",
                                      "content_sha256", "published_on", "reuse_status", "observation_count",
                                      "publication_blockers")} | {
            "source_checks": [{"observation_id": row["id"], "source_page": row["source_page"],
                               "source_check": row["source_check"], "method": row.get("source_check_method")}
                              for row in release["observations"]]}
        for release in packet["releases"]]
    result["quarantine"] = [{key: row[key] for key in (
        "run_id", "legacy_id", "source_doc", "source_page", "disposition", "reason",
        "original_row_sha256", "review_status")} for row in packet["quarantine"]]
    result["findings"] = [{key: finding[key] for key in (
        "severity", "code", "release_id", "observation_id", "legacy_id")} for finding in packet["findings"]]
    result["summary_sha256"] = _hash(result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--legacy", type=Path, help="Exact read-only legacy snapshot for migrated rows")
    parser.add_argument("--source-root", type=Path, help="Bounded local archive; default is target sibling trust_core_sources")
    parser.add_argument("--report", type=Path, help="Separate informational JSON packet, NOT approval evidence")
    parser.add_argument("--summary-only", action="store_true", help="Redact observation values and originals from the exported report")
    args = parser.parse_args(argv)
    try:
        protected = [p for p in (args.target, args.legacy) if p is not None]
        if args.report:
            _check_report_path(args.report, protected)
        result = build_review_packet(args.target, legacy=args.legacy, source_root=args.source_root)
        if args.report:
            # The archive may use arbitrary extensions; guard inode aliases as well.
            for release in result["releases"]:
                uri = urlparse(release["storage_uri"])
                if uri.scheme == "file":
                    protected.append(Path(url2pathname(uri.path)))
            _check_report_path(args.report, protected)
            args.report.parent.mkdir(parents=True, exist_ok=True)
            export = public_summary(result) if args.summary_only else result
            args.report.write_text(json.dumps(export, ensure_ascii=False, indent=2, default=str) + "\n",
                                   encoding="utf-8", newline="\n")
    except (ValueError, OSError, sqlite3.Error, sa.exc.SQLAlchemyError) as exc:
        print(f"ERROR: {exc}")
        return 2
    print(f"Source review: {result['status'].upper()} ({result['review_version']})")
    print(f"Snapshot SHA-256: {result['snapshot_sha256']}")
    print(f"{result['matched_observation_count']}/{result['observation_count']} source comparisons matched; "
          f"{result['pending_observation_count']} observations without matching source evidence; "
          f"{result['quarantine_count']} quarantined originals preserved")
    for release in result["releases"]:
        counts = Counter(r["source_check"] for r in release["observations"])
        print(f"Release {release['release_id']} ({release['source_doc']}): "
              f"{counts['matched']} matched, {counts['pending']} pending, {counts['mismatch']} mismatches")
        print("  Publication blockers: " + ", ".join(release["publication_blockers"]))
    for finding in result["findings"]:
        if finding["severity"] == "FAIL":
            print(f"  [FAIL] {finding['code']}: {finding['message']}")
    print("Read-only packet. Certification pending; no publication authority or reviewer approval.")
    return 1 if result["hard_failure_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
