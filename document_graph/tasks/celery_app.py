from __future__ import annotations

import os

import logging

from celery import Celery

from document_graph.logging_config import setup_logging


setup_logging()
logger = logging.getLogger(__name__)


def _redis_url() -> str:
    return os.environ.get("REDIS_URL", "redis://localhost:6379/0")


celery_app = Celery(
    "document_graph",
    broker=_redis_url(),
    backend=_redis_url(),
)

celery_app.conf.update(
    task_track_started=True,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    worker_hijack_root_logger=False,
    worker_redirect_stdouts=True,
    worker_redirect_stdouts_level="INFO",
)
logger.info("celery_configured")

# Ensure tasks are registered when the worker starts.
from document_graph.tasks import document_index as _document_index  # noqa: E402,F401
