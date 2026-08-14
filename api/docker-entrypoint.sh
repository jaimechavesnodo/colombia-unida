#!/bin/sh
# Arranque del contenedor: primero deja la base al día, después ejecuta el
# proceso (API o worker).
#
# Las migraciones corren aquí y no en un job aparte porque el despliegue es
# de una sola réplica (EasyPanel, réplicas: 1): no hay dos contenedores
# compitiendo por aplicar la misma migración. Si algún día hay más de una
# réplica, esto debe salir a un paso previo del despliegue — Alembic toma un
# lock por transacción, pero dos arranques simultáneos igual son ruido
# innecesario en los logs.
#
# Si la migración falla, el contenedor NO arranca: es preferible un servicio
# caído y visible a una API sirviendo contra un esquema equivocado.
set -e

# Aprovisionamiento de la base: crea rol, base y PostGIS si hace falta. Solo
# actúa si DB_BOOTSTRAP_URL está definida (usuario con permiso para crear
# bases). Necesario cuando la instancia de PostgreSQL es compartida y no está
# expuesta fuera de la red de Docker.
if [ -n "${DB_BOOTSTRAP_URL:-}" ]; then
  echo "[entrypoint] aprovisionando base…"
  python -m app.bootstrap_db
fi

echo "[entrypoint] aplicando migraciones…"
alembic upgrade head

# Semillas base (roles, catálogo de necesidades, DIVIPOLA, incidente). Son
# idempotentes, así que correrlas en cada arranque no duplica nada; se dejan
# detrás de una bandera para que un despliegue no toque datos sin querer.
if [ "${RUN_SEEDS:-0}" = "1" ]; then
  echo "[entrypoint] semillas base…"
  python -m app.seeds
fi

# Datos sintéticos de demostración. NUNCA en un entorno con datos reales de
# un incidente activo: crea personas, casos y usuarios de prueba.
if [ "${SEED_DEMO:-0}" = "1" ]; then
  if [ "${APP_ENV:-local}" = "production" ]; then
    echo "[entrypoint] SEED_DEMO ignorado: APP_ENV=production" >&2
  else
    echo "[entrypoint] datos de demostración…"
    python -m app.seeds.demo
  fi
fi

exec "$@"
