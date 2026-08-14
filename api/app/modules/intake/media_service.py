"""Pipeline de media (§4.2-7 del alcance).

Handler idempotente del evento `media.received`: descarga la media de
Meta (o la lee de S3 si ya fue subida por otra vía), valida el MIME por
firma real de bytes, la pasa por cuarentena + antivirus, calcula SHA-256,
elimina EXIF de imágenes y la promueve al bucket protegido. Al final
publica `media.ready` para las etapas siguientes (STT, triage, etc.).
"""

import hashlib
import logging
from io import BytesIO

import sqlalchemy as sa
from PIL import Image
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import log_ctx
from app.core.outbox import OutboxEvent, publish, register_handler
from app.integrations.clamav import scan_bytes
from app.integrations.meta_whatsapp.client import GraphClient
from app.integrations.storage import get_storage, parse_uri
from app.modules.intake.models import (
    MalwareStatus,
    MediaAsset,
    Message,
    MessageProcessingStatus,
    StorageClassification,
)

logger = logging.getLogger("media")

MIME_ALLOWED = {
    # Audio (notas de voz y adjuntos WhatsApp)
    "audio/ogg",
    "audio/mpeg",
    "audio/mp4",
    "audio/amr",
    "audio/aac",
    "audio/wav",
    # Imagen
    "image/jpeg",
    "image/png",
    "image/webp",
    # Video
    "video/mp4",
    "video/3gpp",
    # Documento
    "application/pdf",
}

_PIL_FORMATS = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}


def sniff_mime(data: bytes) -> str | None:
    """Detecta el MIME por magic numbers; None si no se reconoce.

    Nunca se confía en el MIME declarado por el proveedor (§4.2-7).
    """
    if len(data) < 12:
        return None
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"RIFF"):
        if data[8:12] == b"WEBP":
            return "image/webp"
        if data[8:12] == b"WAVE":
            return "audio/wav"
        return None
    if data.startswith(b"OggS"):
        return "audio/ogg"
    if data.startswith(b"ID3") or data.startswith(b"\xff\xfb"):
        return "audio/mpeg"
    if data.startswith(b"\xff\xf1") or data.startswith(b"\xff\xf9"):
        return "audio/aac"
    if data.startswith(b"#!AMR"):
        return "audio/amr"
    if data.startswith(b"%PDF"):
        return "application/pdf"
    if data[4:8] == b"ftyp":
        brand = data[8:12]
        if brand.startswith(b"3gp"):
            return "video/3gpp"
        if brand in (b"M4A ", b"M4B "):
            return "audio/mp4"
        return "video/mp4"
    return None


def strip_exif(data: bytes, mime: str) -> bytes:
    """Recodifica imágenes sin metadata (EXIF/GPS); no-imagen pasa tal cual."""
    fmt = _PIL_FORMATS.get(mime)
    if fmt is None:
        return data
    with Image.open(BytesIO(data)) as image:
        if image.mode == "P":
            image = image.convert("RGBA")
        # Copia solo los píxeles: descarta EXIF, GPS, ICC y demás metadata.
        clean = Image.frombytes(image.mode, image.size, image.tobytes())
        out = BytesIO()
        if fmt == "JPEG":
            clean.save(out, format=fmt, quality=90)
        elif fmt == "PNG":
            clean.save(out, format=fmt, optimize=True)
        else:
            clean.save(out, format=fmt)
    return out.getvalue()


def _mark_message_needs_review(session: Session, asset: MediaAsset) -> None:
    message = session.get(Message, asset.message_id)
    if message is not None:
        message.processing_status = MessageProcessingStatus.NEEDS_REVIEW


def _load_bytes(session: Session, asset: MediaAsset) -> bytes | None:
    """Obtiene los bytes originales: Graph API o S3 si ya fue subida."""
    if asset.object_uri.startswith("s3://"):
        bucket, key = parse_uri(asset.object_uri)
        return get_storage().get(bucket, key)
    if asset.object_uri.startswith("meta-media://"):
        settings = get_settings()
        if not settings.meta_access_token:
            # Sin token no hay descarga posible: queda PENDING para un
            # reintento futuro cuando el entorno tenga credenciales.
            log_ctx(
                logger, logging.WARNING, "media.download_skipped_no_token",
                asset_id=str(asset.id),
            )
            return None
        media_id = asset.object_uri.removeprefix("meta-media://")
        client = GraphClient()
        try:
            url, _declared_mime = client.get_media_url(media_id)
            return client.download_media(url)
        finally:
            client.close()
    log_ctx(logger, logging.WARNING, "media.unknown_uri_scheme", asset_id=str(asset.id))
    return None


def process_media_asset(session: Session, event: OutboxEvent) -> None:
    """Handler idempotente del evento media.received (aggregate = MediaAsset)."""
    asset = session.get(MediaAsset, event.aggregate_id)
    if asset is None:
        return
    if asset.bucket_class != StorageClassification.QUARANTINE:
        return  # ya promovida a protected (reentrega del outbox)
    if asset.malware_status in (MalwareStatus.INFECTED, MalwareStatus.ERROR):
        return  # veredicto terminal previo: no se reprocesa

    data = _load_bytes(session, asset)
    if data is None:
        return

    settings = get_settings()
    storage = get_storage()

    mime = sniff_mime(data)
    if mime is None or mime not in MIME_ALLOWED:
        asset.malware_status = MalwareStatus.ERROR
        _mark_message_needs_review(session, asset)
        log_ctx(logger, logging.WARNING, "media.mime_rejected", asset_id=str(asset.id))
        return

    quarantine_key = f"{asset.id}/original"
    storage.put(settings.s3_bucket_quarantine, quarantine_key, data, mime)

    verdict = scan_bytes(data)
    if verdict == "INFECTED":
        asset.malware_status = MalwareStatus.INFECTED
        storage.delete(settings.s3_bucket_quarantine, quarantine_key)
        _mark_message_needs_review(session, asset)
        publish(
            session,
            event_type="media.infected",
            aggregate_type="media_asset",
            aggregate_id=asset.id,
        )
        log_ctx(logger, logging.WARNING, "media.infected", asset_id=str(asset.id))
        return
    if verdict == "ERROR":
        raise RuntimeError("clamav scan error")  # reintento vía outbox
    if verdict == "SKIPPED":
        log_ctx(logger, logging.WARNING, "media.scan_skipped", asset_id=str(asset.id))

    digest = hashlib.sha256(data).digest()
    duplicate_id = session.execute(
        sa.select(MediaAsset.id)
        .where(MediaAsset.sha256 == digest, MediaAsset.id != asset.id)
        .limit(1)
    ).scalar_one_or_none()
    if duplicate_id is not None:
        # UNIQUE(sha256): se registra el duplicado sin bloquear el pipeline
        # y sin violar la restricción (el hash queda en el asset original).
        log_ctx(
            logger, logging.INFO, "media.duplicate_sha256",
            asset_id=str(asset.id), duplicate_of=str(duplicate_id),
        )
    else:
        asset.sha256 = digest

    clean = data
    if mime.startswith("image/"):
        clean = strip_exif(data, mime)
        asset.exif_removed = True

    protected_key = f"{asset.id}/clean"
    asset.object_uri = storage.put(settings.s3_bucket_protected, protected_key, clean, mime)
    asset.bucket_class = StorageClassification.PROTECTED
    asset.malware_status = MalwareStatus.CLEAN
    asset.mime_type = mime
    asset.size_bytes = len(clean)

    publish(
        session,
        event_type="media.ready",
        aggregate_type="media_asset",
        aggregate_id=asset.id,
        payload={"mime": mime},
    )
    log_ctx(logger, logging.INFO, "media.ready", asset_id=str(asset.id))


def register() -> None:
    register_handler("media.received", process_media_asset)
