"""Antivirus ClamAV vía protocolo clamd INSTREAM (§4.2-7 del alcance).

`scan_bytes` nunca lanza: devuelve "CLEAN" | "INFECTED" | "ERROR" |
"SKIPPED" (sin AV configurado) y el llamador decide la política.
"""

import logging
import socket

from app.core.config import get_settings
from app.core.logging import log_ctx

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 30.0
_CHUNK_SIZE = 8192


def scan_bytes(data: bytes, host: str | None = None, port: int | None = None) -> str:
    """Escanea bytes con clamd (zINSTREAM). Ver docstring del módulo."""
    settings = get_settings()
    host = host if host is not None else settings.clamav_host
    port = port if port is not None else settings.clamav_port
    if not host:
        return "SKIPPED"
    try:
        with socket.create_connection((host, port), timeout=_TIMEOUT_SECONDS) as sock:
            sock.settimeout(_TIMEOUT_SECONDS)
            sock.sendall(b"zINSTREAM\0")
            for i in range(0, len(data), _CHUNK_SIZE):
                chunk = data[i : i + _CHUNK_SIZE]
                sock.sendall(len(chunk).to_bytes(4, "big") + chunk)
            sock.sendall((0).to_bytes(4, "big"))
            response = b""
            while b"\0" not in response:
                part = sock.recv(4096)
                if not part:
                    break
                response += part
    except OSError:
        log_ctx(logger, logging.WARNING, "clamav.connection_error")
        return "ERROR"
    reply = response.decode("utf-8", errors="replace").strip("\0").strip()
    if reply.endswith("OK"):
        return "CLEAN"
    if reply.endswith("FOUND"):
        return "INFECTED"
    log_ctx(logger, logging.WARNING, "clamav.unexpected_reply")
    return "ERROR"
