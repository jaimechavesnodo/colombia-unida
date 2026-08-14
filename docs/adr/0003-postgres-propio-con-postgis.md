# ADR-0003 — Postgres propio con PostGIS en vez del compartido del VPS

Fecha: 14 de agosto de 2026
Estado: aceptada (revisable)

## Contexto

La instrucción era usar el PostgreSQL que ya corre en el VPS de Hostinger
(proyecto `nodo-postgres` en EasyPanel), donde vive la migración de las bases de
Supabase. Una sola instancia para todo NODO significa un solo respaldo, un solo
ajuste de memoria y un solo lugar donde mirar.

Al intentarlo, el arranque de la API reportó por `/ready`:

> PostGIS no está instalada en este servidor de PostgreSQL.

La instancia compartida corre `pgvector/pgvector:pg17`: Postgres 17 con
pgvector, sin PostGIS. El esquema de Colombia Unida tiene columnas
`geometry(Point, 4326)` en `locations` e `incidents`, así que sin PostGIS la
primera migración no pasa. No hay modo degradado para eso.

## Opciones consideradas

1. **Cambiar la imagen de la instancia compartida** a `postgis/postgis:17-3.5`.
   Mantiene una sola instancia, que era el objetivo. Pero esa imagen **no trae
   pgvector**: cualquier proyecto que use columnas `vector` dejaría de
   funcionar. Desde fuera no hay forma de verificar quién las usa, y el cambio
   reinicia una base compartida. Riesgo alto sobre infraestructura de la que
   dependen otros proyectos.

2. **Instalar `postgresql-17-postgis-3` dentro del contenedor en ejecución.**
   Se pierde en el siguiente despliegue de ese servicio. No es una solución.

3. **Un Postgres propio para Colombia Unida** (`postgis/postgis:17-3.5`), como
   servicio `db` dentro del proyecto, en el mismo VPS.

## Decisión

La opción 3. Sigue estando en el VPS de Hostinger, no toca infraestructura
compartida y no le quita capacidades a nadie. Cuesta una instancia más de
PostgreSQL (~100 MB en reposo).

## Consecuencias

- El respaldo de Colombia Unida es propio: no entra en el que exista para
  `nodo-postgres`. Queda pendiente de M10 y es ahora más importante, porque
  nadie más lo va a cubrir por nosotros.
- Si en el futuro se quiere consolidar en una sola instancia, hace falta una
  imagen con PostGIS **y** pgvector (hay que construirla: no existe oficial), y
  una ventana de mantenimiento. Es una consolidación deliberada, no algo que
  deba pasar por descuido.
- `DB_BOOTSTRAP_URL` apunta al superusuario de este `db`, no al compartido.

## Nota sobre cómo se diagnosticó

El backend de logs del panel no responde (`fetch failed` incluso para servicios
con meses corriendo), así que un contenedor que muere no deja rastro. Por eso el
arranque ahora escribe el motivo del fallo y la API vive para reportarlo en
`/ready`, rechazando el tráfico de negocio con 503. Sin eso, este diagnóstico
habría sido adivinar entre varias causas plausibles.
