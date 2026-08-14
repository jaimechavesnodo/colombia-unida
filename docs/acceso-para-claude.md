# Qué necesita Claude para desplegar sin intervención manual

Resumen: hoy Claude puede hacer todo el ciclo de código (editar, probar,
commitear, pushear) y todo el ciclo de base de datos (crear, migrar, sembrar,
verificar). Lo único que no puede es **crear los servicios en EasyPanel**,
porque no tiene acceso al panel.

Hay dos caminos. El primero es el que menos trabajo te deja.

## Opción A — Darle acceso a EasyPanel (recomendada)

EasyPanel expone una API con token. Con eso Claude crea el proyecto, los
servicios, los dominios y las variables de entorno, y verifica el despliegue.

Necesita dos datos:

1. **URL del panel** — la que usas para entrar (algo como
   `https://<host>.easypanel.host` o `https://panel.tudominio`).
2. **Un token de API** — en EasyPanel: *Settings → API Keys → Create*.

**Cómo pasarlos sin que queden en el historial del chat:** pégalos en un
archivo local, que ya está ignorado por git:

```bash
cat > deploy/easypanel-api.local <<'EOF'
EASYPANEL_URL=https://tu-panel.easypanel.host
EASYPANEL_TOKEN=el-token-que-copiaste
EOF
```

Luego dile en el chat: «ya dejé las credenciales en
`deploy/easypanel-api.local`». Claude las lee del archivo y no las imprime.

Sobre el alcance: el token de EasyPanel es de administración y sirve para todo
el panel, no solo para este proyecto. Si prefieres no darlo, la Opción B no lo
necesita.

### Alternativa dentro de la Opción A: acceso SSH

Si prefieres SSH en vez del token del panel, Claude puede trabajar con
`docker`/`docker compose` directamente en el VPS. Funciona, pero los servicios
quedarían fuera de EasyPanel y se pierde la convención de NODO (auto-deploy por
webhook, dominios administrados desde el panel). No lo recomiendo salvo que ya
tengas ese hábito para otros proyectos.

## Opción B — Lo creas tú (unos 10 minutos)

Son **3 servicios**, no 4: la base de datos ya no es un servicio de EasyPanel
porque usamos el PostgreSQL del VPS (ver `base-de-datos-vps.md`).

Antes de tocar el panel, corre el aprovisionamiento de la base:

```bash
export ADMIN_URL='postgresql://postgres:CLAVE@HOST:5432/postgres'
export APP_PASSWORD='clave-para-el-rol-colombia_unida'
SEED_DEMO=1 ./deploy/provision_db.sh
```

Después, en EasyPanel (los valores exactos de las variables están en
`deploy/easypanel.env.local`, ajustando `DATABASE_URL` al que imprimió el
script):

| Servicio | Fuente | Ruta de compilación | Comando | Dominio |
|---|---|---|---|---|
| `api` | GitHub `jaimechavesnodo/colombia-unida`, rama `main` | `/api`, Dockerfile | (el del Dockerfile) | ninguno |
| `worker` | igual que `api` | `/api`, Dockerfile | `python -m app.worker` | ninguno |
| `web` | igual, ruta `/` | Dockerfile `web-server/Dockerfile` | (el del Dockerfile) | `nodo.host/colombia-unida` → `http://colombia-unida_web:80/` |

En `api` deja `RUN_SEEDS=0` y `SEED_DEMO=0` si ya corriste el script de arriba
(sembraría dos veces, y aunque es idempotente, es trabajo repetido en cada
arranque). En `worker`, siempre en `0`.

Cuando estén creados, copia la **URL de deploy** de cada uno (formato
`http://<host>/api/deploy/<token>`) y pégalas en el chat: Claude crea los
webhooks de GitHub y verifica la entrega, y desde ahí cada `git push` a `main`
despliega solo.

## Lo que Claude ya no necesita pedirte

- Repositorio: creado, con CI verde.
- Migraciones: se aplican solas al arrancar el contenedor.
- PostGIS: lo habilita la primera migración y el script de aprovisionamiento.
- Claves de cifrado, HMAC, JWT y contraseña de base: generadas en
  `deploy/easypanel.env.local`.
- Datos de demostración: sembrados por bandera.

## Lo que sigue dependiendo de ti, con o sin acceso

- **La WABA de Meta** (`waba-setup.md`): requiere tu Business Manager y liberar
  la línea de WATI. Claude no puede autenticarse como tú en Meta.
- **`ANTHROPIC_API_KEY` y `OPENAI_API_KEY`** del vault, para la extracción con
  IA y la transcripción de audios.
- **El aliado institucional** que valide en campo y custodie recursos.
- **Revisión jurídica** de los textos de consentimiento y la política de
  tratamiento de datos (Ley 1581).
