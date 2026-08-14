#!/usr/bin/env python3
"""Crea y configura los servicios de Colombia Unida en EasyPanel vía API.

Idempotente: si un servicio ya existe, actualiza su configuración en vez de
fallar. Se puede correr varias veces sin romper nada.

Lee las credenciales de `deploy/easypanel-api.local` (ignorado por git):

    EASYPANEL_URL, EASYPANEL_TOKEN     acceso al panel
    PG_ADMIN_USER, PG_ADMIN_PASSWORD   superusuario del PostgreSQL compartido
    PG_ADMIN_DB, PG_HOST, PG_PORT      base de mantenimiento y host interno
    CU_DB_PASSWORD                     contraseña del rol de esta aplicación

Las claves de cifrado de la aplicación salen de `deploy/easypanel.env.local`.

Uso:
    python3 deploy/easypanel_provision.py            # configura, no despliega
    python3 deploy/easypanel_provision.py --deploy   # configura y despliega
"""

import argparse
import json
import pathlib
import random
import string
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
PROJECT = "colombia-unida"
OWNER = "jaimechavesnodo"
REPO = "colombia-unida"
REF = "main"
ALFA = string.ascii_lowercase + string.digits


def load_env(path: pathlib.Path) -> dict[str, str]:
    if not path.exists():
        sys.exit(f"Falta {path}")
    out = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


CREDS = load_env(ROOT / "deploy/easypanel-api.local")
APP_ENV = load_env(ROOT / "deploy/easypanel.env.local")
URL = CREDS["EASYPANEL_URL"].rstrip("/")
TOKEN = CREDS["EASYPANEL_TOKEN"]


def call(path: str, payload: dict, method: str = "POST"):
    if method == "GET":
        q = urllib.parse.urlencode({"input": json.dumps({"json": payload})})
        req = urllib.request.Request(
            f"{URL}/api/trpc/{path}?{q}", headers={"Authorization": f"Bearer {TOKEN}"}
        )
    else:
        req = urllib.request.Request(
            f"{URL}/api/trpc/{path}",
            data=json.dumps({"json": payload}).encode(),
            headers={
                "Authorization": f"Bearer {TOKEN}",
                "Content-Type": "application/json",
            },
        )
    # El panel corta handshakes TLS de vez en cuando (aparece como
    # CERTIFICATE_VERIFY_FAILED a mitad de una serie de llamadas que venían
    # funcionando). Un par de reintentos lo resuelve; un 4xx/5xx no se reintenta
    # porque es respuesta legítima del servidor.
    last = ""
    for intento in range(4):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                body = r.read().decode()
                return True, (json.loads(body) if body else {})
        except urllib.error.HTTPError as e:
            return False, e.read().decode()[:300]
        except (urllib.error.URLError, TimeoutError) as e:
            last = str(e)
            time.sleep(2 * (intento + 1))
    return False, f"red: {last}"


def step(label: str, path: str, payload: dict) -> bool:
    ok, res = call(path, payload)
    print(f"  {'ok ' if ok else 'FALLA'} {label}")
    if not ok:
        print(f"      {res}")
    return ok


# ── Variables de entorno de los servicios ──────────────────────────────

DB_USER = "colombia_unida"
DB_NAME = "colombia_unida"
PG = f"{CREDS['PG_HOST']}:{CREDS['PG_PORT']}"

DATABASE_URL = (
    f"postgresql+psycopg://{DB_USER}:{CREDS['CU_DB_PASSWORD']}@{PG}/{DB_NAME}"
)
# URL de administración para que el contenedor cree el rol, la base y PostGIS.
DB_BOOTSTRAP_URL = (
    f"postgresql://{CREDS['PG_ADMIN_USER']}:{CREDS['PG_ADMIN_PASSWORD']}"
    f"@{PG}/{CREDS['PG_ADMIN_DB']}"
)


def api_env(*, seeds: bool) -> str:
    """Entorno de api y worker. Solo la api siembra: dos contenedores sembrando
    a la vez sobre la misma base es una carrera innecesaria."""
    keep = (
        "APP_ENCRYPTION_KEY",
        "APP_HMAC_KEY",
        "AUDIT_SIGNING_KEY",
        "JWT_SECRET",
        "META_GRAPH_API_VERSION",
        "ANTHROPIC_MODEL",
        "STT_PROVIDER",
    )
    lines = [
        "APP_ENV=pilot",
        "PORT=80",
        "ROOT_PATH=/colombia-unida/api",
        "LOG_LEVEL=INFO",
        f"DATABASE_URL={DATABASE_URL}",
        f"DB_BOOTSTRAP_URL={DB_BOOTSTRAP_URL}",
        f"RUN_SEEDS={'1' if seeds else '0'}",
        f"SEED_DEMO={'1' if seeds else '0'}",
    ]
    lines += [f"{k}={APP_ENV[k]}" for k in keep if APP_ENV.get(k)]
    # Pendientes hasta que exista la WABA y las llaves de proveedores.
    lines += [
        "META_WABA_ID=",
        "META_PHONE_NUMBER_ID=",
        "META_ACCESS_TOKEN=",
        "META_APP_SECRET=",
        "META_WEBHOOK_VERIFY_TOKEN=",
        "ANTHROPIC_API_KEY=",
        "OPENAI_API_KEY=",
    ]
    return "\n".join(lines) + "\n"


SERVICES = [
    {
        "name": "api",
        "path": "/api",
        "dockerfile": "Dockerfile",
        "command": None,
        "env": lambda: api_env(seeds=True),
        "domain": None,
    },
    {
        "name": "worker",
        "path": "/api",
        "dockerfile": "Dockerfile",
        "command": "python -m app.worker",
        "env": lambda: api_env(seeds=False),
        "domain": None,
    },
    {
        "name": "web",
        "path": "/",
        "dockerfile": "web-server/Dockerfile",
        "command": None,
        "env": lambda: "",
        # Convención NODO: nodo.host/{proyecto} → el servicio en su :80. Es la
        # misma forma que ya usan crack-tesos y club-tesos.
        "domain": {
            "https": True,
            "host": "nodo.host",
            "path": f"/{PROJECT}",
            "middlewares": [],
            "certificateResolver": "",
            "wildcard": False,
            "destinationType": "service",
            "serviceDestination": {
                "protocol": "http",
                "port": 80,
                "path": "/",
                "projectName": PROJECT,
                "serviceName": "web",
            },
        },
    },
]


def existing_services() -> set[str]:
    ok, res = call("projects.listProjectsAndServices", {}, method="GET")
    if not ok:
        sys.exit(f"No se pudo listar servicios: {res}")
    return {
        s["name"]
        for s in res["json"]["services"]
        if s["projectName"] == PROJECT
    }


def provision(svc: dict, present: set[str]) -> None:
    name = svc["name"]
    print(f"\n▸ {name}")
    if name not in present:
        step("crear servicio", "services.app.createService",
             {"projectName": PROJECT, "serviceName": name})

    base = {"projectName": PROJECT, "serviceName": name}

    step("fuente github", "services.app.updateSourceGithub", {
        **base,
        "owner": OWNER,
        "repo": REPO,
        "ref": REF,
        "path": svc["path"],
        "autoDeploy": True,
    })
    step("build dockerfile", "services.app.updateBuild", {
        **base,
        "build": {"type": "dockerfile", "file": svc["dockerfile"]},
    })
    step("entorno", "services.app.updateEnv", {**base, "env": svc["env"]()})
    step("deploy", "services.app.updateDeploy", {
        **base,
        "deploy": {
            "replicas": 1,
            "command": svc["command"],
            # Sin zero-downtime en api: el arranque corre migraciones y dos
            # instancias a la vez no aportan nada aquí.
            "zeroDowntime": name == "web",
        },
    })
    if svc["domain"]:
        # Idempotencia: si el dominio ya está, no se vuelve a crear (crearlo dos
        # veces deja dos reglas de Traefik para la misma ruta).
        ok, res = call("domains.listDomains", base, method="GET")
        ya = ok and any(
            d["host"] == svc["domain"]["host"] and d["path"] == svc["domain"]["path"]
            for d in res.get("json", [])
        )
        if ya:
            print("  ok  dominio (ya existía)")
        else:
            # El id lo genera el cliente (EasyPanel usa cuid). El servidor solo
            # lo almacena, así que basta con una cadena única de la misma forma.
            dominio = {"id": "c" + "".join(random.choices(ALFA, k=24)), **svc["domain"]}
            step("dominio", "domains.createDomain", {**base, **dominio})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--deploy", action="store_true", help="desplegar al terminar")
    args = ap.parse_args()

    present = existing_services()
    print(f"Servicios ya existentes en {PROJECT}: {sorted(present) or 'ninguno'}")
    for svc in SERVICES:
        provision(svc, present)

    if args.deploy:
        # api primero: crea la base y aplica migraciones antes de que el worker
        # empiece a leer el outbox.
        for svc in SERVICES:
            print(f"\n▸ desplegando {svc['name']}")
            step("deploy", "services.app.deployService",
                 {"projectName": PROJECT, "serviceName": svc["name"]})


if __name__ == "__main__":
    main()
