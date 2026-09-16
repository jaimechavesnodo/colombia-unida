"""Arranque contra la base: qué se reintenta y qué se queda en modo degradado.

El 16-sep-2026 el plano público estuvo caído con un 503 porque la API arrancó
antes que su base tras un reinicio del host, no pudo resolver el DNS del
servicio y se quedó en modo degradado **para siempre**, aunque la base volvió
a los pocos segundos. Estas pruebas fijan la distinción que lo corrige.
"""

import socket
import threading
import time

import pytest

from app.bootstrap_db import (
    EXIT_TRANSITORIO,
    BaseNoDisponible,
    BootstrapError,
    esperar_base,
)


def _dsn(host: str, port: int) -> str:
    return f"postgresql+psycopg://u:p@{host}:{port}/db"


def test_dns_que_no_resuelve_es_transitorio():
    """El caso real: el nombre del servicio de Swarm aún no existe."""
    with pytest.raises(BaseNoDisponible):
        esperar_base(_dsn("host-que-no-existe.invalid", 5432), limite_s=2)


def test_puerto_cerrado_es_transitorio():
    with pytest.raises(BaseNoDisponible):
        esperar_base(_dsn("127.0.0.1", 59999), limite_s=2)


def test_transitorio_y_determinista_no_se_confunden():
    """Son jerarquías distintas: el entrypoint decide según cuál se lanza."""
    assert not issubclass(BaseNoDisponible, BootstrapError)
    assert not issubclass(BootstrapError, BaseNoDisponible)
    # EX_TEMPFAIL: el entrypoint lo traduce en "sal y que te reinicien".
    assert EXIT_TRANSITORIO == 75


def test_espera_a_que_la_base_aparezca():
    """No se rinde al primer intento: la base puede tardar en levantar."""
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    puerto = srv.getsockname()[1]
    srv.close()  # se libera y se vuelve a abrir tarde, para simular el arranque

    listo = threading.Event()

    def abrir_tarde():
        time.sleep(3)
        s = socket.socket()
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", puerto))
        s.listen(1)
        listo.set()
        time.sleep(10)

    hilo = threading.Thread(target=abrir_tarde, daemon=True)
    hilo.start()
    try:
        esperar_base(_dsn("127.0.0.1", puerto), limite_s=25)
    finally:
        listo.wait(timeout=15)
