"""Helpers for recording workflow lifecycle audit events."""
from datetime import datetime, timezone

from app.models import WorkflowAuditEvent


def record_audit_event(
    db,
    run_id: int,
    event_type: str,
    message: str,
    *,
    details: dict | None = None,
    created_at: datetime | None = None,
) -> WorkflowAuditEvent:
    """Add an audit event to the current transaction; caller commits it."""
    event = WorkflowAuditEvent(
        run_id=run_id,
        event_type=event_type,
        message=message,
        details=details or {},
        created_at=created_at or datetime.now(timezone.utc),
    )
    db.add(event)
    return event
