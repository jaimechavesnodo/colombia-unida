"""Worker de procesos asíncronos.

Consume el outbox transaccional (§1.4-5): eventos publicados en la misma
transacción que los cambios de dominio, procesados con consumidores
idempotentes, reintentos con backoff y DLQ. Los handlers se registran
por event_type en los módulos (import de registros abajo).
"""

import logging
import signal
import time

from app.core.config import get_settings
from app.core.db import get_session_factory
from app.core.logging import log_ctx, setup_logging
from app.core.outbox import process_batch

logger = logging.getLogger("worker")

_running = True

POLL_INTERVAL_SECONDS = 2.0


def _stop(signum, frame):  # noqa: ARG001
    global _running
    _running = False


def _register_handlers() -> None:
    """Importa los módulos que registran handlers de eventos.

    Se amplía por milestone (conversación, IA, matching, analytics,
    retención, anclas de auditoría).
    """


def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    _register_handlers()
    log_ctx(logger, logging.INFO, "worker started", env=settings.app_env)

    session_factory = get_session_factory()
    while _running:
        try:
            handled = process_batch(session_factory)
        except Exception:
            logger.exception("outbox batch failed")
            handled = 0
        if handled == 0:
            time.sleep(POLL_INTERVAL_SECONDS)

    log_ctx(logger, logging.INFO, "worker stopped")


if __name__ == "__main__":
    main()
