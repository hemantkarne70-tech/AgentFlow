"""Create and verify a consistent SQLite backup for AgentFlow.

Examples:
    python scripts/backup_sqlite.py --output-dir backups
    python scripts/backup_sqlite.py --database-url sqlite:///./agentflow.db --keep 7

Only SQLite DATABASE_URL values are supported by this utility. For other database
engines, use the database vendor's native backup mechanism.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy.engine import make_url


def sqlite_path_from_url(database_url: str) -> Path:
    """Resolve a SQLite SQLAlchemy URL to its on-disk database path."""
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite":
        raise ValueError("Only SQLite DATABASE_URL values are supported")
    database = url.database
    if not database or database == ":memory:":
        raise ValueError("A file-backed SQLite database is required for backups")
    path = Path(database).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve()


def verify_sqlite(path: Path) -> None:
    """Raise RuntimeError unless SQLite reports a healthy database."""
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        result = connection.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise RuntimeError(f"SQLite integrity check failed: {result[0] if result else 'no result'}")
    finally:
        connection.close()


def prune_backups(output_dir: Path, keep: int) -> None:
    """Keep the newest `keep` backup files; keep=0 disables pruning."""
    if keep < 0:
        raise ValueError("keep must be zero or greater")
    if keep == 0:
        return
    backups = sorted(
        output_dir.glob("agentflow-*.sqlite3"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for old_backup in backups[keep:]:
        old_backup.unlink(missing_ok=True)


def backup_database(database_url: str, output_dir: str | Path = "backups", keep: int = 7) -> Path:
    """Make a consistent backup using SQLite's online backup API and verify it."""
    source_path = sqlite_path_from_url(database_url)
    if not source_path.is_file():
        raise FileNotFoundError(f"SQLite database not found: {source_path}")
    if keep < 0:
        raise ValueError("keep must be zero or greater")

    destination_dir = Path(output_dir).expanduser().resolve()
    destination_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = destination_dir / f"agentflow-{timestamp}.sqlite3"

    fd, temporary_name = tempfile.mkstemp(prefix=".agentflow-backup-", suffix=".tmp", dir=destination_dir)
    os.close(fd)
    temporary_path = Path(temporary_name)
    try:
        # sqlite3.Connection's context manager commits/rolls back but does not
        # close the underlying file handle. Explicitly close both connections;
        # this is especially important on Windows, where open handles prevent
        # temporary directories and backup files from being removed.
        source = sqlite3.connect(source_path)
        target = sqlite3.connect(temporary_path)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        verify_sqlite(temporary_path)
        # Atomic rename means consumers never see a partially written final backup.
        temporary_path.replace(destination)
        prune_backups(destination_dir, keep)
        return destination
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Create and verify an AgentFlow SQLite backup")
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL", "sqlite:///./agentflow.db"),
        help="SQLAlchemy SQLite URL (defaults to DATABASE_URL or sqlite:///./agentflow.db)",
    )
    parser.add_argument("--output-dir", default="backups", help="Directory to store backup files")
    parser.add_argument("--keep", type=int, default=7, help="Number of newest backups to retain; 0 disables pruning")
    args = parser.parse_args(argv)
    try:
        path = backup_database(args.database_url, args.output_dir, args.keep)
    except (ValueError, FileNotFoundError, RuntimeError, sqlite3.Error) as exc:
        print(f"Backup failed: {exc}", file=sys.stderr)
        return 1
    print(f"Backup created and verified: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
