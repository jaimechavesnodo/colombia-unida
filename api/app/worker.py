"""Worker de procesos asíncronos.

Consume el outbox transaccional (§1.4-5 del alcance): eventos publicados
en la misma transacción que los cambios de dominio, procesados aquí con
consumidores idempotentes, reintentos y DLQ. Los handlers se registran
por event_type a medida que se implementan los milestones.
"""

import logging
import signal
import time

from app.core.config import get_settings
from app.core.logging import log_ctx, setup_logging

logger = logging.getLogger("worker")

_running = True

# Registro de handlers: event_type -> callable(session, event) idempotente.
HANDLERS: dict = {}


def _stop(signum, frame):  # noqa: ARG001
    global _running
    _running = False


def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    log_ctx(logger, logging.INFO, "worker started", env=settings.app_env)

    while _running:
        # M1 introduce la tabla outbox_events y aquí el polling con
        # SELECT ... FOR UPDATE SKIP LOCKED. Por ahora, latido.
        time.sleep(5)

    log_ctx(logger, logging.INFO, "worker stopped")


if __name__ == "__main__":
    main()
