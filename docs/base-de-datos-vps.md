# Base de datos en el PostgreSQL del VPS

Colombia Unida usa el PostgreSQL que ya corre en el VPS de Hostinger, en vez de
levantar un contenedor `db` propio en EasyPanel. Una sola instancia para todos
los proyectos de NODO significa un solo respaldo, un solo ajuste de memoria y
un solo lugar donde mirar cuando algo va lento.

## Requisito: PostGIS

El esquema tiene columnas `geometry(Point, 4326)` para ubicaciones, así que la
extensión PostGIS tiene que estar disponible en el servidor. Para comprobarlo:

```bash
psql "$ADMIN_URL" -Atc "select * from pg_available_extensions where name='postgis';"
```

Si no aparece, hay que instalarla en el VPS (Debian/Ubuntu, ajustando la
versión mayor de Postgres):

```bash
sudo apt-get install -y postgresql-16-postgis-3
```

Sin PostGIS las migraciones fallan en la primera tabla con geometría. No hay
modo degradado: la ubicación es parte del dominio.

## Aprovisionamiento

El script `deploy/provision_db.sh` hace todo: crea el rol y la base, habilita
PostGIS, aplica las migraciones y siembra los datos. Es idempotente — correrlo
dos veces no duplica nada (verificado).

```bash
export ADMIN_URL='postgresql://postgres:CLAVE_ADMIN@HOST_DEL_VPS:5432/postgres'
export APP_PASSWORD='clave-para-el-rol-colombia_unida'
SEED_DEMO=1 ./deploy/provision_db.sh
```

- `ADMIN_URL` apunta a un usuario que pueda crear roles y bases (normalmente
  `postgres`) **y a la base `postgres`**, no a la de la aplicación.
- `SEED_DEMO=1` incluye el escenario sintético de demostración. Omítelo cuando
  vayan a entrar datos reales de un incidente activo.
- Al terminar imprime el `DATABASE_URL` que hay que pegar en EasyPanel.

El script no imprime ni guarda las contraseñas: viajan por variables de entorno
y no quedan en el repo.

## Acceso desde los contenedores de EasyPanel

Los contenedores no ven `localhost` del host. Según cómo esté expuesto el
Postgres del VPS, el `DATABASE_URL` cambia:

| Situación | Host a usar en `DATABASE_URL` |
|---|---|
| Postgres en el host, contenedores en la red de Docker | `host.docker.internal` o la IP del gateway de Docker (`172.17.0.1`) |
| Postgres como servicio de EasyPanel en el mismo proyecto | `colombia-unida_db` |
| Postgres en otro servidor | su host o IP, con `?sslmode=require` |

Para el primer caso hay que confirmar dos cosas en el servidor:

1. `listen_addresses` en `postgresql.conf` incluye la interfaz de Docker (o
   `'*'`).
2. `pg_hba.conf` permite conexiones desde la subred de Docker con `scram-sha-256`.

Después de tocar cualquiera de los dos: `sudo systemctl reload postgresql`.

## Respaldo

Al usar el Postgres del VPS, el respaldo de Colombia Unida entra en el que ya
exista para esa instancia. Si no hay ninguno, esto es el mínimo (pendiente
formal de M10):

```bash
pg_dump "$APP_URL" -Fc -f "/var/backups/colombia-unida-$(date +%F).dump"
```

Las claves de cifrado de la aplicación **no** están en la base: sin
`APP_ENCRYPTION_KEY` un respaldo restaurado deja los campos `[PRIV]`/`[SENS]`
ilegibles. El respaldo de la base y el de las claves son dos cosas distintas y
las dos son necesarias.
