from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


REQUIRED_TABLES = {"entities", "state"}


def validate_database(path: Path) -> list[str]:
    if not path.is_file():
        raise ValueError(f"database does not exist: {path}")
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    finally:
        connection.close()
    tables = sorted(row[0] for row in rows)
    missing = sorted(REQUIRED_TABLES.difference(tables))
    if missing:
        raise ValueError(f"database is missing tables: {', '.join(missing)}")
    return tables


def backup_database(source: Path, output: Path) -> None:
    tables = validate_database(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise ValueError(f"backup target already exists: {output}")
    source_connection = sqlite3.connect(source)
    target_connection = sqlite3.connect(output)
    try:
        source_connection.backup(target_connection)
        target_connection.commit()
    finally:
        target_connection.close()
        source_connection.close()
    print(
        json.dumps(
            {
                "status": "ok",
                "operation": "backup",
                "source": str(source.resolve()),
                "output": str(output.resolve()),
                "tables": tables,
            },
            ensure_ascii=False,
        )
    )


def restore_database(source: Path, target: Path, force: bool) -> None:
    tables = validate_database(source)
    if target.exists() and not force:
        raise ValueError("target exists; pass --force after stopping the application")
    target.parent.mkdir(parents=True, exist_ok=True)
    source_connection = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    target_connection = sqlite3.connect(target)
    try:
        source_connection.backup(target_connection)
        target_connection.commit()
    finally:
        target_connection.close()
        source_connection.close()
    print(
        json.dumps(
            {
                "status": "ok",
                "operation": "restore",
                "source": str(source.resolve()),
                "target": str(target.resolve()),
                "tables": tables,
            },
            ensure_ascii=False,
        )
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SmartWMS SQLite backup and restore")
    subparsers = parser.add_subparsers(dest="command", required=True)

    backup = subparsers.add_parser("backup", help="Create a consistent SQLite backup")
    backup.add_argument("--db", type=Path, required=True)
    backup.add_argument("--output", type=Path)

    restore = subparsers.add_parser("restore", help="Restore SQLite from a backup")
    restore.add_argument("--db", type=Path, required=True)
    restore.add_argument("--input", type=Path, required=True)
    restore.add_argument("--force", action="store_true")

    verify = subparsers.add_parser("verify", help="Validate a SQLite database")
    verify.add_argument("--db", type=Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "backup":
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            output = args.output or Path("backups") / f"wms-{stamp}.sqlite"
            backup_database(args.db, output)
        elif args.command == "restore":
            restore_database(args.input, args.db, args.force)
        else:
            tables = validate_database(args.db)
            print(
                json.dumps(
                    {"status": "ok", "database": str(args.db.resolve()), "tables": tables}
                )
            )
    except Exception as error:
        print(json.dumps({"status": "error", "error": str(error)}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
