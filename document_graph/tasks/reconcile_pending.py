from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime

from celery.result import AsyncResult
from sqlalchemy import create_engine, text

from document_graph.tasks.celery_app import celery_app
from document_graph.logging_config import setup_logging


setup_logging()
logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.utcnow()


def main() -> int:
    pg_url = os.environ.get("POSTGRES_URL")
    if not pg_url:
        raise SystemExit("POSTGRES_URL is required")

    engine = create_engine(pg_url, pool_pre_ping=True, future=True)
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "select id, celery_task_id from tasks "
                "where status='pending' and celery_task_id is not null "
                "order by created_at desc limit 200"
            )
        ).fetchall()

        updated = 0
        logger.info("reconcile_pending_loaded count=%s", len(rows))
        for r in rows:
            task_db_id = uuid.UUID(str(r.id))
            celery_id = str(r.celery_task_id)
            res = AsyncResult(celery_id, app=celery_app)
            state = res.state
            if state in {"PENDING", "RECEIVED", "STARTED", "RETRY"}:
                continue
            if state == "SUCCESS":
                conn.execute(
                    text("update tasks set status='succeeded', updated_at=:now where id=:id and status='pending'"),
                    {"id": task_db_id, "now": _utcnow()},
                )
                updated += 1
                continue
            # FAILURE or other terminal states
            err = {"celery_state": state}
            try:
                err["celery_result"] = res.result if isinstance(res.result, (str, int, float, dict, list)) else str(res.result)
            except Exception:
                pass
            conn.execute(
                text(
                    "update tasks set status='failed', error=:error::jsonb, updated_at=:now "
                    "where id=:id and status='pending'"
                ),
                {"id": task_db_id, "now": _utcnow(), "error": json.dumps(err, ensure_ascii=False)},
            )
            updated += 1

    logger.info("reconcile_pending_done checked=%s updated=%s", len(rows), updated)
    print(json.dumps({"checked": len(rows), "updated": updated}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

