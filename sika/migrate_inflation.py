"""Migrate a legacy SQLite inflation snapshot with exhaustive reconciliation.

Run with python -m sika.migrate_inflation --help. No network or .env loading.
"""

import argparse
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
from statistics import fmean, pstdev

import sqlalchemy as sa

from sika.database import create_database_engine
from sika.inflation_mapping import CONCEPT_LABELS, MAPPING_VERSION, PROFILES, Mapping, in_scope, map_row, period_frequency

SCHEMA_VERSION = "0002_inflation_audit"


def checksum(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_snapshot(path: Path) -> tuple[str, list[dict], list[dict]]:
    """Read a checkpointed SQLite snapshot without changing its journal or data."""
    path = path.resolve(strict=True)
    for suffix in ("-wal", "-journal"):
        sidecar = Path(str(path) + suffix)
        if sidecar.exists() and sidecar.stat().st_size:
            raise ValueError("Legacy database has an active journal; use a checkpointed snapshot")
    digest = checksum(path)
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as con:
        con.row_factory = sqlite3.Row
        con.execute("BEGIN")
        rows = [dict(r) for r in con.execute("SELECT * FROM observations ORDER BY id") if in_scope(dict(r))]
        docs = {r["source_doc"] for r in rows}
        passages = [dict(r) for r in con.execute("SELECT * FROM passages ORDER BY id") if r["source_doc"] in docs]
    if checksum(path) != digest:
        raise ValueError("Legacy database changed while reading; use an immutable snapshot")
    return f"sha256:{digest}", rows, passages


def source_manifest(raw: Path, rows: list[dict]) -> dict:
    manifest = {}
    for name in sorted({row["source_doc"] for row in rows}):
        profile = PROFILES.get(name)
        path = raw / name
        if Path(name).name != name or path.resolve().parent != raw.resolve():
            reason, digest = "unsafe_source_filename", None
        elif profile is None:
            reason, digest = "source_not_reviewed", None
        elif not path.is_file():
            reason, digest = "source_file_missing", None
        else:
            digest = checksum(path)
            reason = "verified" if digest == profile["sha256"] else "source_checksum_changed"
        manifest[name] = {"sha256": digest, "reason": reason}
    return manifest


def _insert(con, table, **values) -> int:
    return con.execute(table.insert().values(**values)).inserted_primary_key[0]


def _catalog_id(con, table, identity: dict, **values) -> int:
    found = con.execute(sa.select(table.c.id).where(
        *[table.c[key] == value for key, value in identity.items()])).scalar_one_or_none()
    return found if found is not None else _insert(con, table, **identity, **values)


def _series_id(con, tables, mapping: Mapping) -> int:
    concept = _catalog_id(con, tables["concepts"], {"code": mapping.concept}, label_fr=CONCEPT_LABELS[mapping.concept])
    geo = _catalog_id(con, tables["geographies"], {"code": mapping.geography}, label_fr=mapping.geography_label)
    unit = _catalog_id(con, tables["units"], {"code": mapping.unit},
                       label_fr="Pourcentage" if mapping.unit == "percent" else "Indice",
                       symbol="%" if mapping.unit == "percent" else "index", quantity=mapping.unit, scale=1)
    dimensions = _catalog_id(con, tables["dimensions"], mapping.dimensions(),
        description=f"S0.3 {MAPPING_VERSION}; methodology {mapping.source_variant}; seasonal adjustment not stated")
    identity = dict(concept_id=concept, geography_id=geo, unit_id=unit, dimensions_id=dimensions, frequency=mapping.frequency)
    external = dict(concept=mapping.concept, geography=mapping.geography, unit=mapping.unit,
                    frequency=mapping.frequency, dimensions=mapping.dimensions())
    signature = hashlib.sha256(json.dumps(external, sort_keys=True).encode()).hexdigest()
    return _catalog_id(con, tables["series"], identity,
                       series_key=f"{mapping.concept}.{mapping.geography}.{signature}",
                       label_fr=CONCEPT_LABELS[mapping.concept])


def _create_releases(con, tables, raw: Path, archive: Path, manifest: dict) -> dict:
    releases = {}
    for name, artifact in manifest.items():
        if artifact["reason"] != "verified":
            continue
        profile = PROFILES[name]
        digest = artifact["sha256"]
        archive.mkdir(parents=True, exist_ok=True)
        archived = archive / f"{digest}.pdf"
        if not archived.exists():
            try:
                with (raw / name).open("rb") as source, archived.open("xb") as destination:
                    shutil.copyfileobj(source, destination)
            except FileExistsError:
                pass
        if checksum(archived) != digest:
            raise ValueError("Archived source checksum mismatch; no data was imported")
        source = _catalog_id(con, tables["sources"], {"source_key": profile["source_key"]},
                             publisher=profile["publisher"], title=profile["title"],
                             canonical_url=profile["url"], reuse_status="unknown")
        releases[name] = _insert(con, tables["releases"],
            release_key=f"{name}:{digest}", source_id=source, source_doc=name,
            source_url=profile["url"], publisher=profile["publisher"], content_sha256=digest,
            published_on=None, retrieved_at=datetime.now(timezone.utc), storage_uri=archived.resolve().as_uri(),
            media_type="application/pdf", status="draft")
    return releases


def _copy_rows(con, tables, run: int, snapshot: str, rows: list[dict], manifest: dict, releases: dict) -> None:
    assignments = {}
    groups = defaultdict(list)
    for row in rows:
        reason = manifest[row["source_doc"]]["reason"]
        mapping = None
        if reason == "verified":
            mapping, reason = map_row(row, PROFILES[row["source_doc"]])
        if mapping is None:
            assignments[row["id"]] = (None, "quarantined", reason)
        else:
            groups[(mapping, row["source_doc"], row["period"])].append(row)
    for (mapping, doc, period), group in groups.items():
        if len({Decimal(str(row["value"])) for row in group}) != 1:
            for row in group:
                assignments[row["id"]] = (None, "quarantined", "conflicting_legacy_values")
            continue
        ordered = sorted(group, key=lambda row: (row["source_page"], row["id"]))
        first = ordered[0]
        observation = _insert(con, tables["observations"], series_id=_series_id(con, tables, mapping),
            release_id=releases[doc], period=period, value=Decimal(str(first["value"])),
            source_page=first["source_page"], source_locator=f"PDF page {first['source_page']}",
            original_label=first["indicator_label"], confidence=first["confidence"],
            revision_status="unknown", status="draft")
        for index, row in enumerate(ordered):
            assignments[row["id"]] = (observation, "mapped" if index == 0 else "merged",
                                       "reviewed_mapping" if index == 0 else "same_series_period_release_and_value")
            _insert(con, tables["legacy_observation_links"], legacy_database=snapshot, legacy_id=row["id"],
                    observation_id=observation, original_row=row)
    for row in rows:
        target, disposition, reason = assignments[row["id"]]
        _insert(con, tables["inflation_migration_rows"], run_id=run, legacy_id=row["id"], target_id=target,
                source_doc=row["source_doc"], source_page=row["source_page"], disposition=disposition,
                reason=reason, original_row=row)


def _copy_passages(con, tables, run: int, snapshot: str, passages: list[dict], manifest: dict, releases: dict) -> None:
    groups = defaultdict(list)
    for row in passages:
        groups[(row["source_doc"], row["page"])].append(row)
    for (doc, page), group in groups.items():
        reason = manifest[doc]["reason"]
        target = None
        if reason == "verified":
            if not isinstance(page, int) or not 1 <= page <= PROFILES[doc]["pages"]:
                reason = "invalid_source_page"
            elif len({r["text"] for r in group}) != 1:
                reason = "conflicting_legacy_passages"
            else:
                target = _insert(con, tables["passages"], release_id=releases[doc], source_page=page,
                                 text=group[0]["text"], legacy_database=snapshot, legacy_id=group[0]["id"])
        for index, row in enumerate(group):
            _insert(con, tables["inflation_migration_passages"], run_id=run, legacy_id=row["id"], target_id=target,
                    source_doc=doc, source_page=page, original_row=row, reason=reason,
                    disposition=("mapped" if index == 0 else "merged") if target else "quarantined")


def reconcile(con, tables, run: int, snapshot: str, rows: list[dict], passages: list[dict]) -> dict:
    """Read persisted records and compare complete values, identities and citations."""
    audit = [dict(r) for r in con.execute(sa.select(tables["inflation_migration_rows"]).where(
        tables["inflation_migration_rows"].c.run_id == run)).mappings()]
    by_id = {r["legacy_id"]: r for r in audit}
    errors = []
    if set(by_id) != {r["id"] for r in rows}:
        errors.append("observation_id_coverage")
    obs = tables["observations"]
    rel = tables["releases"]
    actual = {r["id"]: dict(r) for r in con.execute(sa.select(obs, rel.c.source_doc).join(rel)).mappings()}
    links = {r["legacy_id"]: dict(r) for r in con.execute(sa.select(tables["legacy_observation_links"]).where(
        tables["legacy_observation_links"].c.legacy_database == snapshot)).mappings()}
    expected_links = {r["legacy_id"] for r in audit if r["target_id"] is not None}
    if set(links) != expected_links:
        errors.append("legacy_link_coverage")
    if set(actual) != {r["target_id"] for r in audit if r["target_id"] is not None}:
        errors.append("unexplained_canonical_observations")
    persisted_series = {}
    series = tables["series"]
    dims = tables["dimensions"]
    query = sa.select(series.c.id, tables["concepts"].c.code.label("concept"),
        tables["geographies"].c.code.label("geography"), tables["units"].c.code.label("unit"), series.c.frequency,
        *[dims.c[k] for k in ("category", "sector", "aggregation_level", "price_basis", "seasonal_adjustment", "source_variant")])
    for r in con.execute(query.select_from(series.join(tables["concepts"]).join(tables["geographies"])
                         .join(tables["units"]).join(dims))).mappings():
        persisted_series[r["id"]] = dict(r)
    # Migration-level DATA_SPEC checks use the complete identity, not the coarse
    # legacy indicator key. These checks do not authorize publication (S0.4).
    quality_warnings = []
    document_counts = Counter(r["source_doc"] for r in actual.values())
    quality_warnings.extend(f"fewer_than_5_observations:{doc}:{count}"
                            for doc, count in sorted(document_counts.items()) if count < 5)
    values_by_series = defaultdict(list)
    identities = set()
    for target in actual.values():
        identity = (target["series_id"], target["period"], target["release_id"])
        if identity in identities:
            errors.append(f"duplicate_canonical_identity:{target['id']}")
        identities.add(identity)
        series_identity = persisted_series[target["series_id"]]
        if period_frequency(target["period"]) != series_identity["frequency"]:
            errors.append(f"invalid_canonical_period:{target['id']}")
        if series_identity["unit"] not in {"percent", "index"}:
            errors.append(f"invalid_canonical_unit:{target['id']}")
        values_by_series[target["series_id"]].append(target)
    for series_id, group in sorted(values_by_series.items()):
        values = [float(r["value"]) for r in group]
        deviation = pstdev(values)
        if deviation:
            mean = fmean(values)
            quality_warnings.extend(f"outlier_z_gt_4:observation:{r['id']}:series:{series_id}"
                                    for r, value in zip(group, values) if abs((value - mean) / deviation) > 4)
    samples = []
    for original in rows:
        record = by_id.get(original["id"])
        if not record or record["original_row"] != original or record["source_doc"] != original["source_doc"] or record["source_page"] != original["source_page"]:
            errors.append(f"raw_row_or_citation:{original['id']}")
            continue
        if record["target_id"] is None:
            if record["disposition"] != "quarantined" or not record["reason"]:
                errors.append(f"unexplained_quarantine:{original['id']}")
            continue
        target = actual.get(record["target_id"])
        link = links.get(original["id"])
        if not target or not link or link["original_row"] != original or link["observation_id"] != record["target_id"]:
            errors.append(f"missing_canonical_link:{original['id']}")
            continue
        if (Decimal(str(original["value"])) != target["value"] or original["period"] != target["period"] or
            original["source_doc"] != target["source_doc"] or target["status"] != "draft"):
            errors.append(f"canonical_value_or_period:{original['id']}")
        mapping, _ = map_row(original, PROFILES[original["source_doc"]])
        expected = dict(concept=mapping.concept, geography=mapping.geography, unit=mapping.unit,
                        frequency=mapping.frequency, **mapping.dimensions()) if mapping else {}
        if not expected or any(persisted_series[target["series_id"]][key] != value for key, value in expected.items()):
            errors.append(f"canonical_identity:{original['id']}")
        if record["disposition"] == "mapped" and (target["source_page"] != original["source_page"] or target["original_label"] != original["indicator_label"]):
            errors.append(f"canonical_citation:{original['id']}")
        if len(samples) < 10 and record["disposition"] == "mapped":
            samples.append(dict(legacy_id=original["id"], concept=mapping.concept, category=mapping.category,
                geography=mapping.geography, period=original["period"], value=str(target["value"].normalize()),
                source_doc=original["source_doc"], source_page=original["source_page"]))
    passage_audit = {r["legacy_id"]: dict(r) for r in con.execute(sa.select(tables["inflation_migration_passages"]).where(
        tables["inflation_migration_passages"].c.run_id == run)).mappings()}
    persisted_passages = {r["id"]: dict(r) for r in con.execute(sa.select(tables["passages"], rel.c.source_doc).join(rel)).mappings()}
    if set(passage_audit) != {r["id"] for r in passages}:
        errors.append("passage_id_coverage")
    if set(persisted_passages) != {r["target_id"] for r in passage_audit.values() if r["target_id"] is not None}:
        errors.append("unexplained_canonical_passages")
    for original in passages:
        record = passage_audit.get(original["id"])
        if not record or record["original_row"] != original or record["source_doc"] != original["source_doc"] or record["source_page"] != original["page"]:
            errors.append(f"raw_passage:{original['id']}")
        elif record["target_id"]:
            target = persisted_passages.get(record["target_id"])
            if not target or (target["text"], target["source_page"], target["source_doc"]) != (original["text"], original["page"], original["source_doc"]):
                errors.append(f"canonical_passage:{original['id']}")
        elif record["disposition"] != "quarantined" or not record["reason"]:
            errors.append(f"unexplained_passage_quarantine:{original['id']}")
    by_document = {}
    for doc in sorted({r["source_doc"] for r in rows}):
        group = [r for r in audit if r["source_doc"] == doc]
        by_document[doc] = dict(input_rows=len(group), **Counter(r["disposition"] for r in group))
    return dict(mapping_version=MAPPING_VERSION, legacy_database=snapshot,
        result="PASS" if not errors else "FAIL", errors=errors,
        quality_warnings=quality_warnings,
        input_rows=len(rows), legacy_eligible_rows=sum((r.get("confidence") or 0) >= 0.5 for r in rows),
        dispositions=dict(Counter(r["disposition"] for r in audit)),
        canonical_observations=len(actual), canonical_series=len(persisted_series), input_passages=len(passages),
        passage_dispositions=dict(Counter(r["disposition"] for r in passage_audit.values())),
        quarantine_reasons=dict(sorted(Counter(r["reason"] for r in audit if r["disposition"] == "quarantined").items())),
        by_document=by_document, sample_rows=samples,
        rows=[dict(legacy_id=r["legacy_id"], target_id=r["target_id"], disposition=r["disposition"], reason=r["reason"],
                   source_doc=r["source_doc"], source_page=r["source_page"],
                   indicator=r["original_row"]["indicator"], period=r["original_row"]["period"],
                   value=r["original_row"]["value"], original_label=r["original_row"]["indicator_label"])
              for r in sorted(audit, key=lambda r: r["legacy_id"])])


def migrate(legacy: Path, target: Path, raw: Path) -> dict:
    """Apply once to an empty versioned store; an exact replay only reconciles it."""
    if legacy.resolve() == target.resolve() or (target.exists() and legacy.samefile(target)):
        raise ValueError("Trust Core target must differ from the legacy database")
    if not target.is_file():
        raise ValueError("Create a separate Trust Core database with alembic upgrade head first")
    snapshot, rows, passages = read_snapshot(legacy)
    if not rows:
        raise ValueError("No legacy inflation rows found; refusing an empty migration")
    manifest = source_manifest(raw, rows)
    archive = target.parent / "trust_core_sources"
    engine = create_database_engine(sa.URL.create("sqlite", database=str(target.resolve())))
    try:
        with engine.begin() as con:
            if con.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one() != SCHEMA_VERSION:
                raise ValueError("Upgrade the Trust Core schema to the S0.3 revision first")
            tables = sa.MetaData()
            tables.reflect(con)
            tables = tables.tables
            # Acquire the SQLite write reservation before deciding the target is empty.
            con.execute(tables["inflation_migration_runs"].update().where(sa.false()).values(id=0))
            imports = con.execute(sa.select(tables["inflation_migration_runs"])).mappings().all()
            if imports:
                if len(imports) != 1 or imports[0]["legacy_database"] != snapshot or imports[0]["mapping_version"] != MAPPING_VERSION or imports[0]["source_manifest"] != manifest:
                    raise ValueError("Target contains a different import; use a separate database for changed inputs")
                run = imports[0]["id"]
            else:
                if any(con.execute(sa.select(sa.func.count()).select_from(table)).scalar_one() for name, table in tables.items() if name != "alembic_version"):
                    raise ValueError("Initial inflation import requires an empty Trust Core schema")
                run = _insert(con, tables["inflation_migration_runs"], legacy_database=snapshot,
                              mapping_version=MAPPING_VERSION, source_manifest=manifest)
                releases = _create_releases(con, tables, raw, archive, manifest)
                _copy_rows(con, tables, run, snapshot, rows, manifest, releases)
                _copy_passages(con, tables, run, snapshot, passages, manifest, releases)
            for artifact in manifest.values():
                if artifact["reason"] == "verified" and checksum(archive / f"{artifact['sha256']}.pdf") != artifact["sha256"]:
                    raise ValueError("Archived source checksum mismatch")
            report = reconcile(con, tables, run, snapshot, rows, passages)
            report["source_manifest"] = manifest
            if report["errors"]:
                raise ValueError("Reconciliation failed: " + ", ".join(report["errors"][:10]))
            if checksum(legacy) != snapshot.removeprefix("sha256:"):
                raise ValueError("Legacy source changed during migration; target transaction rolled back")
            return report
    finally:
        engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy", type=Path, default=Path("data/processed/sika.db"))
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--raw", type=Path, default=Path("data/raw"))
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.report.resolve() in {args.legacy.resolve(), args.target.resolve()} or args.report.suffix.lower() != ".json":
            raise ValueError("Report must be a separate .json file")
        report = migrate(args.legacy, args.target, args.raw)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (ValueError, OSError, sa.exc.SQLAlchemyError, sqlite3.Error) as exc:
        print(f"FAIL: {exc}")
        return 1
    print(f"PASS: {report['input_rows']} legacy rows accounted for; {report['canonical_observations']} canonical observations; "
          f"{report['dispositions'].get('quarantined', 0)} quarantined; {report['input_passages']} passages preserved")
    print("No observations published. Reconciliation report: " + str(args.report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
