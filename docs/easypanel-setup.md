# Despliegue en EasyPanel — proyecto `colombia-unida`

Checklist de configuración inicial (una sola vez). Sigue la convención NODO:
proyecto en EasyPanel + servicios, dominio `nodo.host/colombia-unida`,
auto-deploy por webhook de GitHub.

## 1. Crear proyecto

- EasyPanel → **+** → Crear proyecto → nombre: `colombia-unida`

## 2. Servicios

### 2.1 `db` (App → Imagen Docker)
- Imagen: `postgis/postgis:16-3.4`
- Entorno:
  - `POSTGRES_DB=colombia_unida`
  - `POSTGRES_USER=colombia_unida`
  - `POSTGRES_PASSWORD=<generar y guardar en vault>`
- Almacenamiento: volumen → `/var/lib/postgresql/data`
- Sin dominio público.

### 2.2 `minio` (App → Imagen Docker)
- Imagen: `minio/minio:latest`
- Comando: `server /data`
- Entorno: `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD` (vault)
- Almacenamiento: volumen → `/data`
- Sin dominio público (los buckets `public` se sirven vía API con URLs firmadas).

### 2.3 `clamav` (App → Imagen Docker)
- Imagen: `clamav/clamav:stable`
- Sin dominio público. Nota: tarda varios minutos en el primer arranque
  (descarga de firmas); darle memoria suficiente (~1 GB).

### 2.4 `api` (App → GitHub)
- Propietario: `jaimechavesnodo` · Repo: `colombia-unida` · Rama: `main`
- Ruta de compilación: `/api` · Compilación: **Dockerfile**
- Entorno: todas las variables de `.env.example` con valores reales
  (`ROOT_PATH=/colombia-unida/api`).
- Sin dominio público directo (entra por el nginx de `web`).

### 2.5 `worker` (App → GitHub)
- Igual que `api` (mismo repo/ruta/Dockerfile) pero **comando de arranque:**
  `python -m app.worker`
- Mismas variables de entorno que `api`.

### 2.6 `web` (App → GitHub)
- Propietario: `jaimechavesnodo` · Repo: `colombia-unida` · Rama: `main`
- Ruta de compilación: `/` · Compilación: **Dockerfile** →
  archivo: `web-server/Dockerfile`
- Dominio: `https://nodo.host/colombia-unida` → destino interno
  `http://colombia-unida_web:80/`
- Avanzado: réplicas 1, cero tiempo de inactividad ✅

## 3. Auto-deploy

1. Copiar la **URL de deploy** de cada servicio GitHub (api, worker, web)
   en EasyPanel y pegarla en el chat con Claude.
2. Claude crea los webhooks en GitHub vía API y verifica la entrega
   (`status: OK`).

## 4. Verificación

- `https://nodo.host/colombia-unida/` → página pública
- `https://nodo.host/colombia-unida/consola/` → consola
- `https://nodo.host/colombia-unida/api/health` → `{"status":"ok"}`
- `https://nodo.host/colombia-unida/api/ready` → `{"status":"ok","db":true}`
