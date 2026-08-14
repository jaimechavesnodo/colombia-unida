# Despliegue en EasyPanel — proyecto `colombia-unida`

Configuración inicial (una sola vez). Convención NODO: proyecto en EasyPanel +
servicios, dominio `nodo.host/colombia-unida`, auto-deploy por webhook de
GitHub.

**Los valores reales de las variables ya están generados** en
`deploy/easypanel.env.local` (ignorado por git). Ese archivo es la fuente para
copiar y pegar en EasyPanel; guarda una copia en el vault de NODO. Si se
pierden `APP_ENCRYPTION_KEY` y `APP_HMAC_KEY`, los campos cifrados de la base
quedan ilegibles y no hay manera de recuperarlos.

## Para esta primera versión bastan 4 servicios

`db`, `api`, `worker` y `web`. **MinIO y ClamAV no hacen falta todavía**: solo
entran en juego cuando llegue media por WhatsApp, y eso depende de la WABA
productiva (ver `waba-setup.md`). La API arranca sin ellos.

## 1. Crear proyecto

- EasyPanel → **+** → Crear proyecto → nombre: `colombia-unida`

## 2. Servicios

### 2.1 `db` (Servicio → Imagen Docker)

- Imagen: `postgis/postgis:16-3.4`
- Entorno: las tres variables `POSTGRES_*` del archivo generado
- Almacenamiento: volumen → `/var/lib/postgresql/data`
- Sin dominio público

No hay que crear la extensión PostGIS a mano: la primera migración ejecuta
`CREATE EXTENSION IF NOT EXISTS postgis`.

### 2.2 `api` (Servicio → App → GitHub)

- Propietario: `jaimechavesnodo` · Repo: `colombia-unida` · Rama: `main`
- Ruta de compilación: `/api` · Compilación: **Dockerfile**
- Entorno: todo el bloque «api y worker» del archivo generado,
  **con `RUN_SEEDS=1` y `SEED_DEMO=1`** en el primer despliegue
- Sin dominio público directo (entra por el nginx de `web`)
- Avanzado: réplicas `1`

El contenedor aplica `alembic upgrade head` al arrancar y, con esas dos
banderas, siembra el catálogo, la geografía DANE, los roles y el escenario de
demostración. Si la migración falla, el contenedor no arranca — es a propósito:
mejor un servicio caído y visible que una API sirviendo contra un esquema
equivocado.

### 2.3 `worker` (Servicio → App → GitHub)

- Igual que `api` (mismo repo, misma ruta `/api`, mismo Dockerfile)
- **Comando de arranque:** `python -m app.worker`
- Mismas variables que `api` pero con `RUN_SEEDS=0` y `SEED_DEMO=0`
  (que dos contenedores siembren a la vez es una carrera innecesaria)

### 2.4 `web` (Servicio → App → GitHub)

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

- Poner `RUN_SEEDS=0` y `SEED_DEMO=0` en `api` **antes** de que entren datos
  reales de un incidente activo. El seed de demostración crea personas, casos y
  usuarios sintéticos; no debe correr junto a datos reales.
- Cambiar la contraseña de los tres usuarios de demostración, o desactivarlos.
- Programar el respaldo diario de la base (`pg_dump`) — pendiente de M10.
