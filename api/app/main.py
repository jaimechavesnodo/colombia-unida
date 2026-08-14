"""Punto de entrada del monolito modular Colombia Unida."""

import logging
import pathlib
import uuid

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.core.db import check_db_ready
from app.core.logging import log_ctx, setup_logging

logger = logging.getLogger("app")

# Si el arranque no pudo dejar la base al día, el entrypoint escribe el motivo
# aquí y deja que el proceso arranque igual. La alternativa —morir— no deja
# rastro cuando el hosting no expone los logs, y entonces un despliegue roto
# es indistinguible de uno que todavía está construyéndose.
STARTUP_ERROR_FILE = pathlib.Path("/tmp/colombia-unida-startup-error.txt")  # noqa: S108
# Avisos que no impiden servir: por ejemplo, semillas que fallaron. El esquema
# está bien, así que la API funciona; solo faltan datos.
STARTUP_WARNING_FILE = pathlib.Path("/tmp/colombia-unida-startup-warning.txt")  # noqa: S108

# Rutas que siguen respondiendo en modo degradado: son las que sirven para
# diagnosticar. Todo lo demás devuelve 503 sin tocar la base.
DIAGNOSTIC_PATHS = ("/health", "/ready", "/docs", "/openapi.json")


def _read_note(path: pathlib.Path) -> str | None:
    try:
        return path.read_text().strip() or None
    except OSError:
        return None


def _startup_error() -> str | None:
    return _read_note(STARTUP_ERROR_FILE)


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

    @app.middleware("http")
    async def block_when_not_ready(request: Request, call_next):
        """En modo degradado no se atiende tráfico de negocio.

        Servir contra un esquema que no se pudo migrar es peor que no servir:
        se preserva la garantía de fallar cerrado, pero el proceso sigue vivo
        para poder decir por qué.
        """
        error = _startup_error()
        if error and not request.url.path.rstrip("/").endswith(DIAGNOSTIC_PATHS):
            return JSONResponse(
                status_code=503,
                media_type="application/problem+json",
                content={
                    "title": "Servicio no disponible",
                    "status": 503,
                    "detail": "La base de datos no quedó lista al arrancar.",
                },
            )
        return await call_next(request)

    @app.get("/health", include_in_schema=False)
    def health():
        """Liveness: el proceso está vivo. No consulta la base."""
        return {"status": "ok"}

    @app.get("/ready", include_in_schema=False)
    def ready():
        """Readiness: si algo falló al arrancar, lo dice con su motivo."""
        error = _startup_error()
        if error:
            log_ctx(logger, logging.ERROR, "readiness", startup_error=True)
            return JSONResponse(
                status_code=503,
                content={"status": "startup_failed", "db": False, "error": error},
            )
        db_ok = check_db_ready()
        status = "ok" if db_ok else "degraded"
        log_ctx(logger, logging.INFO if db_ok else logging.WARNING, "readiness", db=db_ok)
        body: dict = {"status": status, "db": db_ok}
        # Un aviso no impide servir, pero tiene que verse: si las semillas
        # fallaron, la demo aparecerá vacía y conviene saber por qué.
        warning = _read_note(STARTUP_WARNING_FILE)
        if warning:
            body["warnings"] = warning.splitlines()
        return JSONResponse(status_code=200 if db_ok else 503, content=body)

    # Routers de los bounded contexts
    from app.modules.console.router import router as console_router
    from app.modules.identity.auth_router import router as auth_router
    from app.modules.intake.webhook_router import router as webhook_router
    from app.modules.public_impact.router import router as public_router
    from app.modules.supply.router import router as supply_router

    app.include_router(webhook_router)
    app.include_router(public_router)
    app.include_router(auth_router)
    app.include_router(console_router)
    app.include_router(supply_router)

    return app


app = create_app()
