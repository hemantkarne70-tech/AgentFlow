# AgentFlow Milestone 29 — Bulk Workflow Operations

Adds `POST /workflows/runs/bulk-cancel` so operators can preview or cancel up to
100 workflow runs in one request.

## Request example

```json
{
  "run_ids": [12, 13, 14],
  "dry_run": true
}
```

Set `dry_run` to `false` or omit it to perform the operation. Queued runs are
marked cancelled immediately; running runs receive a cooperative cancellation
request. Terminal, missing, or otherwise non-cancellable runs are reported
individually without stopping the other items. Duplicate IDs and non-positive
IDs are rejected.

## Apply and test

1. Back up your current `app/main.py`.
2. Copy `app/main.py` from this bundle into the project.
3. Copy `tests/test_bulk_workflow_operations.py` into your project's `tests/`.
4. Run:

```bat
python -m pytest -q tests/test_bulk_workflow_operations.py
python -m pytest -q
```

The endpoint uses the existing database and audit trail; no migration is needed.
Cancellation of a running workflow is cooperative: the current step may finish
before the worker observes the cancellation request.
