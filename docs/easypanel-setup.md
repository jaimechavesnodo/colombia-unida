# Despliegue en EasyPanel — proyecto `colombia-unida`

Configuración inicial (una sola vez). Convención NODO: proyecto en EasyPanel +
servicios, dominio `nodo.host/colombia-unida`, auto-deploy por webhook de
GitHub.

**Los valores reales de las variables ya están generados** en
`deploy/easypanel.env.local` (ignorado por git). Ese archivo es la fuente para
copiar y pegar en EasyPanel; guarda una copia en el vault de NODO. Si se
pierden `APP_ENCRYPTION_KEY` y `APP_HMAC_KEY`, los campos cifrados de la base
quedan ilegibles y no hay manera de recuperarlos.

## Para esta primera versión bastan 3 servicios

`api`, `worker` y `web`.

- **La base de datos no es un servicio de EasyPanel**: se usa el PostgreSQL que
  ya corre en el VPS de Hostinger. Antes de crear los servicios hay que
  aprovisionarla con `deploy/provision_db.sh` — ver `base-de-datos-vps.md`.
- **MinIO y ClamAV no hacen falta todavía**: solo entran en juego cuando llegue
  media por WhatsApp, y eso depende de la WABA productiva (ver
  `waba-setup.md`). La API arranca sin ellos.

## 0. Base de datos (antes de tocar el panel)

```bash
export ADMIN_URL='postgresql://postgres:CLAVE@HOST:5432/postgres'
export APP_PASSWORD='clave-para-el-rol-colombia_unida'
SEED_DEMO=1 ./deploy/provision_db.sh
```

Al terminar imprime el `DATABASE_URL` que va en `api` y `worker`. Ese valor
reemplaza el que trae `deploy/easypanel.env.local`, que apunta a un servicio
`db` que ya no existe.

## 1. Crear proyecto

- EasyPanel → **+** → Crear proyecto → nombre: `colombia-unida`

## 2. Servicios

### 2.1 `api` (Servicio → App → GitHub)

- Propietario: `jaimechavesnodo` · Repo: `colombia-unida` · Rama: `main`
- Ruta de compilación: `/api` · Compilación: **Dockerfile**
- Entorno: todo el bloque «api y worker» del archivo generado, con el
  `DATABASE_URL` que imprimió `provision_db.sh`, y **`RUN_SEEDS=0` /
  `SEED_DEMO=0`** (el script ya sembró; dejarlos en `1` repite el trabajo en
  cada arranque)
- Sin dominio público directo (entra por el nginx de `web`)
- Avanzado: réplicas `1`

El contenedor aplica `alembic upgrade head` al arrancar, así que un despliegue
con migraciones nuevas se actualiza solo. Si la migración falla, el contenedor
no arranca — es a propósito: mejor un servicio caído y visible que una API
sirviendo contra un esquema equivocado.

### 2.2 `worker` (Servicio → App → GitHub)

- Igual que `api` (mismo repo, misma ruta `/api`, mismo Dockerfile)
- **Comando de arranque:** `python -m app.worker`
- Mismas variables que `api`, con `RUN_SEEDS=0` y `SEED_DEMO=0`
  (que dos contenedores siembren a la vez es una carrera innecesaria)

### 2.3 `web` (Servicio → App → GitHub)

- Propietario: `jaimechavesnodo` · Repo: `colombia-unida` · Rama: `main`
- Ruta de compilación: `/` · Compilación: **Dockerfile** →
  archivo: `web-server/Dockerfile`
- Dominio: `https://nodo.host/colombia-unida` → destino interno
  `http://colombia-unida_web:80/`
- Avanzado: réplicas `1`, cero tiempo de inactividad ✅

Este servicio compila las dos SPAs, las sirve y hace proxy de
`/colombia-unida/api/` hacia el servicio `api`.

## 3. Auto-deploy

1. Copiar la **URL de deploy** de cada servicio GitHub (`api`, `worker`, `web`)
   desde EasyPanel y pegarla en el chat.
2. Claude crea los webhooks en GitHub vía API y verifica la entrega
   (`status: OK` / `code: 200`).

Desde ahí, cada `git push` a `main` redespliega solo.

## 4. Verificación

En este orden — si el primero falla, los demás también:

```bash
curl https://nodo.host/colombia-unida/api/health
curl https://nodo.host/colombia-unida/api/ready
curl -s https://nodo.host/colombia-unida/api/public/v1/impact | head -c 300
```

- `/api/health` → `{"status":"ok"}` (el proceso está vivo)
- `/api/ready` → base de datos respondiendo
- `/api/public/v1/impact` → métricas agregadas (las semillas corrieron)
- `https://nodo.host/colombia-unida/` → feed público
- `https://nodo.host/colombia-unida/consola/` → consola interna
  (demo: `supervisor@colombiaunida.demo` / `Demo1234!`)

## 5. Después del primer despliegue

- Antes de que entren datos reales de un incidente activo: confirmar que
  `RUN_SEEDS` y `SEED_DEMO` estén en `0`, y borrar el escenario sintético. El
  seed de demostración crea personas, casos y usuarios de prueba; no debe
  convivir con datos reales.
- Cambiar la contraseña de los tres usuarios de demostración, o desactivarlos.
- Programar el respaldo diario (`pg_dump`) en la instancia del VPS y guardar
  aparte las claves de cifrado — ver `base-de-datos-vps.md`. Un respaldo sin
  `APP_ENCRYPTION_KEY` deja los campos sensibles ilegibles.
