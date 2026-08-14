#!/usr/bin/env bash
# Aprovisiona la base de Colombia Unida en un PostgreSQL ya existente
# (por ejemplo el del VPS de Hostinger) y la deja lista para la aplicación.
#
# Qué hace, en orden:
#   1. crea el rol de aplicación y la base, si no existen
#   2. habilita PostGIS en esa base
#   3. aplica las migraciones de Alembic
#   4. siembra roles, catálogo de necesidades, geografía DANE e incidente
#   5. opcionalmente siembra el escenario de demostración
#
# Es idempotente: correrlo dos veces no duplica nada.
#
# Uso:
#   export ADMIN_URL='postgresql://postgres:CLAVE@HOST:5432/postgres'
#   export APP_PASSWORD='clave-de-la-app'
#   ./deploy/provision_db.sh              # sin datos de demostración
#   SEED_DEMO=1 ./deploy/provision_db.sh  # con datos de demostración
#
# ADMIN_URL debe apuntar a un usuario con permiso para crear roles y bases
# (normalmente 'postgres') y a la base 'postgres', no a la de la aplicación.
# Nada de esto se guarda en el repo: las credenciales viajan por variables de
# entorno y este script no las imprime.
set -euo pipefail

DB_NAME="${DB_NAME:-colombia_unida}"
DB_USER="${DB_USER:-colombia_unida}"
SEED_DEMO="${SEED_DEMO:-0}"
API_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../api" && pwd)"

if [[ -z "${ADMIN_URL:-}" ]]; then
  echo "Falta ADMIN_URL (usuario con permiso para crear bases)." >&2
  exit 1
fi
if [[ -z "${APP_PASSWORD:-}" ]]; then
  echo "Falta APP_PASSWORD (contraseña del rol de la aplicación)." >&2
  exit 1
fi

# El host/puerto se extraen de ADMIN_URL para construir la URL de la app sin
# pedir los mismos datos dos veces.
HOSTPORT="$(printf '%s' "$ADMIN_URL" | sed -E 's#^[^@]+@##; s#/.*$##')"

echo "==> 1/5 rol y base"
# CREATE ROLE/DATABASE no aceptan IF NOT EXISTS: se consulta primero.
psql "$ADMIN_URL" -v ON_ERROR_STOP=1 -q \
  -v user="$DB_USER" -v pass="$APP_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'user', :'pass')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'user')
\gexec
-- Si el rol ya existía, se alinea la contraseña con la que se va a usar.
SELECT format('ALTER ROLE %I LOGIN PASSWORD %L', :'user', :'pass')
WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'user')
\gexec
SQL

psql "$ADMIN_URL" -v ON_ERROR_STOP=1 -q -v db="$DB_NAME" -v user="$DB_USER" <<'SQL'
SELECT format('CREATE DATABASE %I OWNER %I', :'db', :'user')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'db')
\gexec
SQL

# Misma URL de admin pero apuntando a la base de la aplicación. Se hace con
# expansión de bash y no con sed: dentro de comillas dobles, el '$' de la
# expresión regular lo interpreta el shell.
ADMIN_DB_URL="${ADMIN_URL%/*}/${DB_NAME}"

echo "==> 2/5 PostGIS"
# Requiere superusuario, por eso va con ADMIN_URL y no con el rol de la app.
# La migración inicial también lo intenta, pero ahí el rol puede no tener
# permiso; hacerlo aquí evita ese fallo.
psql "$ADMIN_DB_URL" -v ON_ERROR_STOP=1 -q \
  -c "CREATE EXTENSION IF NOT EXISTS postgis" \
  -c "GRANT ALL ON SCHEMA public TO ${DB_USER}"

APP_URL="postgresql+psycopg://${DB_USER}:${APP_PASSWORD}@${HOSTPORT}/${DB_NAME}"

echo "==> 3/5 migraciones"
cd "$API_DIR"
PY="${PYTHON:-.venv/bin/python}"
[[ -x "$PY" ]] || PY="python3"
DATABASE_URL="$APP_URL" "$PY" -m alembic upgrade head

echo "==> 4/5 semillas base"
DATABASE_URL="$APP_URL" "$PY" -m app.seeds

if [[ "$SEED_DEMO" == "1" ]]; then
  echo "==> 5/5 datos de demostración (sintéticos)"
  DATABASE_URL="$APP_URL" "$PY" -m app.seeds.demo
else
  echo "==> 5/5 datos de demostración: omitidos (SEED_DEMO=1 para incluirlos)"
fi

echo
echo "Listo. DATABASE_URL para EasyPanel (api y worker):"
echo "postgresql+psycopg://${DB_USER}:<APP_PASSWORD>@${HOSTPORT}/${DB_NAME}"
