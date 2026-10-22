# Milestone 23 Windows file-lock fix

This patch fixes SQLite connection lifecycle handling in the backup utility and its test.

## Apply
1. Extract this ZIP into the AgentFlow project root, allowing the two files to replace their counterparts.
2. Run the targeted tests and then the full suite:

```bat
python -m pytest -q tests/test_sqlite_backup.py
python -m pytest -q
```

## Why
`sqlite3.Connection` used as a context manager does not automatically close the connection. On Windows, open database handles can prevent pytest from deleting its temporary test directory. The backup utility now explicitly closes both source and target connections, and the test explicitly closes the connection used to inspect the backup.

Do not push until both test commands pass.
