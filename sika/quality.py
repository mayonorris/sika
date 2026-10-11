"""Independent, versioned technical checks for the Trust Core inflation pilot.

A passed quality run is evidence about a snapshot, never publication approval.
"""

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
from statistics import fmean, pstdev
from urllib.parse import urlparse
from urllib.request import url2pathname

import pdfplumber
import sqlalchemy as sa

from sika.database import _allow_writes, create_database_engine
from sika.inflation_mapping import CATEGORIES, CONCEPT_LABELS, GEOGRAPHIES, MAPPING_VERSION, PROFILES, map_row
from sika.migrate_inflation import checksum, read_snapshot

VALIDATOR_VERSION = "trust-core-inflation-1.1"
SCHEMA_VERSION = "0003_publication_gate"
DIMENSIONS = ("category", "sector", "aggregation_level", "price_basis", "seasonal_adjustment", "source_variant")
SNAPSHOT_TABLES = ("concepts", "geographies", "units", "dimensions", "series", "sources", "releases",
                   "observations", "passages", "legacy_observation_links", "inflation_migration_runs",
                   "inflation_migration_rows", "inflation_migration_passages")
CATEGORY_LEVELS = {code: level for code, level, _ in CATEGORIES}
GEOGRAPHY_CODES = {code for code, _ in GEOGRAPHIES.values()}


@dataclass(frozen=True)
class Finding:
    severity: str
    code: str
    message: str
    release_id: int | None = None
    observation_id: int | None = None
    legacy_id: int | None = None


def valid_period(period: str, frequency: str) -> bool:
    """Require normalized calendar periods; irregular mappings are not reviewed yet."""
    if not isinstance(period, str) or not re.match(r"^[0-9]{4}", period) or period[:4] == "0000":
        return False
    patterns = {"annual": r"[0-9]{4}", "quarterly": r"[0-9]{4}-Q[1-4]",
                "monthly": r"[0-9]{4}-(?:0[1-9]|1[0-2])", "daily": r"[0-9]{4}-[0-9]{2}-[0-9]{2}"}
    if frequency not in patterns or not re.fullmatch(patterns[frequency], period):
        return False
    if frequency == "daily":
        try:
            date.fromisoformat(period)
        except ValueError:
            return False
    return True


def numeric(value: object) -> Decimal | None:
    """Check representable finite values without silently rounding the input."""
    try:
        number = Decimal(str(value))
        if number.is_finite() and abs(number) < Decimal("1e18") and number == number.quantize(Decimal("1e-10")):
            return number
    except (InvalidOperation, ValueError):
        pass
    return None


def _read_tables(con: sa.Connection, tables: dict) -> dict[str, list[dict]]:
    snapshot = {}
    for name in SNAPSHOT_TABLES:
        table = tables[name]
        # Read numeric storage as text so malformed SQLite values become findings,
        # rather than failing inside SQLAlchemy's decimal result processor.
        columns = [sa.cast(c, sa.String).label(c.name) if isinstance(c.type, sa.Numeric) else c for c in table.c]
        rows = [dict(r) for r in con.execute(sa.select(*columns).order_by(*table.primary_key.columns)).mappings()]
        snapshot[name] = rows
    return snapshot


def _artifact(release: dict, root: Path) -> dict:
    """Inspect only local PDF artifacts inside the explicitly selected archive."""
    uri = urlparse(release["storage_uri"])
    if uri.scheme != "file" or uri.netloc not in {"", "localhost"} or uri.query or uri.fragment:
        return {"error": "unsupported_storage_uri"}
    path = Path(url2pathname(uri.path)).resolve()
    if not path.is_relative_to(root.resolve()):
        return {"error": "source_outside_archive"}
    if not path.is_file():
        return {"error": "source_file_missing"}
    try:
        digest = checksum(path)
        if release["media_type"] != "application/pdf":
            return {"sha256": digest, "error": "unsupported_media_type"}
        with pdfplumber.open(path) as pdf:
            page_count = len(pdf.pages)
        return {"sha256": digest, "page_count": page_count}
    except Exception:
        # Parser errors may include source content; persist a bounded code only.
        return {"error": "source_unreadable"}


def _fingerprint(snapshot: dict, artifacts: dict, legacy: tuple | None) -> str:
    payload = dict(schema_version=SCHEMA_VERSION, validator_version=VALIDATOR_VERSION, tables=snapshot,
                   artifacts=artifacts, legacy_snapshot=legacy[0] if legacy else None)
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def examine(snapshot: dict, artifacts: dict, legacy: tuple | None = None) -> list[Finding]:
    """Validate persisted records independently of the importer's mapping decisions."""
    findings = []
    def add(severity, code, message, release=None, observation=None, legacy_id=None):
        findings.append(Finding(severity, code, message, release, observation, legacy_id))

    catalogs = {name: {r["id"]: r for r in snapshot[name]} for name in
                ("concepts", "geographies", "units", "dimensions", "series", "sources", "releases", "observations")}
    releases = catalogs["releases"]
    series = catalogs["series"]
    observations = catalogs["observations"]
    for name, rows in snapshot.items():
        counts = Counter(r["id"] for r in rows if "id" in r)
        if any(count > 1 for count in counts.values()):
            add("FAIL", "duplicate_record_id", f"Duplicate IDs in {name}")

    complete_identities = {}
    for row in snapshot["series"]:
        refs = {name: catalogs[name].get(row[field]) for name, field in
                (("concepts", "concept_id"), ("geographies", "geography_id"), ("units", "unit_id"), ("dimensions", "dimensions_id"))}
        if not all(refs.values()):
            add("FAIL", "series_reference_missing", f"Series {row['id']} has a missing catalog reference")
            continue
        concept, geography, unit, dims = (refs[name] for name in ("concepts", "geographies", "units", "dimensions"))
        identity = (concept["code"], geography["code"], unit["code"], row["frequency"], *[dims[k] for k in DIMENSIONS])
        complete_identities[row["id"]] = identity
        if not row["series_key"].strip() or any(not isinstance(v, str) or not v.strip() for v in identity):
            add("FAIL", "series_identity_incomplete", f"Series {row['id']} has incomplete identity")
        if concept["code"] not in CONCEPT_LABELS or geography["code"] not in GEOGRAPHY_CODES:
            add("FAIL", "catalog_code_not_reviewed", f"Series {row['id']} uses an unreviewed concept/geography")
        expected_unit = "index" if concept["code"] == "cpi_index" else "percent"
        if (unit["code"] != expected_unit or numeric(unit["scale"]) != 1 or
            unit["symbol"] != ("index" if expected_unit == "index" else "%") or
            unit["quantity"] not in ({"index"} if expected_unit == "index" else {"percent", "rate"})):
            add("FAIL", "unit_concept_mismatch", f"Series {row['id']} has incompatible unit metadata")
        if CATEGORY_LEVELS.get(dims["category"]) != dims["aggregation_level"] or dims["sector"] != "all_sectors":
            add("FAIL", "dimension_not_reviewed", f"Series {row['id']} has unreviewed category/aggregation/sector")
        if dims["seasonal_adjustment"] != "not_reported":
            add("FAIL", "adjustment_not_reviewed", f"Series {row['id']} has an unreviewed adjustment")
        if expected_unit == "percent" and dims["price_basis"] != "not_applicable":
            add("FAIL", "price_basis_mismatch", f"Series {row['id']} assigns an index base to a rate")
        if concept["code"] in {"inflation_rate_3m", "inflation_rate_12m_average"} and row["frequency"] != "monthly":
            add("FAIL", "measure_frequency_mismatch", f"Series {row['id']} has an incompatible measurement window")

    if any(count > 1 for count in Counter(complete_identities.values()).values()):
        add("FAIL", "duplicate_series_identity", "Several series share the same complete statistical identity")
    if any(count > 1 for count in Counter(r["series_key"] for r in snapshot["series"]).values()):
        add("FAIL", "duplicate_series_key", "Several series share the same stable key")

    duplicate_keys = defaultdict(list)
    by_release = defaultdict(list)
    for row in snapshot["observations"]:
        release_id = row["release_id"]
        by_release[release_id].append(row)
        if release_id not in releases or row["series_id"] not in series:
            add("FAIL", "observation_reference_missing", "Observation has a missing release or series", None, row["id"])
            continue
        s = series[row["series_id"]]
        if not valid_period(row["period"], s["frequency"]):
            add("FAIL", "invalid_period", "Period does not match the normalized calendar/frequency", release_id, row["id"])
        if numeric(row["value"]) is None:
            add("FAIL", "invalid_value", "Value is not finite or representable without rounding", release_id, row["id"])
        page_count = artifacts.get(release_id, {}).get("page_count")
        if (not isinstance(row["source_page"], int) or row["source_page"] < 1 or
            (page_count is not None and row["source_page"] > page_count) or not row["original_label"].strip()):
            add("FAIL", "invalid_observation_provenance", "Invalid physical page or missing original label", release_id, row["id"])
        confidence = numeric(row["confidence"]) if row["confidence"] is not None else None
        if row["confidence"] is not None and (confidence is None or not 0 <= confidence <= 1):
            add("FAIL", "invalid_confidence", "Extraction confidence is outside [0,1]", release_id, row["id"])
        identity = complete_identities.get(row["series_id"])
        if identity:
            duplicate_keys[(identity, row["period"], release_id)].append(row["id"])
            profile = PROFILES.get(releases[release_id]["source_doc"])
            dims = catalogs["dimensions"][s["dimensions_id"]]
            concept = catalogs["concepts"][s["concept_id"]]["code"]
            if profile:
                expected_basis = f"index_{profile['basis']}" if concept == "cpi_index" else "not_applicable"
                expected_frequency = "quarterly" if profile["variant"] == "bceao:regional_quarterly_inflation" else "monthly"
                if (dims["source_variant"] != profile["variant"] or dims["price_basis"] != expected_basis or
                    s["frequency"] != expected_frequency):
                    add("FAIL", "source_methodology_mismatch", "Series identity conflicts with the reviewed publication", release_id, row["id"])
        if row["revision_status"] not in {"unknown", "provisional", "revised", "final"}:
            add("FAIL", "invalid_revision_state", "Invalid revision state", release_id, row["id"])
        if row["status"] not in {"draft", "reviewed", "published", "quarantined", "corrected", "superseded"}:
            add("FAIL", "invalid_observation_state", "Invalid observation state", release_id, row["id"])
    for (_, _, release_id), ids in duplicate_keys.items():
        if len(ids) > 1:
            add("FAIL", "duplicate_observation", f"Repeated complete identity/period/release: {sorted(ids)}", release_id)

    # Identical repetitions across vintages do not increase a sample's weight.
    # Report extreme values within complete series; never alter or reject a value
    # solely because it is statistically unusual.
    samples = defaultdict(lambda: defaultdict(list))
    for row in snapshot["observations"]:
        value = numeric(row["value"])
        if value is not None and row["series_id"] in complete_identities:
            samples[complete_identities[row["series_id"]]][(row["period"], value)].append(row)
    for group in samples.values():
        values = [float(value) for _, value in group]
        deviation = pstdev(values)
        if deviation:
            mean = fmean(values)
            for (_, value), rows in group.items():
                if abs((float(value) - mean) / deviation) > 4:
                    for row in rows:
                        add("WARN", "value_outlier", "Absolute z-score exceeds 4 within the complete series", row["release_id"], row["id"])

    for release_id, release in releases.items():
        artifact = artifacts.get(release_id, {})
        profile = PROFILES.get(release["source_doc"])
        if not profile:
            add("FAIL", "source_not_reviewed", "Publication is outside the reviewed inflation source registry", release_id)
        if (Path(release["source_doc"]).name != release["source_doc"] or
            not re.fullmatch(r"[0-9a-f]{64}", release["content_sha256"])):
            add("FAIL", "invalid_release_provenance", "Invalid filename or checksum metadata", release_id)
        if artifact.get("error"):
            add("FAIL", artifact["error"], "Source artifact could not be verified", release_id)
        elif artifact.get("sha256") != release["content_sha256"]:
            add("FAIL", "source_checksum_mismatch", "Archived bytes differ from release provenance", release_id)
        if profile and (release["content_sha256"] != profile["sha256"] or
                        release["source_url"] != profile["url"] or release["publisher"] != profile["publisher"]):
            add("FAIL", "reviewed_source_mismatch", "Release differs from the reviewed source profile", release_id)
        source = catalogs["sources"].get(release["source_id"])
        if not source:
            add("FAIL", "source_reference_missing", "Release has no publisher registry record", release_id)
        elif source["reuse_status"] != "allowed":
            add("WARN", "reuse_review_required", f"Source reuse status: {source['reuse_status']}", release_id)
        if release["published_on"] is None:
            add("WARN", "publication_date_unknown", "Official publication date has not been verified", release_id)
        group = by_release[release_id]
        if not group:
            add("FAIL", "empty_release", "Release has no canonical observations", release_id)
        elif len(group) < 5:
            add("WARN", "small_release", f"Only {len(group)} canonical observations", release_id)
        unknown = sum(r["revision_status"] == "unknown" for r in group)
        if unknown:
            add("WARN", "revision_review_required", f"Revision marks unverified for {unknown} observations", release_id)

    for row in snapshot["passages"]:
        limit = artifacts.get(row["release_id"], {}).get("page_count")
        if (row["release_id"] not in releases or not isinstance(row["source_page"], int) or row["source_page"] < 1 or
            (limit is not None and row["source_page"] > limit)):
            add("FAIL", "invalid_passage_provenance", f"Passage {row['id']} has an invalid reference/page", row["release_id"] if row["release_id"] in releases else None)

    _check_migration_audit(snapshot, catalogs, legacy, add)
    return sorted(findings, key=lambda f: (f.release_id or 0, f.code, f.observation_id or 0, f.legacy_id or 0, f.severity))


def _check_migration_audit(snapshot: dict, catalogs: dict, legacy: tuple | None, add) -> None:
    runs = snapshot["inflation_migration_runs"]
    if not runs:
        if (snapshot["inflation_migration_rows"] or snapshot["inflation_migration_passages"] or
            snapshot["legacy_observation_links"] or any(r.get("legacy_database") is not None for r in snapshot["passages"]) or
            legacy is not None):
            add("FAIL", "orphan_migration_audit", "Migration audit has no run")
        return
    if len(runs) != 1 or legacy is None or runs[0]["legacy_database"] != legacy[0]:
        add("FAIL", "legacy_snapshot_unverified", "Provide the exact read-only legacy snapshot to verify migration completeness")
        return
    run = runs[0]
    if run["mapping_version"] != MAPPING_VERSION:
        add("FAIL", "migration_mapping_version", "Migration uses an unsupported mapping version")
    # Metadata-corrected vintages may reuse a filename/hash. Find the original
    # imported vintage by its stable key, not by filename alone.
    releases_by_doc = {r["source_doc"]: r["id"] for r in catalogs["releases"].values()
                      if r["release_key"] == f"{r['source_doc']}:{run['source_manifest'].get(r['source_doc'], {}).get('sha256')}"}
    links = {(r["legacy_database"], r["legacy_id"]): r for r in snapshot["legacy_observation_links"]}
    for table, originals, targets, page_field in (
        ("inflation_migration_rows", legacy[1], catalogs["observations"], "source_page"),
        ("inflation_migration_passages", legacy[2], {r["id"]: r for r in snapshot["passages"]}, "page"),
    ):
        audit = snapshot[table]
        by_id = {r["legacy_id"]: r for r in audit if r["run_id"] == run["id"]}
        if len(audit) != len(by_id) or set(by_id) != {r["id"] for r in originals}:
            add("FAIL", "migration_id_coverage", f"Incomplete or duplicate legacy ID coverage in {table}")
        for original in originals:
            row = by_id.get(original["id"])
            release_id = releases_by_doc.get(original["source_doc"])
            if not row or (row["original_row"] != original or row["source_doc"] != original["source_doc"] or
                           row["source_page"] != original[page_field] or not row["reason"].strip()):
                add("FAIL", "migration_original_mismatch", f"Original fields/citation/reason missing in {table}", release_id, legacy_id=original["id"])
                continue
            if row["disposition"] == "quarantined":
                if row["target_id"] is not None:
                    add("FAIL", "quarantine_has_target", "Quarantined original has a canonical target", release_id, legacy_id=original["id"])
                continue
            target = targets.get(row["target_id"])
            if row["disposition"] not in {"mapped", "merged"} or not target or target["release_id"] != release_id:
                add("FAIL", "migration_target_mismatch", "Original has an invalid canonical target", release_id, legacy_id=original["id"])
                continue
            if table == "inflation_migration_rows":
                link = links.get((legacy[0], original["id"]))
                if (numeric(target["value"]) != numeric(original["value"]) or target["period"] != original["period"] or
                    not link or link["original_row"] != original or link["observation_id"] != target["id"]):
                    add("FAIL", "legacy_value_or_link_mismatch", "Value/period/full original link differs from legacy", release_id, target["id"], original["id"])
                mapping, _ = map_row(original, PROFILES[original["source_doc"]])
                series = catalogs["series"].get(target["series_id"])
                if mapping and series and all(series[k] in catalogs[t] for k, t in
                        (("concept_id", "concepts"), ("geography_id", "geographies"), ("unit_id", "units"), ("dimensions_id", "dimensions"))):
                    actual = dict(concept=catalogs["concepts"][series["concept_id"]]["code"],
                                  geography=catalogs["geographies"][series["geography_id"]]["code"],
                                  unit=catalogs["units"][series["unit_id"]]["code"], frequency=series["frequency"],
                                  **{k: catalogs["dimensions"][series["dimensions_id"]][k] for k in DIMENSIONS})
                    expected = dict(concept=mapping.concept, geography=mapping.geography,
                                    unit=mapping.unit, frequency=mapping.frequency, **mapping.dimensions())
                    if actual != expected:
                        add("FAIL", "legacy_identity_mismatch", "Canonical identity differs from the versioned original mapping", release_id, target["id"], original["id"])
                else:
                    add("FAIL", "legacy_identity_mismatch", "Canonical target has no reviewed original identity", release_id, target["id"], original["id"])
                if row["disposition"] == "mapped" and (target["source_page"] != original["source_page"] or target["original_label"] != original["indicator_label"]):
                    add("FAIL", "legacy_primary_citation_mismatch", "Primary citation differs from its original", release_id, target["id"], original["id"])
            elif (target["text"], target["source_page"]) != (original["text"], original["page"]):
                add("FAIL", "legacy_passage_mismatch", "Canonical passage differs from its original", release_id, legacy_id=original["id"])
        groups = defaultdict(list)
        for row in audit:
            if row["target_id"] is not None:
                groups[row["target_id"]].append(row)
        for target_id, group in groups.items():
            if sum(r["disposition"] == "mapped" for r in group) != 1:
                add("FAIL", "unexplained_merge", f"Target {target_id} does not have one primary mapping in {table}")
        if {r["id"] for r in targets.values() if r["release_id"] in releases_by_doc.values()} != set(groups):
            add("FAIL", "unexplained_canonical_target", f"Unexplained canonical targets in {table}")
        if table == "inflation_migration_rows":
            expected_links = {(legacy[0], r["legacy_id"]) for r in audit if r["target_id"] is not None}
            if set(links) != expected_links:
                add("FAIL", "legacy_link_coverage", "Unexplained or missing original observation links")
    counts = Counter(r["source_doc"] for r in snapshot["inflation_migration_rows"] if r["disposition"] == "quarantined")
    for doc, count in sorted(counts.items()):
        add("WARN", "legacy_quarantine", f"{count} legacy originals await domain review", releases_by_doc.get(doc))


def run_quality_checks(target: Path, *, legacy: Path | None = None, source_root: Path | None = None,
                       release_ids: list[int] | None = None) -> dict:
    """Append completed per-release evidence in one transaction without changing data states."""
    target = target.resolve(strict=True)
    if legacy is not None and target.samefile(legacy):
        raise ValueError("Legacy snapshot must differ from the Trust Core target")
    source_root = source_root or target.parent / "trust_core_sources"
    engine = create_database_engine(sa.URL.create("sqlite", database=str(target)))
    started = datetime.now(timezone.utc)
    try:
        with engine.begin() as con:
            names = set(sa.inspect(con).get_table_names())
            if not set(SNAPSHOT_TABLES + ("quality_runs", "alembic_version")).issubset(names):
                raise ValueError("Target is not a versioned Trust Core database; never use the MVP database")
            if con.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one() != SCHEMA_VERSION:
                raise ValueError("Unsupported Trust Core schema revision")
            tables = sa.MetaData()
            tables.reflect(con)
            tables = tables.tables
            # Reserve the SQLite writer before reading the snapshot. A concurrent
            # writer cannot change the checked records before evidence is committed.
            con.execute(tables["quality_runs"].update().where(sa.false()).values(status="running"))
            snapshot = _read_tables(con, tables)
            releases = snapshot["releases"]
            selected = sorted(set(release_ids)) if release_ids is not None else [r["id"] for r in releases]
            if not selected or not set(selected).issubset(r["id"] for r in releases):
                raise ValueError("Choose one or more existing releases; empty validation is not a pass")
            legacy_snapshot = read_snapshot(legacy) if legacy is not None else None
            artifacts = {r["id"]: _artifact(r, source_root) for r in releases}
            findings = examine(snapshot, artifacts, legacy_snapshot)
            if artifacts != {r["id"]: _artifact(r, source_root) for r in releases}:
                findings.append(Finding("FAIL", "source_changed_during_validation", "Source bytes changed during validation"))
            if legacy_snapshot and checksum(legacy) != legacy_snapshot[0].removeprefix("sha256:"):
                findings.append(Finding("FAIL", "legacy_changed_during_validation", "Legacy snapshot changed during validation"))
            fingerprint = _fingerprint(snapshot, artifacts, legacy_snapshot)
            reports = []
            for release_id in selected:
                relevant = [f for f in findings if f.release_id in {None, release_id}]
                failures = sum(f.severity == "FAIL" for f in relevant)
                warnings = sum(f.severity == "WARN" for f in relevant)
                report = dict(validator_version=VALIDATOR_VERSION, schema_version=SCHEMA_VERSION,
                              snapshot_sha256=fingerprint, release_id=release_id,
                              source_doc=next(r["source_doc"] for r in releases if r["id"] == release_id),
                              observation_count=sum(r["release_id"] == release_id for r in snapshot["observations"]),
                              status="failed" if failures else "passed", hard_failure_count=failures,
                              warning_count=warnings, publication_authorized=False,
                              findings=[asdict(f) for f in relevant])
                run_id = _store_run(con, tables["quality_runs"], report, started)
                reports.append(report | {"quality_run_id": run_id})
            return dict(validator_version=VALIDATOR_VERSION, schema_version=SCHEMA_VERSION,
                        snapshot_sha256=fingerprint, publication_authorized=False, releases=reports,
                        status="failed" if any(r["hard_failure_count"] for r in reports) else "passed")
    finally:
        engine.dispose()


def _store_run(con: sa.Connection, table: sa.Table, report: dict, started: datetime) -> int:
    with _allow_writes(con, ("quality_runs", "insert", -1)):
        return con.execute(table.insert().values(release_id=report["release_id"], validator_version=VALIDATOR_VERSION,
            status=report["status"], started_at=started, completed_at=datetime.now(timezone.utc),
            hard_failure_count=report["hard_failure_count"], warning_count=report["warning_count"],
            report=report)).inserted_primary_key[0]
