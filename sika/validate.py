"""Validate an explicit Trust Core target and append quality evidence. No API calls."""

import argparse
import json
from pathlib import Path
import sqlite3

import sqlalchemy as sa

from sika.quality import run_quality_checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--legacy", type=Path, help="Exact legacy snapshot, required for migration audit completeness")
    parser.add_argument("--source-root", type=Path, help="Local archive directory (default: target sibling trust_core_sources)")
    parser.add_argument("--release-id", type=int, action="append", help="Optional existing release; repeat to select several")
    parser.add_argument("--report", type=Path, help="Optional separate JSON report")
    args = parser.parse_args(argv)
    try:
        if args.report:
            protected = [p.resolve() for p in (args.target, args.legacy) if p is not None]
            if args.report.suffix.lower() != ".json" or args.report.resolve() in protected or (
                args.report.exists() and any(args.report.samefile(p) for p in protected if p.exists())):
                raise ValueError("Report must be a separate .json file, never a database")
        result = run_quality_checks(args.target, legacy=args.legacy, source_root=args.source_root, release_ids=args.release_id)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (ValueError, OSError, sa.exc.SQLAlchemyError, sqlite3.Error) as exc:
        print(f"ERROR: {exc}")
        return 2
    print(f"Trust Core quality: {result['status'].upper()} ({result['validator_version']})")
    print("Snapshot SHA-256: " + result["snapshot_sha256"])
    for release in result["releases"]:
        print(f"Release {release['release_id']} ({release['source_doc']}): {release['observation_count']} observations, "
              f"{release['hard_failure_count']} hard failures, {release['warning_count']} warnings; "
              f"quality_run={release['quality_run_id']}")
        for finding in release["findings"]:
            location = f" observation={finding['observation_id']}" if finding["observation_id"] else ""
            print(f"  [{finding['severity']}] {finding['code']}{location}: {finding['message']}")
    print("Technical validation only. Publication requires the subsequent review gate.")
    return 1 if result["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
