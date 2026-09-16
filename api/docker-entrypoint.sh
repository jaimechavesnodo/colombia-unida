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
# Hay dos clases de fallo al arrancar y se tratan distinto:
#
#   - Transitorio (la base todavía no está: DNS que no resuelve tras reiniciar
#     el host, PostgreSQL arrancando). El contenedor sale con 75 y el
#     orquestador lo reinicia. Quedarse en modo degradado por esto dejaba la
#     API caída para siempre aunque la base volviera a los diez segundos.
#   - Determinista (falta PostGIS, una migración rota). Reintentar no lo
#     arregla: el proceso vive en modo degradado para poder explicarlo por
#     /ready, y rechaza todo el tráfico de negocio.
set -e

# EX_TEMPFAIL: lo devuelve app.bootstrap_db cuando la base no está disponible.
EXIT_TRANSITORIO=75

# Dónde queda el motivo si el arranque no logra dejar la base lista. La API lo
# lee y responde 503 con ese texto en /ready, rechazando el tráfico de negocio.
ERR_FILE=/tmp/colombia-unida-startup-error.txt
rm -f "$ERR_FILE"

# Deja constancia del fallo y arranca igual, en modo degradado. Morir aquí no
# deja rastro cuando el hosting no expone logs: un despliegue roto se vería
# igual que uno que todavía está construyéndose. El proceso vive para poder
# explicar qué pasó; la garantía de no servir contra un esquema equivocado la
# mantiene la API rechazando todo lo que no sea diagnóstico.
fallar_degradado() {
  echo "$1" | tee "$ERR_FILE" >&2
  echo "[entrypoint] arrancando en modo degradado: /ready explica el motivo" >&2
}

# Espera a que la base esté disponible y, si DB_BOOTSTRAP_URL está definida,
# crea rol, base y extensiones. La espera corre siempre: el worker tampoco
# puede trabajar sin base, y quién arranca primero no lo decide nadie.
echo "[entrypoint] esperando la base y aprovisionando…"
if salida=$(python -m app.bootstrap_db 2>&1); then
  printf '%s\n' "$salida"
else
  codigo=$?
  printf '%s\n' "$salida" >&2
  if [ "$codigo" -eq "$EXIT_TRANSITORIO" ]; then
    echo "[entrypoint] la base no respondió; saliendo para que se reintente" >&2
    exit "$EXIT_TRANSITORIO"
  fi
  fallar_degradado "$(printf '%s' "$salida" | tail -n 3)"
fi

if [ ! -f "$ERR_FILE" ]; then
  echo "[entrypoint] aplicando migraciones…"
  if ! salida=$(alembic upgrade head 2>&1); then
    fallar_degradado "$(printf '%s' "$salida" | tail -n 3)"
  else
    printf '%s\n' "$salida"
  fi
fi

# Las semillas son best-effort, a diferencia de las migraciones: si fallan, el
# esquema sigue siendo correcto y la API puede servir — solo faltarían datos.
# Tumbar el servicio por un problema al sembrar sería desproporcionado. El
# motivo queda en un archivo de avisos que /ready reporta sin marcar el
# servicio como no listo.
WARN_FILE=/tmp/colombia-unida-startup-warning.txt
rm -f "$WARN_FILE"

avisar() {
  echo "$1" | tee -a "$WARN_FILE" >&2
}

sembrar() {
  etiqueta="$1"
  shift
  echo "[entrypoint] $etiqueta…"
  if ! salida=$("$@" 2>&1); then
    avisar "$etiqueta falló: $(printf '%s' "$salida" | tail -n 2)"
  else
    printf '%s\n' "$salida"
  fi
}

# Semillas base (roles, catálogo de necesidades, DIVIPOLA, incidente). Son
# idempotentes, así que correrlas en cada arranque no duplica nada; se dejan
# detrás de una bandera para que un despliegue no toque datos sin querer.
if [ "${RUN_SEEDS:-0}" = "1" ] && [ ! -f "$ERR_FILE" ]; then
  sembrar "semillas base" python -m app.seeds
fi

# Datos sintéticos de demostración. NUNCA en un entorno con datos reales de
# un incidente activo: crea personas, casos y usuarios de prueba.
if [ "${SEED_DEMO:-0}" = "1" ] && [ ! -f "$ERR_FILE" ]; then
  if [ "${APP_ENV:-local}" = "production" ]; then
    echo "[entrypoint] SEED_DEMO ignorado: APP_ENV=production" >&2
  else
    sembrar "datos de demostración" python -m app.seeds.demo
  fi
fi

exec "$@"
