"""Pruebas del pipeline de media (§4.2-7 del alcance).

Unit: sniff_mime, strip_exif, parse_uri, protocolo clamd.
Integración: process_media_asset con storage y GraphClient falsos
(requieren PostgreSQL; se saltan sin servidor).
"""

import uuid
from io import BytesIO

import pytest
import sqlalchemy as sa
from PIL import Image

from app.integrations import clamav
from app.integrations.storage import parse_uri
from app.modules.intake.media_service import sniff_mime, strip_exif

# ── sniff_mime ─────────────────────────────────────────────────────────


def _pad(header: bytes) -> bytes:
    return header + b"\x00" * max(0, 16 - len(header))


def test_sniff_mime_jpeg():
    assert sniff_mime(_pad(b"\xff\xd8\xff\xe0\x00\x10JFIF")) == "image/jpeg"


def test_sniff_mime_png():
    assert sniff_mime(_pad(b"\x89PNG\r\n\x1a\n")) == "image/png"


def test_sniff_mime_ogg():
    assert sniff_mime(_pad(b"OggS\x00\x02")) == "audio/ogg"


def test_sniff_mime_pdf():
    assert sniff_mime(_pad(b"%PDF-1.7")) == "application/pdf"


def test_sniff_mime_wav():
    assert sniff_mime(b"RIFF\x24\x00\x00\x00WAVEfmt ") == "audio/wav"


def test_sniff_mime_webp():
    assert sniff_mime(b"RIFF\x24\x00\x00\x00WEBPVP8 ") == "image/webp"


def test_sniff_mime_mp4():
    assert sniff_mime(b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00") == "video/mp4"


def test_sniff_mime_garbage_none():
    assert sniff_mime(b"esto no es un archivo valido") is None
    assert sniff_mime(b"") is None
    assert sniff_mime(b"corto") is None


# ── strip_exif ─────────────────────────────────────────────────────────


def _jpeg_bytes(with_exif: bool = False) -> bytes:
    image = Image.new("RGB", (8, 8), (200, 30, 30))
    buf = BytesIO()
    if with_exif:
        exif = Image.Exif()
        exif[0x010F] = "MarcaPrueba"  # Make
        exif[0x0110] = "ModeloPrueba"  # Model
        image.save(buf, "JPEG", exif=exif.tobytes())
    else:
        image.save(buf, "JPEG")
    return buf.getvalue()


def test_strip_exif_removes_jpeg_metadata():
    original = _jpeg_bytes(with_exif=True)
    with Image.open(BytesIO(original)) as img:
        assert len(img.getexif()) > 0  # el fixture sí trae EXIF

    cleaned = strip_exif(original, "image/jpeg")
    assert sniff_mime(cleaned) == "image/jpeg"
    with Image.open(BytesIO(cleaned)) as img:
        assert len(img.getexif()) == 0
        assert img.size == (8, 8)


def test_strip_exif_passthrough_non_image():
    data = b"%PDF-1.7 contenido"
    assert strip_exif(data, "application/pdf") is data


# ── parse_uri ──────────────────────────────────────────────────────────


def test_parse_uri_ok():
    assert parse_uri("s3://protected/abc/clean") == ("protected", "abc/clean")


def test_parse_uri_invalid():
    with pytest.raises(ValueError):
        parse_uri("meta-media://123")
    with pytest.raises(ValueError):
        parse_uri("s3://solo-bucket")


# ── clamav (socket simulado) ───────────────────────────────────────────


class _FakeSock:
    def __init__(self, reply: bytes):
        self.reply = reply
        self.sent = b""

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def settimeout(self, timeout):
        pass

    def sendall(self, data: bytes):
        self.sent += data

    def recv(self, size: int) -> bytes:
        reply, self.reply = self.reply, b""
        return reply


def test_clamav_clean(monkeypatch):
    sock = _FakeSock(b"stream: OK\0")
    monkeypatch.setattr(clamav.socket, "create_connection", lambda *a, **k: sock)
    assert clamav.scan_bytes(b"hola" * 5000, host="clam", port=3310) == "CLEAN"
    # protocolo: comando, chunks con prefijo big-endian y chunk final 0
    assert sock.sent.startswith(b"zINSTREAM\0")
    assert sock.sent.endswith(b"\x00\x00\x00\x00")


def test_clamav_infected(monkeypatch):
    sock = _FakeSock(b"stream: Eicar-Test-Signature FOUND\0")
    monkeypatch.setattr(clamav.socket, "create_connection", lambda *a, **k: sock)
    assert clamav.scan_bytes(b"x", host="clam", port=3310) == "INFECTED"


def test_clamav_connection_error(monkeypatch):
    def _boom(*args, **kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(clamav.socket, "create_connection", _boom)
    assert clamav.scan_bytes(b"x", host="clam", port=3310) == "ERROR"


def test_clamav_skipped_without_host():
    assert clamav.scan_bytes(b"x", host="") == "SKIPPED"


# ── process_media_asset (integración con BD) ───────────────────────────


class FakeStorage:
    def __init__(self):
        self.objects: dict[tuple[str, str], bytes] = {}

    def put(self, bucket, key, data, content_type=None):
        self.objects[(bucket, key)] = data
        return f"s3://{bucket}/{key}"

    def get(self, bucket, key):
        return self.objects[(bucket, key)]

    def delete(self, bucket, key):
        self.objects.pop((bucket, key), None)


class FakeGraphClient:
    payload: bytes = b""

    def get_media_url(self, media_id):
        return "https://lookaside.example.invalid/media", "image/jpeg"

    def download_media(self, url):
        return self.payload

    def close(self):
        pass


@pytest.fixture
def media_ctx(db_session, monkeypatch):
    """Storage en memoria, GraphClient falso y token de prueba."""
    from app.core.config import get_settings
    from app.modules.intake import media_service

    monkeypatch.setenv("META_ACCESS_TOKEN", "token-de-prueba")
    get_settings.cache_clear()

    storage = FakeStorage()
    monkeypatch.setattr(media_service, "get_storage", lambda: storage)
    monkeypatch.setattr(media_service, "GraphClient", FakeGraphClient)
    monkeypatch.setattr(media_service, "scan_bytes", lambda data: "CLEAN")
    FakeGraphClient.payload = _jpeg_bytes(with_exif=True)
    yield media_service, storage, db_session
    get_settings.cache_clear()


def _make_asset(session, uri: str = "meta-media://MEDIA123"):
    from app.modules.identity.models import ChannelType
    from app.modules.intake.models import (
        Channel,
        Conversation,
        MalwareStatus,
        MediaAsset,
        Message,
        MessageDirection,
        MessageType,
        ProviderType,
        StorageClassification,
    )

    channel = Channel(
        type=ChannelType.WHATSAPP,
        provider=ProviderType.META_CLOUD_API,
        display_name="canal-prueba",
    )
    session.add(channel)
    session.flush()
    conversation = Conversation(channel_id=channel.id)
    session.add(conversation)
    session.flush()
    message = Message(
        conversation_id=conversation.id,
        provider_message_id=f"wamid.{uuid.uuid4().hex}",
        direction=MessageDirection.INBOUND,
        type=MessageType.IMAGE,
    )
    session.add(message)
    session.flush()
    asset = MediaAsset(
        message_id=message.id,
        bucket_class=StorageClassification.QUARANTINE,
        object_uri=uri,
        mime_type="image/jpeg",
        malware_status=MalwareStatus.PENDING,
        exif_removed=False,
    )
    session.add(asset)
    session.flush()
    return asset, message


def _event_for(asset):
    from app.modules.intake.models import OutboxEvent

    return OutboxEvent(
        aggregate_type="media_asset",
        aggregate_id=asset.id,
        event_type="media.received",
    )


def _ready_events(session, asset):
    from app.modules.intake.models import OutboxEvent

    return (
        session.execute(
            sa.select(OutboxEvent).where(
                OutboxEvent.event_type == "media.ready",
                OutboxEvent.aggregate_id == asset.id,
            )
        )
        .scalars()
        .all()
    )


def test_process_media_asset_happy_path(media_ctx):
    from app.core.config import get_settings
    from app.modules.intake.models import MalwareStatus, StorageClassification

    media_service, storage, session = media_ctx
    settings = get_settings()
    asset, _message = _make_asset(session)

    media_service.process_media_asset(session, _event_for(asset))

    assert asset.bucket_class == StorageClassification.PROTECTED
    assert asset.malware_status == MalwareStatus.CLEAN
    assert asset.exif_removed is True
    assert asset.mime_type == "image/jpeg"
    assert asset.sha256 is not None and len(asset.sha256) == 32
    assert asset.object_uri == f"s3://{settings.s3_bucket_protected}/{asset.id}/clean"

    # original en cuarentena + copia limpia en protected
    assert (settings.s3_bucket_quarantine, f"{asset.id}/original") in storage.objects
    clean = storage.objects[(settings.s3_bucket_protected, f"{asset.id}/clean")]
    with Image.open(BytesIO(clean)) as img:
        assert len(img.getexif()) == 0

    assert len(_ready_events(session, asset)) == 1

    # reejecución del handler (reentrega del outbox): no-op
    media_service.process_media_asset(session, _event_for(asset))
    assert len(_ready_events(session, asset)) == 1


def test_process_media_asset_infected(media_ctx, monkeypatch):
    from app.core.config import get_settings
    from app.modules.intake.models import (
        MalwareStatus,
        MessageProcessingStatus,
        StorageClassification,
    )

    media_service, storage, session = media_ctx
    monkeypatch.setattr(media_service, "scan_bytes", lambda data: "INFECTED")
    asset, message = _make_asset(session)

    media_service.process_media_asset(session, _event_for(asset))

    assert asset.malware_status == MalwareStatus.INFECTED
    assert asset.bucket_class == StorageClassification.QUARANTINE
    assert message.processing_status == MessageProcessingStatus.NEEDS_REVIEW
    settings = get_settings()
    assert (settings.s3_bucket_quarantine, f"{asset.id}/original") not in storage.objects
    assert _ready_events(session, asset) == []


def test_process_media_asset_mime_not_allowed(media_ctx):
    from app.modules.intake.models import (
        MalwareStatus,
        MessageProcessingStatus,
        StorageClassification,
    )

    media_service, storage, session = media_ctx
    FakeGraphClient.payload = b"\x00\x01\x02contenido sin firma reconocible"
    asset, message = _make_asset(session)

    media_service.process_media_asset(session, _event_for(asset))

    assert asset.malware_status == MalwareStatus.ERROR
    assert asset.bucket_class == StorageClassification.QUARANTINE
    assert message.processing_status == MessageProcessingStatus.NEEDS_REVIEW
    assert storage.objects == {}
    assert _ready_events(session, asset) == []

    # idempotente: no reintenta ni cambia nada
    media_service.process_media_asset(session, _event_for(asset))
    assert asset.malware_status == MalwareStatus.ERROR


def test_process_media_asset_scan_error_raises(media_ctx, monkeypatch):
    media_service, _storage, session = media_ctx
    monkeypatch.setattr(media_service, "scan_bytes", lambda data: "ERROR")
    asset, _message = _make_asset(session)

    with pytest.raises(RuntimeError):
        media_service.process_media_asset(session, _event_for(asset))
