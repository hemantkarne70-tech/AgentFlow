# AgentFlow Milestone 23 — SQLite Backup

A small backup utility for AgentFlow's file-backed SQLite database.

```bat
python -m pytest -q tests/test_sqlite_backup.py
python scripts/backup_sqlite.py --output-dir backups --keep 7
```

See `INTEGRATION_GUIDE.md` for setup, verification, Docker usage, and retention details.
