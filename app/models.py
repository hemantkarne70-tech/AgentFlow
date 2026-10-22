from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, JSON, String, Text
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
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    duration_seconds = Column(Float, nullable=True)
    cancel_requested = Column(Boolean, nullable=False, default=False, index=True)
    scheduled_for = Column(DateTime(timezone=True), nullable=True, index=True)
    celery_task_id = Column(String(100), nullable=True, index=True)


class WorkflowStepTrace(Base):
    """Persisted execution diagnostics for an individual workflow step."""

    __tablename__ = "workflow_step_traces"

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(Integer, ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    step_number = Column(Integer, nullable=False)
    tool = Column(String(50), nullable=False)
    status = Column(String(20), nullable=False, index=True)
    input_preview = Column(Text, nullable=True)
    output_preview = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=False)
    finished_at = Column(DateTime(timezone=True), nullable=False)
    duration_seconds = Column(Float, nullable=False, default=0.0)


class WorkflowTemplate(Base):
    """Reusable, named workflow definition."""

    __tablename__ = "workflow_templates"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False, unique=True, index=True)
    description = Column(Text, nullable=False, default="")
    steps = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)
