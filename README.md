# Colombia Unida — Sistema Humanitario

Infraestructura digital para coordinar ayuda humanitaria con trazabilidad,
confianza y transparencia, tras el terremoto del 10 de agosto de 2026.

**Principio rector:** necesidad → verificación → recurso → asignación → entrega → evidencia → impacto.

Implementa el *Alcance Técnico v1.0* (14-ago-2026) adaptado a la
infraestructura de NODO (VPS + EasyPanel + Docker). Las desviaciones de
plataforma respecto al documento están registradas en [`docs/adr/`](docs/adr/).

## Arquitectura

Monolito modular FastAPI + workers asíncronos sobre PostgreSQL/PostGIS,
con outbox transaccional como bus de eventos, MinIO para media y ClamAV
para escaneo. Dos SPAs (feed público y consola de agentes) servidas por nginx.

| Servicio | Rol |
|---|---|
| `api` | FastAPI: webhook Meta WhatsApp, API interna `/v1`, API pública `/public/v1` |
| `worker` | Consumidores del outbox: conversación, IA, matching, analytics, retención, anclas de auditoría |
| `db` | PostgreSQL 16 + PostGIS — fuente de verdad |
| `minio` | Buckets `quarantine` / `protected` / `public` / `audit` |
| `clamav` | Escaneo antivirus de media entrante |
| `web` | nginx: sirve `apps/web` (público) y `apps/console` (agentes), proxy a `api` |

URL producción: `https://nodo.host/colombia-unida`
Webhook Meta: `https://nodo.host/colombia-unida/api/webhooks/meta/whatsapp`

## Desarrollo local

```bash
# Backend (requiere Python 3.12+)
cd api
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
pytest                      # pruebas sin BD
uvicorn app.main:app --reload --port 8000
```

Con Docker disponible: `docker compose up` levanta el stack completo.
Las pruebas que requieren PostgreSQL corren en CI (contenedor PostGIS) y
se ejecutan localmente solo si `DATABASE_URL` apunta a una BD disponible.

## Variables de entorno

Ver [`.env.example`](.env.example). Nunca commitear valores reales.

## Estado por milestone

| Milestone | Estado |
|---|---|
| M0 Fundación (repo, skeleton, CI, EasyPanel) | ✅ |
| M1 Núcleo de datos (migraciones, seeds, cifrado) | ✅ |
| M2 WhatsApp ingress + conversación | ✅ (sandbox; WABA productiva pendiente de Jaime) |
| M3 Media + IA + confirmación | ✅ |
| M4 Casos, necesidades y validación | pendiente |
| M5 Ofertas, matching y asignación | pendiente |
| M6 Fulfillment y evidencia | pendiente |
| M7 Consola de agentes | pendiente |
| M8 Impact Feed + Transparency Dashboard | pendiente |
| M9 Trust, antifraude y auditoría | pendiente |
| M10 Endurecimiento y operación | pendiente |

## Documentación

- `docs/adr/` — decisiones de arquitectura (desviaciones del alcance v1.0)
- `docs/easypanel-setup.md` — checklist de despliegue en EasyPanel
- `docs/waba-setup.md` — guía de creación de la WABA productiva (tras M2)
