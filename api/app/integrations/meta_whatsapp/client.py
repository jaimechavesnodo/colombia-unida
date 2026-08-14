"""Cliente saliente de Meta WhatsApp Cloud API (Graph API).

Reglas §18.1: el token viaja solo en el header Authorization (nunca en URL
ni en logs) y los logs no contienen teléfonos ni cuerpos de mensajes —
solo IDs opacos del proveedor y códigos de estado.
"""

import logging

import httpx

from app.core.config import get_settings
from app.core.logging import log_ctx

logger = logging.getLogger(__name__)

MAX_MEDIA_BYTES = 25 * 1024 * 1024  # límite duro de descarga de media
_TIMEOUT_SECONDS = 15.0


class GraphApiError(Exception):
    """Error devuelto por Graph API (error.code / error.message del body)."""

    def __init__(self, status_code: int, error_code: str | None, message: str):
        super().__init__(f"Graph API {status_code} (code={error_code}): {message}")
        self.status_code = status_code
        self.error_code = error_code
        self.message = message


class GraphClient:
    """Cliente síncrono sobre https://graph.facebook.com/{version}."""

    def __init__(
        self,
        access_token: str | None = None,
        phone_number_id: str | None = None,
        api_version: str | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout: float = _TIMEOUT_SECONDS,
    ):
        settings = get_settings()
        self._phone_number_id = (
            phone_number_id if phone_number_id is not None else settings.meta_phone_number_id
        )
        token = access_token if access_token is not None else settings.meta_access_token
        version = api_version if api_version is not None else settings.meta_graph_api_version
        self._client = httpx.Client(
            base_url=f"https://graph.facebook.com/{version}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
            transport=transport,
        )

    # ── ciclo de vida ──────────────────────────────────────────────────

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "GraphClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ── envíos ─────────────────────────────────────────────────────────

    def send_text(self, to_wa_id: str, body: str, reply_to: str | None = None) -> str:
        """Envía un mensaje de texto libre; devuelve el message id del proveedor."""
        payload: dict = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to_wa_id,
            "type": "text",
            "text": {"preview_url": False, "body": body},
        }
        if reply_to:
            payload["context"] = {"message_id": reply_to}
        return self._post_messages(payload)

    def send_template(
        self,
        to_wa_id: str,
        template_name: str,
        language: str = "es_CO",
        components: list | None = None,
    ) -> str:
        """Envía una plantilla aprobada; devuelve el message id del proveedor."""
        template: dict = {"name": template_name, "language": {"code": language}}
        if components:
            template["components"] = components
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to_wa_id,
            "type": "template",
            "template": template,
        }
        return self._post_messages(payload)

    # ── media ──────────────────────────────────────────────────────────

    def get_media_url(self, media_id: str) -> tuple[str, str]:
        """GET /{media_id} → (url firmada temporal, mime_type)."""
        response = self._client.get(f"/{media_id}")
        if response.status_code >= 400:
            self._raise_from_response(response)
        data = response.json()
        url = data.get("url")
        mime_type = data.get("mime_type")
        if not url or not mime_type:
            raise GraphApiError(response.status_code, None, "Respuesta de media sin url/mime_type")
        return str(url), str(mime_type)

    def download_media(self, url: str) -> bytes:
        """Descarga la media autenticada; ValueError si excede MAX_MEDIA_BYTES."""
        with self._client.stream("GET", url) as response:
            if response.status_code >= 400:
                response.read()
                self._raise_from_response(response)
            length = response.headers.get("Content-Length")
            if length and length.isdigit() and int(length) > MAX_MEDIA_BYTES:
                raise ValueError("La media excede el límite de 25MB (Content-Length)")
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > MAX_MEDIA_BYTES:
                    raise ValueError("La media excede el límite de 25MB (stream)")
                chunks.append(chunk)
        return b"".join(chunks)

    # ── internos ───────────────────────────────────────────────────────

    def _post_messages(self, payload: dict) -> str:
        response = self._send_with_retry("POST", f"/{self._phone_number_id}/messages", json=payload)
        if response.status_code >= 400:
            self._raise_from_response(response)
        try:
            message_id = str(response.json()["messages"][0]["id"])
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise GraphApiError(
                response.status_code, None, "Respuesta de envío sin message id"
            ) from exc
        log_ctx(
            logger,
            logging.INFO,
            "whatsapp.message_sent",
            provider_message_id=message_id,
            status_code=response.status_code,
        )
        return message_id

    def _send_with_retry(self, method: str, url: str, **kwargs) -> httpx.Response:
        """Un reintento ante 5xx o timeout (solo envíos)."""
        last_exc: httpx.TimeoutException | None = None
        response: httpx.Response | None = None
        for attempt in range(2):
            try:
                response = self._client.request(method, url, **kwargs)
            except httpx.TimeoutException as exc:
                last_exc = exc
                response = None
                continue
            if response.status_code >= 500 and attempt == 0:
                log_ctx(
                    logger,
                    logging.WARNING,
                    "whatsapp.send_retry",
                    status_code=response.status_code,
                )
                continue
            break
        if response is None:
            raise GraphApiError(504, None, "Timeout al contactar Graph API") from last_exc
        return response

    def _raise_from_response(self, response: httpx.Response) -> None:
        try:
            error = response.json().get("error") or {}
        except ValueError:
            error = {}
        error_code = str(error["code"]) if error.get("code") is not None else None
        message = str(error.get("message") or "Graph API request failed")
        log_ctx(
            logger,
            logging.WARNING,
            "whatsapp.graph_error",
            status_code=response.status_code,
            error_code=error_code,
        )
        raise GraphApiError(response.status_code, error_code, message)
