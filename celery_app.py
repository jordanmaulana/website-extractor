"""Celery application configuration."""

from celery import Celery

app = Celery("website_extractor")

# Using Redis as broker and result backend
app.conf.broker_url = "redis://localhost:6379/0"
app.conf.result_backend = "redis://localhost:6379/0"

# Task settings for long-running scraping
app.conf.task_time_limit = 3600  # 1 hour max per task
app.conf.task_soft_time_limit = 3300  # 55 min soft limit
app.conf.worker_prefetch_multiplier = 1  # Don't prefetch tasks
app.conf.task_acks_late = True  # Acknowledge after task completes

# Serialization settings
app.conf.task_serializer = "json"
app.conf.accept_content = ["json"]
app.conf.result_serializer = "json"

# Import tasks
app.autodiscover_tasks(["tasks"])
