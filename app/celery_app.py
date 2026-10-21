from celery import Celery

from app.config import REDIS_URL

celery_app = Celery(
    "agentflow",
    broker=REDIS_URL,
    backend=REDIS_URL,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    enable_utc=True,
    timezone="UTC",
    task_track_started=True,
    task_ignore_result=True,
    broker_connection_retry_on_startup=True,
)