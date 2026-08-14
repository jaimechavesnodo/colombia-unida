"""Crea el rol, la base y la extensión PostGIS antes de migrar.

Corre dentro del contenedor porque la instancia de PostgreSQL del VPS no está
expuesta a internet: desde fuera de la red de Docker no hay forma de alcanzarla.

Se activa con DB_BOOTSTRAP_URL, que debe apuntar a un usuario con permiso para
crear roles y bases (normalmente `postgres`) y a una base de mantenimiento que
ya exista. El resto lo deduce de DATABASE_URL.

Es idempotente: si el rol, la base o la extensión ya existen, no hace nada.

Usa psycopg y no `psql` a propósito: la imagen base es python:3.12-slim y no
trae cliente de línea de comandos de PostgreSQL.
"""

import logging
import os
import sys
from urllib.parse import unquote, urlparse

import psycopg
from psycopg import sql

from app.core.logging import setup_logging

logger = logging.getLogger("bootstrap_db")


class BootstrapError(RuntimeError):
    pass


def _parse_app_url(raw: str) -> tuple[str, str, str]:
    """Devuelve (base, usuario, contraseña) del DATABASE_URL de la aplicación."""
    # SQLAlchemy usa 'postgresql+psycopg://'; urlparse necesita un esquema simple.
    parsed = urlparse(raw.replace("postgresql+psycopg://", "postgresql://", 1))
    dbname = (parsed.path or "").lstrip("/")
    if not dbname:
        raise BootstrapError("DATABASE_URL no incluye el nombre de la base")
    if not parsed.username:
        raise BootstrapError("DATABASE_URL no incluye usuario")
    return dbname, unquote(parsed.username), unquote(parsed.password or "")


def _admin_dsn(raw: str) -> str:
    return raw.replace("postgresql+psycopg://", "postgresql://", 1)


def ensure_role(cur, user: str, password: str) -> bool:
    """CREATE/ALTER ROLE no admiten parámetros ligados: la contraseña se
    interpola como literal SQL con psycopg.sql, que la escapa correctamente."""
    cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (user,))
    exists = cur.fetchone() is not None
    verbo = "ALTER" if exists else "CREATE"
    cur.execute(
        sql.SQL("{} ROLE {} WITH LOGIN PASSWORD {}").format(
            sql.SQL(verbo), sql.Identifier(user), sql.Literal(password)
        )
    )
    # Si el rol venía de un intento anterior con otra clave, el ALTER la alinea
    # con la que va a usar la aplicación.
    return not exists


def ensure_database(cur, dbname: str, owner: str) -> bool:
    cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,))
    if cur.fetchone():
        return False
    # CREATE DATABASE tampoco admite parámetros ligados ni corre en transacción.
    crear = sql.SQL("CREATE DATABASE {} OWNER {}").format(
        sql.Identifier(dbname), sql.Identifier(owner)
    )
    try:
        cur.execute(crear)
    except psycopg.errors.InternalError_ as exc:
        if "collation version mismatch" not in str(exc):
            raise
        # Pasa cuando el volumen de datos lo inicializó una imagen con una
        # glibc distinta a la que quedó corriendo (p. ej. el servicio se creó
        # con postgres:17 y después se cambió a postgis/postgis:17-3.5).
        # CREATE DATABASE copia template1 y se niega si esa metadata no cuadra.
        # Refrescarla es el remedio que documenta PostgreSQL, y en un clúster
        # recién creado no hay datos cuyos índices puedan verse afectados.
        logger.warning("template1 con collation desajustada; se refresca")
        cur.execute("ALTER DATABASE template1 REFRESH COLLATION VERSION")
        cur.execute(crear)
    return True


def ensure_postgis(cur) -> None:
    """Habilita PostGIS, con un diagnóstico claro si no está instalada.

    El esquema tiene columnas geometry(Point, 4326): sin PostGIS la primera
    migración falla, y el error de Postgres ("could not open extension control
    file") no dice qué hacer. Aquí se distingue entre «no está instalada en el
    servidor» y «está instalada pero este rol no puede habilitarla».
    """
    cur.execute("SELECT 1 FROM pg_extension WHERE extname = 'postgis'")
    if cur.fetchone():
        logger.info("postgis ya estaba habilitada")
        return

    cur.execute("SELECT 1 FROM pg_available_extensions WHERE name = 'postgis'")
    if not cur.fetchone():
        raise BootstrapError(
            "PostGIS no está instalada en este servidor de PostgreSQL. "
            "La imagen pgvector/pgvector no la incluye. Opciones: usar una "
            "imagen postgis/postgis:17-3.5 para esta base, o instalar el "
            "paquete postgresql-17-postgis-3 en el servidor. "
            "El esquema tiene columnas de geometría y no hay modo degradado."
        )

    cur.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    logger.info("postgis habilitada")


def main() -> int:
    setup_logging()
    admin_raw = os.environ.get("DB_BOOTSTRAP_URL", "").strip()
    if not admin_raw:
        logger.info("sin DB_BOOTSTRAP_URL: nada que aprovisionar")
        return 0

    app_url = os.environ.get("DATABASE_URL", "").strip()
    if not app_url:
        raise BootstrapError("Falta DATABASE_URL")

    dbname, user, password = _parse_app_url(app_url)
    if not password:
        raise BootstrapError("DATABASE_URL no incluye contraseña para el rol")

    # autocommit: CREATE DATABASE no puede ejecutarse dentro de una transacción.
    with psycopg.connect(_admin_dsn(admin_raw), autocommit=True) as conn:
        with conn.cursor() as cur:
            created_role = ensure_role(cur, user, password)
            created_db = ensure_database(cur, dbname, user)
    logger.info(
        "rol y base verificados",
        extra={"role_created": created_role, "database_created": created_db},
    )

    # PostGIS se habilita dentro de la base de la aplicación y con el usuario
    # administrador: CREATE EXTENSION exige superusuario.
    admin_db_dsn = _admin_dsn(admin_raw).rsplit("/", 1)[0] + f"/{dbname}"
    with psycopg.connect(admin_db_dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            ensure_postgis(cur)
            # El rol de la aplicación crea tablas en public durante la migración.
            cur.execute(
                sql.SQL("GRANT ALL ON SCHEMA public TO {}").format(sql.Identifier(user))
            )
    logger.info("bootstrap completo", extra={"database": dbname})
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BootstrapError as exc:
        # Mensaje accionable en una sola línea: es lo que se va a leer en los
        # logs de EasyPanel cuando el contenedor no arranque.
        logger.error("bootstrap falló: %s", exc)
        sys.exit(1)
