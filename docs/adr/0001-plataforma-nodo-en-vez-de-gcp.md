# ADR-0001 — Plataforma: VPS NODO (EasyPanel/Docker) en vez de Google Cloud

**Fecha:** 2026-08-14 · **Estado:** aceptada

## Contexto

El Alcance Técnico v1.0 (§16) especifica Google Cloud: Cloud Run, Cloud SQL,
Pub/Sub, Cloud Storage, KMS, BigQuery. Jaime definió como restricción del
proyecto que esta primera versión corre exclusivamente sobre la
infraestructura de NODO (VPS Hostinger + EasyPanel + Docker Swarm + Traefik).

## Decisión

Mantener intactos dominio, contratos de API, modelo de datos, máquinas de
estado y criterios de aceptación del documento, sustituyendo solo la capa de
plataforma:

| Doc §16 | Aquí |
|---|---|
| Cloud Run | Contenedores EasyPanel (api, worker, web) |
| Cloud SQL + PostGIS | Contenedor `postgis/postgis:16` + backups programados |
| Pub/Sub + DLQ | Outbox transaccional en PostgreSQL + `FOR UPDATE SKIP LOCKED` + tabla DLQ |
| Cloud Storage | MinIO (S3-compatible), buckets quarantine/protected/public/audit |
| Memorystore | Advisory locks de PostgreSQL (Redis si hiciera falta) |
| BigQuery | Schema `analytics` + snapshots materializados |
| Secret Manager | Variables de entorno EasyPanel |
| Cloud KMS | Clave Ed25519 de aplicación para firmar manifiestos de auditoría |
| Cloud Armor | Traefik + rate limiting en FastAPI |

## Consecuencias

- El monolito modular (ya mandatorio en §1.4) hace la adaptación natural;
  una migración futura a GCP no cambia el código de dominio.
- El outbox como bus (en lugar de Pub/Sub) simplifica la operación y elimina
  una pieza de infraestructura; el doc ya exigía consumidores idempotentes.
- HA/DR quedan limitados a lo que ofrece un solo VPS: backups verificados y
  restore ensayado (DR-01), sin failover regional. Aceptado para el piloto.
