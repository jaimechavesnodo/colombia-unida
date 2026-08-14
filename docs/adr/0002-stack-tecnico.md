# ADR-0002 — Stack: SQLAlchemy síncrono, SPAs Vite, TOTP en vez de OIDC

**Fecha:** 2026-08-14 · **Estado:** aceptada

## SQLAlchemy 2 síncrono (psycopg3) en vez de async

Los patrones críticos del alcance (reservas con `SELECT FOR UPDATE`,
outbox en la misma transacción, optimistic locking) son más simples y menos
propensos a error en código síncrono. FastAPI ejecuta endpoints sync en
threadpool; a escala piloto (≤10k mensajes/día, §17.1) el throughput sobra.
Si una etapa regional lo exige, la migración a async es localizada.

## Frontends: Vite + React SPA en vez de Next.js

El doc sugiere Next.js. En el VPS no hay CDN ni edge; SSR no aporta y añade
un runtime Node en producción. Dos SPAs estáticas servidas por nginx
(principio NODO: la solución más simple) cumplen los requisitos. El Impact
Feed compartible con preview seguro se resuelve con meta tags estáticas del
shell y datos de `/public/v1`.

## Auth de consola: email+password + TOTP MFA en vez de OIDC/IdP

No hay IdP corporativo disponible en esta fase. Se implementa MFA TOTP
obligatoria, sesiones cortas JWT, RBAC+ABAC en backend (deny by default),
igual que exige §13.2. Si un aliado exige SSO en fase regional, se añade
OIDC sin cambiar el modelo de permisos.
