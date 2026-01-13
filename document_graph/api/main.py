from __future__ import annotations

import os

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from document_graph.api.routers import chat, conversations, health, tasks, workspaces
from document_graph.api.routers import documents
from document_graph.logging_config import setup_logging


logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    setup_logging()
    app = FastAPI(title="document_graph", version="0.1.0")
    origins = [o.strip() for o in os.environ.get("CORS_ORIGINS", "http://localhost:3000").split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router)
    app.include_router(workspaces.router, prefix="/workspaces", tags=["workspaces"])
    app.include_router(conversations.router, prefix="/workspaces/{workspace_id}/conversations", tags=["conversations"])
    app.include_router(documents.router, prefix="/workspaces/{workspace_id}/documents", tags=["documents"])
    app.include_router(chat.router, prefix="/workspaces/{workspace_id}", tags=["chat"])
    app.include_router(tasks.router, prefix="/tasks", tags=["tasks"])
    logger.info("api_ready")
    return app


app = create_app()
