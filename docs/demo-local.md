# Levantar la demo con datos de prueba

Cinco comandos y tienes el sistema corriendo con un escenario completo:
15 casos en distintos estados, 33 necesidades, 5 donantes, matching,
una asignación reservada, 8 casos publicados en el Impact Feed y el
Transparency Dashboard con supresión de privacidad activa.

> ⚠️ Los datos son **100% sintéticos**. Ningún nombre, teléfono o caso
> corresponde a una persona real. No cargues este seed en un entorno con
> datos reales de un incidente activo.

## 1. Base de datos

Con PostgreSQL 16 + PostGIS disponible (local o el contenedor de
`docker-compose.yml`). Para PostgreSQL local en el puerto 5433:

```bash
createdb -h localhost -p 5433 -U colombia_unida colombia_unida_demo
psql -h localhost -p 5433 -U colombia_unida -d colombia_unida_demo -c "CREATE EXTENSION postgis;"
```

## 2. Variables de entorno

```bash
cd api
cat > .env <<EOF
APP_ENV=local
DATABASE_URL=postgresql+psycopg://colombia_unida@localhost:5433/colombia_unida_demo
APP_ENCRYPTION_KEY=$(python3 -c "import base64,os;print(base64.b64encode(os.urandom(32)).decode())")
APP_HMAC_KEY=$(python3 -c "import base64,os;print(base64.b64encode(os.urandom(32)).decode())")
JWT_SECRET=$(python3 -c "import secrets;print(secrets.token_urlsafe(32))")
STT_PROVIDER=disabled
EOF
```

Las claves quedan fijas en `.env` (ignorado por git): si las regeneras,
los datos cifrados existentes dejan de poder descifrarse.

## 3. Migraciones y datos

```bash
.venv/bin/alembic upgrade head     # 63 tablas
.venv/bin/python -m app.seeds      # roles, incidente, 1122 municipios DANE, catálogo v1
.venv/bin/python -m app.seeds.demo # escenario de demostración
```

El seed de demo es **idempotente**: si lo corres de nuevo no duplica
casos, solo refresca las proyecciones públicas y los snapshots.

## 4. API

```bash
.venv/bin/uvicorn app.main:app --port 8099
```

Verificación rápida:

```bash
curl -s localhost:8099/public/v1/impact | python3 -m json.tool
curl -s "localhost:8099/public/v1/feed?limit=3" | python3 -m json.tool
```

## 5. Interfaz pública

```bash
cd ../apps/web && npm install && npm run dev -- --port 5199
```

Abre **http://localhost:5199/colombia-unida/**

El proxy de Vite enruta `/colombia-unida/api/*` al servicio de la API,
igual que hace nginx en producción — así el navegador siempre habla con
un solo origen y no hay CORS en ninguna de las dos configuraciones.

---

## Qué se puede mostrar en la demo

| Pantalla / comando | Qué demuestra |
|---|---|
| Pestaña **Casos que necesitan ayuda** | Impact Feed: casos consentidos y aprobados, ubicación gruesa (municipio/departamento), banda de tamaño de hogar, progreso de cobertura, hitos, CTA de ayuda |
| Filtros **Mayor brecha** / departamento | Filtros seguros del feed: nunca por ubicación exacta |
| Pestaña **Transparencia** | KPIs (recibidos/verificados/servidos), necesidades y cobertura por horizonte, categorías más pedidas, casos por municipio, definición de cada métrica y fecha de corte |
| Filas *"dato suprimido"* | Supresión de celdas con menos de 5 casos: el dashboard no permite reidentificar hogares en zonas poco pobladas |
| `curl /public/v1/feed \| grep 57300` | **No hay PII**: ni teléfonos, ni nombres, ni narrativas, ni coordenadas. El plano público lee solo proyecciones aprobadas, nunca las tablas protegidas |
| `pytest` (111 pruebas) | Criterios de aceptación §19.2 verificados: WA-01/02/03, CASE, AI-01/02, MATCH-01/02 (incluida concurrencia real de reservas) |

## Simular una conversación de WhatsApp sin Meta

Las pruebas E2E hacen exactamente esto: firman un webhook con
`META_APP_SECRET` y lo envían al endpoint real. Para verlo en vivo:

```bash
TEST_DATABASE_URL=postgresql+psycopg://colombia_unida@localhost:5433/colombia_unida_test \
  .venv/bin/pytest tests/test_whatsapp_flow.py -v
```

Recorre el flujo completo del §5.1: «Hola» → intención → consentimientos
→ código de caso `CU-XXXXXX` → narrativa → municipio → tamaño de hogar →
envío, más «completar después», handoff a agente y flujo de donante.
