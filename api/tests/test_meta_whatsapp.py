"""Pruebas de la integración Meta WhatsApp Cloud API.

Sin red ni base de datos: fixtures JSON locales y httpx.MockTransport.
"""

import hashlib
import hmac
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from app.integrations.meta_whatsapp import client as client_module
from app.integrations.meta_whatsapp.client import GraphApiError, GraphClient
from app.integrations.meta_whatsapp.parser import parse_webhook
from app.integrations.meta_whatsapp.signature import verify_signature

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "meta"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


# ── signature ──────────────────────────────────────────────────────────

APP_SECRET = "un-app-secret-de-prueba"
RAW_BODY = b'{"object":"whatsapp_business_account","entry":[{"id":"222333444555666"}]}'


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class TestVerifySignature:
    def test_valid_signature_passes(self):
        header = sign(APP_SECRET, RAW_BODY)
        assert verify_signature(APP_SECRET, RAW_BODY, header) is True

    def test_wrong_secret_fails(self):
        header = sign("otro-secreto", RAW_BODY)
        assert verify_signature(APP_SECRET, RAW_BODY, header) is False

    def test_tampered_body_fails(self):
        header = sign(APP_SECRET, RAW_BODY)
        assert verify_signature(APP_SECRET, RAW_BODY + b"x", header) is False

    def test_missing_header_fails(self):
        assert verify_signature(APP_SECRET, RAW_BODY, None) is False
        assert verify_signature(APP_SECRET, RAW_BODY, "") is False

    @pytest.mark.parametrize(
        "header",
        [
            "sha256",  # sin '='
            "sha256=",  # hex vacío
            "md5=abcdef",  # esquema incorrecto
            "sha1=" + "a" * 40,
            "sha256=zzzz-no-es-hex",
            "Bearer abcdef",
            "sha256=ñÑ€",  # no-ascii: compare_digest lanzaría sin el guard
        ],
    )
    def test_malformed_header_fails(self, header):
        assert verify_signature(APP_SECRET, RAW_BODY, header) is False

    def test_empty_secret_fails(self):
        header = sign(APP_SECRET, RAW_BODY)
        assert verify_signature("", RAW_BODY, header) is False


# ── parser ─────────────────────────────────────────────────────────────


class TestParser:
    def test_text_message(self):
        parsed = parse_webhook(load_fixture("text_message.json"))
        assert parsed.phone_number_id == "111222333444555"
        assert parsed.statuses == []
        assert parsed.unknown_events == []
        assert len(parsed.messages) == 1
        msg = parsed.messages[0]
        assert msg.wa_id == "573000002222"
        assert msg.profile_name == "Maria Prueba"
        assert msg.message_type == "text"
        assert msg.text == "Hola, necesito ayuda con un reporte"
        assert msg.provider_message_id.startswith("wamid.")
        assert msg.media_id is None
        assert msg.latitude is None
        assert msg.timestamp == datetime.fromtimestamp(1723600000, tz=UTC)
        assert msg.reply_to_provider_message_id is None
        assert msg.raw["type"] == "text"

    def test_audio_message(self):
        parsed = parse_webhook(load_fixture("audio_message.json"))
        msg = parsed.messages[0]
        assert msg.message_type == "audio"
        assert msg.media_id == "1234567890audio"
        assert msg.media_mime_type == "audio/ogg; codecs=opus"
        assert msg.text is None

    def test_image_message_with_caption(self):
        parsed = parse_webhook(load_fixture("image_message.json"))
        msg = parsed.messages[0]
        assert msg.message_type == "image"
        assert msg.media_id == "1234567890image"
        assert msg.media_mime_type == "image/jpeg"
        assert msg.text == "Foto del puente caido"

    def test_location_message(self):
        parsed = parse_webhook(load_fixture("location_message.json"))
        msg = parsed.messages[0]
        assert msg.message_type == "location"
        assert msg.latitude == pytest.approx(4.60971)
        assert msg.longitude == pytest.approx(-74.08175)
        assert msg.media_id is None

    def test_interactive_reply(self):
        parsed = parse_webhook(load_fixture("interactive_reply.json"))
        msg = parsed.messages[0]
        assert msg.message_type == "interactive"
        assert msg.text == "btn_confirmar_caso"
        assert msg.reply_to_provider_message_id is not None
        assert msg.reply_to_provider_message_id.startswith("wamid.")

    def test_status_delivered(self):
        parsed = parse_webhook(load_fixture("status_delivered.json"))
        assert parsed.messages == []
        assert len(parsed.statuses) == 1
        status = parsed.statuses[0]
        assert status.status == "delivered"
        assert status.recipient_wa_id == "573000002222"
        assert status.provider_message_id.startswith("wamid.")
        assert status.timestamp == datetime.fromtimestamp(1723600300, tz=UTC)
        assert status.error_code is None

    def test_status_failed_carries_error_code(self):
        parsed = parse_webhook(load_fixture("status_failed.json"))
        status = parsed.statuses[0]
        assert status.status == "failed"
        assert status.error_code == "131047"
        assert status.recipient_wa_id == "573000003333"

    def test_unknown_event_goes_to_unknown_events(self):
        parsed = parse_webhook(load_fixture("unknown_event.json"))
        assert parsed.messages == []
        assert parsed.statuses == []
        assert len(parsed.unknown_events) == 1
        assert parsed.unknown_events[0]["field"] == "message_template_status_update"

    def test_unknown_message_type_is_normalized(self):
        payload = load_fixture("text_message.json")
        message = payload["entry"][0]["changes"][0]["value"]["messages"][0]
        message["type"] = "sticker"
        message.pop("text")
        parsed = parse_webhook(payload)
        assert len(parsed.messages) == 1
        assert parsed.messages[0].message_type == "unknown"
        assert parsed.messages[0].text is None

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"object": "whatsapp_business_account"},
            {"entry": "no-es-lista"},
            {"entry": [None, 42, "x"]},
            {"entry": [{"changes": "no-es-lista"}]},
            {"entry": [{"changes": [None, {"field": "messages", "value": "no-es-dict"}]}]},
            {"entry": [{"changes": [{"field": "messages", "value": {"messages": [None, 7]}}]}]},
        ],
    )
    def test_malformed_payload_never_raises(self, payload):
        parsed = parse_webhook(payload)
        assert parsed.messages == []
        assert parsed.statuses == []

    def test_bad_timestamp_falls_back_to_now(self):
        payload = load_fixture("text_message.json")
        payload["entry"][0]["changes"][0]["value"]["messages"][0]["timestamp"] = "no-numerico"
        parsed = parse_webhook(payload)
        delta = abs((datetime.now(UTC) - parsed.messages[0].timestamp).total_seconds())
        assert delta < 5


# ── client ─────────────────────────────────────────────────────────────

TOKEN = "token-de-prueba"
PHONE_NUMBER_ID = "111222333444555"
API_VERSION = "v23.0"


def make_client(handler) -> GraphClient:
    return GraphClient(
        access_token=TOKEN,
        phone_number_id=PHONE_NUMBER_ID,
        api_version=API_VERSION,
        transport=httpx.MockTransport(handler),
    )


def send_response(message_id: str = "wamid.SALIENTE0001") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "messaging_product": "whatsapp",
            "contacts": [{"input": "573000002222", "wa_id": "573000002222"}],
            "messages": [{"id": message_id}],
        },
    )


class TestGraphClient:
    def test_send_text_builds_correct_request(self):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["auth"] = request.headers.get("Authorization")
            seen["content_type"] = request.headers.get("Content-Type")
            seen["body"] = json.loads(request.content)
            return send_response("wamid.TEXTO0001")

        with make_client(handler) as client:
            message_id = client.send_text("573000002222", "Hola desde Colombia Unida")

        assert message_id == "wamid.TEXTO0001"
        assert seen["url"] == (
            f"https://graph.facebook.com/{API_VERSION}/{PHONE_NUMBER_ID}/messages"
        )
        assert seen["auth"] == f"Bearer {TOKEN}"
        assert seen["content_type"] == "application/json"
        assert seen["body"] == {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": "573000002222",
            "type": "text",
            "text": {"preview_url": False, "body": "Hola desde Colombia Unida"},
        }

    def test_send_text_with_reply_context(self):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            return send_response()

        with make_client(handler) as client:
            client.send_text("573000002222", "Respuesta", reply_to="wamid.ORIGINAL")

        assert seen["body"]["context"] == {"message_id": "wamid.ORIGINAL"}

    def test_send_template_builds_correct_request(self):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            return send_response("wamid.TEMPLATE0001")

        components = [{"type": "body", "parameters": [{"type": "text", "text": "CU-0042"}]}]
        with make_client(handler) as client:
            message_id = client.send_template(
                "573000002222", "confirmacion_caso", components=components
            )

        assert message_id == "wamid.TEMPLATE0001"
        assert seen["body"]["type"] == "template"
        assert seen["body"]["template"] == {
            "name": "confirmacion_caso",
            "language": {"code": "es_CO"},
            "components": components,
        }

    def test_graph_api_error_raised_on_meta_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                400,
                json={
                    "error": {
                        "message": "(#131030) Recipient phone number not in allowed list",
                        "type": "OAuthException",
                        "code": 131030,
                        "fbtrace_id": "AbC123",
                    }
                },
            )

        with make_client(handler) as client, pytest.raises(GraphApiError) as excinfo:
            client.send_text("573000004444", "Hola")

        assert excinfo.value.status_code == 400
        assert excinfo.value.error_code == "131030"
        assert "131030" in excinfo.value.message

    def test_send_retries_once_on_500(self):
        calls = {"count": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["count"] += 1
            if calls["count"] == 1:
                return httpx.Response(500, json={"error": {"code": 1, "message": "Interno"}})
            return send_response("wamid.REINTENTO0001")

        with make_client(handler) as client:
            message_id = client.send_text("573000002222", "Hola")

        assert calls["count"] == 2
        assert message_id == "wamid.REINTENTO0001"

    def test_send_fails_after_second_500(self):
        calls = {"count": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["count"] += 1
            return httpx.Response(500, json={"error": {"code": 2, "message": "Interno"}})

        with make_client(handler) as client, pytest.raises(GraphApiError) as excinfo:
            client.send_text("573000002222", "Hola")

        assert calls["count"] == 2
        assert excinfo.value.status_code == 500

    def test_get_media_url(self):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["auth"] = request.headers.get("Authorization")
            return httpx.Response(
                200,
                json={
                    "url": "https://lookaside.fbsbx.com/whatsapp_business/attachments/?mid=abc",
                    "mime_type": "audio/ogg",
                    "sha256": "cafe",
                    "file_size": 1024,
                    "id": "1234567890audio",
                    "messaging_product": "whatsapp",
                },
            )

        with make_client(handler) as client:
            url, mime_type = client.get_media_url("1234567890audio")

        assert seen["url"] == f"https://graph.facebook.com/{API_VERSION}/1234567890audio"
        assert seen["auth"] == f"Bearer {TOKEN}"
        assert url.startswith("https://lookaside.fbsbx.com/")
        assert mime_type == "audio/ogg"

    def test_download_media_success_sends_auth(self):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["auth"] = request.headers.get("Authorization")
            return httpx.Response(200, content=b"contenido-binario-ogg")

        with make_client(handler) as client:
            data = client.download_media("https://lookaside.fbsbx.com/whatsapp/abc")

        assert data == b"contenido-binario-ogg"
        assert seen["auth"] == f"Bearer {TOKEN}"

    def test_download_media_rejects_oversize_content_length(self, monkeypatch):
        monkeypatch.setattr(client_module, "MAX_MEDIA_BYTES", 10)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"x" * 20)  # Content-Length: 20 > 10

        with make_client(handler) as client, pytest.raises(ValueError, match="excede"):
            client.download_media("https://lookaside.fbsbx.com/whatsapp/abc")

    def test_download_media_rejects_oversize_stream(self, monkeypatch):
        monkeypatch.setattr(client_module, "MAX_MEDIA_BYTES", 10)

        def handler(request: httpx.Request) -> httpx.Response:
            # Iterador → sin Content-Length: fuerza el chequeo durante el stream
            return httpx.Response(200, content=iter([b"a" * 8, b"b" * 8]))

        with make_client(handler) as client, pytest.raises(ValueError, match="excede"):
            client.download_media("https://lookaside.fbsbx.com/whatsapp/abc")

    def test_token_never_in_url(self):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return send_response()

        with make_client(handler) as client:
            client.send_text("573000002222", "Hola")

        assert TOKEN not in seen["url"]
