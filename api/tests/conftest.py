"""Fixtures compartidas.

Las pruebas con base de datos usan TEST_DATABASE_URL (o DATABASE_URL).
Si no hay servidor alcanzable se saltan — en CI siempre hay contenedor
PostGIS; en local, Postgres.app en el puerto 5433.
"""

import base64
import os

import pytest
import sqlalchemy as sa

DEFAULT_TEST_DB = "postgresql+psycopg://colombia_unida@localhost:5433/colombia_unida_test"


def _test_db_url() -> str:
    return os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL") or DEFAULT_TEST_DB


def _db_available(url: str) -> bool:
    try:
        engine = sa.create_engine(url, connect_args={"connect_timeout": 2})
        with engine.connect():
            return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def db_url() -> str:
    url = _test_db_url()
    if not _db_available(url):
        # Intentar crear la BD de test si el servidor responde
        admin_url = url.rsplit("/", 1)[0] + "/postgres"
        dbname = url.rsplit("/", 1)[1]
        try:
            engine = sa.create_engine(admin_url, isolation_level="AUTOCOMMIT")
            with engine.connect() as conn:
                conn.execute(sa.text(f'CREATE DATABASE "{dbname}"'))
        except Exception:
            pytest.skip("Base de datos de prueba no disponible")
        if not _db_available(url):
            pytest.skip("Base de datos de prueba no disponible")
    return url


@pytest.fixture(scope="session", autouse=True)
def _test_keys():
    """Claves de cifrado de prueba para toda la sesión."""
    os.environ.setdefault("APP_ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    os.environ.setdefault("APP_HMAC_KEY", base64.b64encode(os.urandom(32)).decode())


@pytest.fixture(scope="session")
def migrated_engine(db_url):
    """Motor sobre una BD con todas las migraciones aplicadas."""
    os.environ["DATABASE_URL"] = db_url
    from app.core import config, db

    config.get_settings.cache_clear()
    db._engine = None
    db._session_factory = None

    from alembic.config import Config

    from alembic import command

    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option(
        "script_location", os.path.join(os.path.dirname(__file__), "..", "alembic")
    )
    engine = sa.create_engine(db_url)
    with engine.connect() as conn:
        conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS postgis"))
        conn.commit()
    command.upgrade(cfg, "head")
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(migrated_engine):
    from sqlalchemy.orm import sessionmaker

    factory = sessionmaker(bind=migrated_engine, expire_on_commit=False)
    session = factory()
    yield session
    session.rollback()
    session.close()
