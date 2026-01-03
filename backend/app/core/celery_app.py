"""
Celery Application Configuration
"""
from celery import Celery
from app.config import get_settings

settings = get_settings()

celery_app = Celery(
    "voice_notion",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.worker.tasks"]
)

# Celery 配置
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Taipei",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,  # 確保任務執行完才移除
    worker_prefetch_multiplier=1,
)
