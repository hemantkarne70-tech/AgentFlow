
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Float, Integer, JSON, String, Text

from app.db import Base


def utc_now():
    return datetime.now(timezone.utc)


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False, index=True)
    status = Column(String(20), nullable=False, index=True)
    total_steps = Column(Integer, nullable=False, default=0)
    results = Column(JSON, nullable=False, default=list)
    error = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    duration_seconds = Column(Float, nullable=True)
