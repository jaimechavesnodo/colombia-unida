"""Punto de entrada del monolito modular Colombia Unida."""

import logging
import uuid

from fastapi import FastAPI, Request, Response

from app.core.config import get_settings
from app.core.db import check_db_ready
from app.core.logging import log_ctx, setup_logging

logger = logging.getLogger("app")


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging(settings.log_level)

    app = FastAPI(
        title="Colombia Unida — Sistema Humanitario",
        version="0.1.0",
        root_path=settings.root_path,
        docs_url="/docs" if settings.app_env != "production" else None,
        redoc_url=None,
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id
        response: Response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    @app.get("/health", include_in_schema=False)
    def health():
        return {"status": "ok"}

    @app.get("/ready", include_in_schema=False)
    def ready():
        db_ok = check_db_ready()
        status = "ok" if db_ok else "degraded"
        log_ctx(logger, logging.INFO if db_ok else logging.WARNING, "readiness", db=db_ok)
        return {"status": status, "db": db_ok}

    # Routers de los bounded contexts
    from app.modules.intake.webhook_router import router as webhook_router
    from app.modules.public_impact.router import router as public_router

    app.include_router(webhook_router)
    app.include_router(public_router)

    return app


app = create_app()
