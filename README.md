# AgentFlow Milestone 28 — Workflow Management & Control Center

This bundle adds a consolidated dashboard API and expands workflow run search/filtering.

## Included
- `GET /workflows/dashboard?recent_limit=10`: run status counts, average duration, recurring schedule counts, recent runs, and recent failures.
- Extended `GET /workflows/runs` filters:
  - `status`: queued, running, completed, failed, cancelled
  - `search`: case-insensitive workflow-name search
  - `date_from` and `date_to`: inclusive ISO-8601 creation-time bounds
  - `outcome`: `success`, `failure`, `active`, or `cancelled`
  - Existing pagination remains supported.
- Automated tests for dashboard payload, filtering, date ranges, and validation.

Existing cancel, retry, schedule pause/resume, timeline, and run-detail endpoints are retained; this bundle does not replace those controls or change the database schema.

## Apply
1. Back up `app/main.py`.
2. Copy `app/main.py` and `tests/test_workflow_dashboard.py` from this ZIP into the matching locations in your AgentFlow project.
3. Run:

```bat
python -m pytest -q tests/test_workflow_dashboard.py
python -m pytest -q
```

4. If tests pass, commit and push:

```bat
git add app/main.py tests/test_workflow_dashboard.py
git commit -m "Add workflow management dashboard"
git push
```

## Example requests
- `GET /workflows/dashboard`
- `GET /workflows/runs?status=failed&page=1&per_page=20`
- `GET /workflows/runs?search=invoice&outcome=success`
- `GET /workflows/runs?date_from=2026-10-01T00:00:00Z&date_to=2026-10-31T23:59:59Z`
- `GET /workflows/runs?outcome=active`

Date bounds are inclusive and refer to workflow creation time, not completion time.
