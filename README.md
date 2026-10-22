# AgentFlow Milestone 25 — Timeline Ordering Fix

This patch fixes the timeline test where `created` appeared after `started` and
`completed`. Synchronous workflow execution can finish before the `WorkflowRun`
row is flushed, meaning the database default for `run.created_at` may be later
than the execution timestamps.

## Apply
1. Back up your current `app/main.py`.
2. Copy this patch's `app/main.py` over your project file.
3. Run:
   `python -m pytest -q tests/test_operations_observability.py`
   `python -m pytest -q`

The timeline now uses lifecycle ordering for known event types, normalizes the
created event's effective timestamp to the earliest known start time, and keeps
elapsed values non-decreasing. No database migration is required.
