import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from scripts.backup_sqlite import backup_database, sqlite_path_from_url, verify_sqlite


def make_database(path: Path) -> str:
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE IF NOT EXISTS jobs (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
        connection.exec_driver_sql("INSERT INTO jobs (name) VALUES ('workflow-one')")
    engine.dispose()
    return f"sqlite:///{path.as_posix()}"


def test_backup_contains_database_data_and_passes_integrity_check(tmp_path):
    source = tmp_path / "agentflow.db"
    database_url = make_database(source)

    backup = backup_database(database_url, tmp_path / "backups")

    assert backup.is_file()
    verify_sqlite(backup)
    connection = sqlite3.connect(backup)
    try:
        row = connection.execute("SELECT name FROM jobs").fetchone()
    finally:
        connection.close()
    assert row == ("workflow-one",)


def test_backup_missing_database_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        backup_database(f"sqlite:///{(tmp_path / 'missing.db').as_posix()}", tmp_path / "backups")


def test_non_sqlite_url_is_rejected():
    with pytest.raises(ValueError, match="Only SQLite"):
        sqlite_path_from_url("postgresql://user:pass@localhost/db")


def test_keep_retains_newest_backups(tmp_path):
    source = tmp_path / "agentflow.db"
    database_url = make_database(source)
    output_dir = tmp_path / "backups"

    # Distinct timestamps ensure unique filenames across repeated calls.
    first = backup_database(database_url, output_dir, keep=2)
    first.touch()
    second = backup_database(database_url, output_dir, keep=2)
    second.touch()
    third = backup_database(database_url, output_dir, keep=2)

    files = list(output_dir.glob("agentflow-*.sqlite3"))
    assert len(files) <= 2
    assert third.exists()


def test_negative_keep_is_rejected(tmp_path):
    source = tmp_path / "agentflow.db"
    database_url = make_database(source)
    with pytest.raises(ValueError, match="keep"):
        backup_database(database_url, tmp_path / "backups", keep=-1)
