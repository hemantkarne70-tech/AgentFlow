from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.main import RecurringWorkflowScheduleRequest, _schedule_next_occurrence


def payload(**overrides):
    value = {
        "name": "daily-health-check",
        "steps": [{"tool": "uppercase", "input": "hello"}],
        "start_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        "recurrence": "daily",
        "timezone": "Asia/Kolkata",
    }
    value.update(overrides)
    return value


def test_daily_schedule_accepts_timezone_and_future_start():
    request = RecurringWorkflowScheduleRequest(**payload())
    assert request.recurrence == "daily"
    assert request.timezone == "Asia/Kolkata"
    assert request.start_at.tzinfo is not None


def test_interval_schedule_requires_interval_minutes():
    with pytest.raises(ValidationError, match="interval_minutes is required"):
        RecurringWorkflowScheduleRequest(**payload(recurrence="interval"))


def test_daily_schedule_rejects_interval_minutes():
    with pytest.raises(ValidationError, match="only valid for interval"):
        RecurringWorkflowScheduleRequest(**payload(interval_minutes=15))


def test_schedule_rejects_invalid_timezone():
    with pytest.raises(ValidationError, match="valid IANA timezone"):
        RecurringWorkflowScheduleRequest(**payload(timezone="Mars/Olympus"))


def test_schedule_rejects_naive_start_time():
    naive = (datetime.now(timezone.utc) + timedelta(hours=1)).replace(tzinfo=None)
    with pytest.raises(ValidationError, match="must include a timezone"):
        RecurringWorkflowScheduleRequest(**payload(start_at=naive.isoformat()))


def test_interval_next_occurrence_uses_minutes():
    due = datetime(2026, 10, 22, 10, 0, tzinfo=timezone.utc)
    result = _schedule_next_occurrence(due, "interval", "UTC", 15)
    assert result == due + timedelta(minutes=15)


def test_daily_next_occurrence_preserves_local_clock_time():
    due = datetime(2026, 10, 22, 10, 30, tzinfo=timezone.utc)
    result = _schedule_next_occurrence(due, "daily", "Asia/Kolkata", None)
    assert result.astimezone(__import__("zoneinfo").ZoneInfo("Asia/Kolkata")).hour == 16
    assert result.astimezone(__import__("zoneinfo").ZoneInfo("Asia/Kolkata")).minute == 0
    assert result.date() > due.date() or result > due
