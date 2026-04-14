from celery import Celery

celery_app = Celery(
    "test_runner",
    broker="redis://127.0.0.1:6379/0",
    backend="redis://127.0.0.1:6379/1"
)

celery_app.autodiscover_tasks(["tasks"])