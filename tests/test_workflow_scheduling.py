from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.main import ScheduledWorkflowRequest


def payload(scheduled_at):
    return {
        "name": "scheduled-test",
        "steps": [{"tool": "uppercase", "input": "hello"}],
        "scheduled_at": scheduled_at,
    }


def test_schedule_accepts_future_timezone_aware_datetime():
    scheduled = datetime.now(timezone.utc) + timedelta(hours=1)
    request = ScheduledWorkflowRequest(**payload(scheduled.isoformat()))
    assert request.scheduled_at.tzinfo is not None
    assert request.scheduled_at > datetime.now(timezone.utc)


def test_schedule_rejects_naive_datetime():
    scheduled = (datetime.now(timezone.utc) + timedelta(hours=1)).replace(tzinfo=None)
    with pytest.raises(ValidationError, match="must include a timezone"):
        ScheduledWorkflowRequest(**payload(scheduled.isoformat()))


def test_schedule_rejects_past_datetime():
    scheduled = datetime.now(timezone.utc) - timedelta(minutes=1)
    with pytest.raises(ValidationError, match="must be in the future"):
        ScheduledWorkflowRequest(**payload(scheduled.isoformat()))


def test_schedule_rejects_more_than_seven_days_ahead():
    scheduled = datetime.now(timezone.utc) + timedelta(days=8)
    with pytest.raises(ValidationError, match="cannot be more than 7 days"):
        ScheduledWorkflowRequest(**payload(scheduled.isoformat()))
