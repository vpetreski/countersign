"""Command line: `countersign seed | ingest | migrate | openapi`."""

from __future__ import annotations

import argparse
import json
import sys

from countersign.settings import get_settings
from countersign.store.db import Database


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="countersign")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate", help="create or upgrade the schema")
    sub.add_parser("ingest", help="load the sources (idempotent by checksum)")
    sub.add_parser("seed", help="migrate, then ingest: everything a fresh database needs")
    sub.add_parser("openapi", help="print the OpenAPI document")
    args = parser.parse_args(argv)
    settings = get_settings()
    if args.command == "openapi":
        from countersign.api.app import create_app  # noqa: PLC0415

        json.dump(create_app(settings).openapi(), sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0
    db = Database(settings.database_url)
    if args.command in ("migrate", "seed"):
        from countersign.store.migrate import upgrade  # noqa: PLC0415

        print(f"schema: {upgrade(db)}")
    if args.command in ("ingest", "seed"):
        from countersign.data.ingest import ingest  # noqa: PLC0415

        report = ingest(db, settings)
        print(
            f"ingest: changed={report.changed} resources={report.resources} cost_lines={report.cost_lines} "
            f"quarantined={report.quarantined} warnings={report.warnings}"
        )
        failed = [c.check_id for c in report.checks if c.status == "fail"]
        if failed:
            print("failing checks: " + ", ".join(failed), file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
