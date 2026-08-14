"""Extracción estructurada con Claude (§9.1).

Reglas del alcance:
- JSON Schema versionado; salida que no valide contra el schema se rechaza.
- No inventar cantidades ni identidades (must_not_infer explícito).
- catalog_code solo si mapea claramente a un código del catálogo activo;
  lo demás va como free_text con catalog_code null.
- unknowns explícitos: lo que el texto no dice, no se rellena.
- Prohibido loggear el texto de entrada o la salida; solo metadatos.

La política de modelo (§17.4) es "modelo pequeño primero" con presupuesto
por incidente; el modelo llega por settings.anthropic_model.
"""

import json
import logging
import time

import anthropic

from app.core.config import get_settings
from app.core.logging import log_ctx

logger = logging.getLogger("integrations.llm")

CASE_INTAKE_SCHEMA_VERSION = "case_intake.v1"
OFFER_INTAKE_SCHEMA_VERSION = "offer_intake.v1"
PROMPT_VERSION = "v1"

MAX_TOKENS = 2048

NEED_HORIZONS = ["EMERGENCY", "RECOVERY", "RECONSTRUCTION"]


class LlmError(Exception):
    """Fallo del proveedor LLM o salida inválida."""


class LlmUnavailable(LlmError):
    """LLM sin credenciales; procede captura manual (AI-02)."""


def _confident(value_type: str) -> dict:
    """Campo {value, confidence} con value anulable (nunca inventar)."""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["value", "confidence"],
        "properties": {
            "value": {"type": [value_type, "null"]},
            "confidence": {"type": "number"},
        },
    }


CASE_INTAKE_JSON_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "reporter_is_affected",
        "household",
        "location",
        "damage_summary",
        "needs",
        "unknowns",
        "safety_flags",
    ],
    "properties": {
        "reporter_is_affected": _confident("boolean"),
        "household": {
            "type": "object",
            "additionalProperties": False,
            "required": ["member_count"],
            "properties": {"member_count": _confident("integer")},
        },
        "location": {
            "type": "object",
            "additionalProperties": False,
            "required": ["municipality_text"],
            "properties": {"municipality_text": _confident("string")},
        },
        "damage_summary": _confident("string"),
        "needs": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["catalog_code", "free_text", "horizon", "quantity", "confidence"],
                "properties": {
                    "catalog_code": {"type": ["string", "null"]},
                    "free_text": {"type": "string"},
                    "horizon": {"type": ["string", "null"], "enum": [*NEED_HORIZONS, None]},
                    "quantity": {"type": ["number", "null"]},
                    "confidence": {"type": "number"},
                },
            },
        },
        "unknowns": {"type": "array", "items": {"type": "string"}},
        "safety_flags": {"type": "array", "items": {"type": "string"}},
    },
}

OFFER_INTAKE_JSON_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "offer_type",
        "items",
        "origin_municipality_text",
        "availability_text",
        "unknowns",
    ],
    "properties": {
        "offer_type": {"type": ["string", "null"]},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["catalog_code", "free_text", "quantity", "unit", "confidence"],
                "properties": {
                    "catalog_code": {"type": ["string", "null"]},
                    "free_text": {"type": "string"},
                    "quantity": {"type": ["number", "null"]},
                    "unit": {"type": ["string", "null"]},
                    "confidence": {"type": "number"},
                },
            },
        },
        "origin_municipality_text": {"type": ["string", "null"]},
        "availability_text": {"type": ["string", "null"]},
        "unknowns": {"type": "array", "items": {"type": "string"}},
    },
}

_TASKS = {
    "case": (CASE_INTAKE_SCHEMA_VERSION, CASE_INTAKE_JSON_SCHEMA),
    "offer": (OFFER_INTAKE_SCHEMA_VERSION, OFFER_INTAKE_JSON_SCHEMA),
}

_SYSTEM_TEMPLATE = """\
Eres un extractor de datos para intake humanitario en Colombia. Recibes el texto \
de una persona que reporta una necesidad ({task_label}) y devuelves SOLO el JSON \
del schema indicado.

Reglas estrictas (must_not_infer):
- Extrae únicamente lo que el texto dice de forma explícita. NUNCA infieras \
identidades, nombres, documentos, teléfonos, diagnósticos médicos ni cantidades \
que no estén dichas. Si un dato no está, su value es null.
- No inventes cantidades: quantity solo si la persona dio un número.
- catalog_code solo si el ítem mapea claramente a UNO de estos códigos del \
catálogo; en cualquier otro caso usa null y conserva la descripción en free_text:
{catalog_codes}
- Lista en unknowns los datos relevantes que el texto NO menciona.
- safety_flags: señala solo riesgos evidentes en el texto (p. ej. MEDICAL, \
MINOR, VIOLENCE).
- confidence entre 0 y 1 según qué tan explícito es el dato en el texto.\
{municipalities_hint}"""


def _build_system_prompt(
    task: str, catalog_codes: list[str], municipalities_hint: list[str] | None
) -> str:
    hint = ""
    if municipalities_hint:
        hint = (
            "\n- Municipios probables (solo como referencia de escritura, no "
            "asumas ninguno): " + ", ".join(municipalities_hint)
        )
    label = "caso de ayuda" if task == "case" else "oferta de ayuda"
    return _SYSTEM_TEMPLATE.format(
        task_label=label,
        catalog_codes=", ".join(catalog_codes),
        municipalities_hint=hint,
    )


def _create_message(client: anthropic.Anthropic, schema: dict, **kwargs):
    """Compat SDK: output_config como kwarg directo o vía extra_body."""
    output_config = {"format": {"type": "json_schema", "schema": schema}}
    try:
        return client.messages.create(output_config=output_config, **kwargs)
    except TypeError:
        return client.messages.create(extra_body={"output_config": output_config}, **kwargs)


def _first_text_block(response) -> str:
    for block in getattr(response, "content", []) or []:
        text = getattr(block, "text", None)
        if text is not None:
            return text
    raise LlmError("llm response had no text block")


def extract(
    task: str,
    text: str,
    catalog_codes: list[str],
    municipalities_hint: list[str] | None = None,
) -> dict:
    """Extrae campos estructurados del texto para task "case" u "offer".

    Lanza LlmUnavailable sin api key; LlmError ante fallos de API o salida
    no parseable. Reintenta una vez ante 5xx/rate-limit/conexión.
    """
    if task not in _TASKS:
        raise ValueError(f"unknown extraction task: {task}")
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise LlmUnavailable("anthropic api key not configured")

    schema_version, schema = _TASKS[task]
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    system_prompt = _build_system_prompt(task, catalog_codes, municipalities_hint)

    started = time.monotonic()
    last_error: Exception | None = None
    response = None
    for attempt in range(2):
        try:
            response = _create_message(
                client,
                schema,
                model=settings.anthropic_model,
                max_tokens=MAX_TOKENS,
                system=system_prompt,
                messages=[{"role": "user", "content": text}],
            )
            break
        except anthropic.RateLimitError as exc:
            last_error = exc
        except anthropic.APIStatusError as exc:
            if exc.status_code < 500:
                _log_extract(task, settings.anthropic_model, started, "FAILED")
                raise LlmError(f"llm api error {exc.status_code}") from exc
            last_error = exc
        except anthropic.APIConnectionError as exc:
            last_error = exc
        if attempt == 0:
            continue
    if response is None:
        _log_extract(task, settings.anthropic_model, started, "FAILED")
        raise LlmError(f"llm request failed: {type(last_error).__name__}") from last_error

    raw = _first_text_block(response)
    try:
        data = json.loads(raw)
    except ValueError as exc:
        _log_extract(task, settings.anthropic_model, started, "FAILED")
        raise LlmError(f"llm output is not valid json for {schema_version}") from exc
    if not isinstance(data, dict):
        _log_extract(task, settings.anthropic_model, started, "FAILED")
        raise LlmError(f"llm output is not an object for {schema_version}")

    _log_extract(task, settings.anthropic_model, started, "SUCCEEDED")
    return data


def _log_extract(task: str, model: str, started: float, status: str) -> None:
    log_ctx(
        logger, logging.INFO if status == "SUCCEEDED" else logging.WARNING,
        "llm extract",
        task=task, model=model,
        latency_ms=int((time.monotonic() - started) * 1000), status=status,
    )
