"""Almacenamiento de objetos S3/MinIO (§4.2-7 del alcance).

Cuatro buckets por clasificación: quarantine (entrada sin validar),
protected (media validada y limpia), public (derivados publicables) y
audit (evidencia). Nunca loggear URLs firmadas (§18.1).
"""

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

from app.core.config import get_settings


def parse_uri(uri: str) -> tuple[str, str]:
    """Descompone ``s3://bucket/key`` en (bucket, key)."""
    if not uri.startswith("s3://"):
        raise ValueError("URI de objeto no soportada (se espera s3://)")
    bucket, _, key = uri.removeprefix("s3://").partition("/")
    if not bucket or not key:
        raise ValueError("URI s3:// sin bucket o sin key")
    return bucket, key


class ObjectStorage:
    """Cliente síncrono sobre el endpoint S3 configurado (MinIO en producción)."""

    def __init__(self):
        settings = get_settings()
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url or None,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name="us-east-1",
            config=BotoConfig(signature_version="s3v4"),
        )

    def ensure_buckets(self) -> None:
        """Crea los buckets del alcance si no existen (idempotente)."""
        settings = get_settings()
        for bucket in (
            settings.s3_bucket_quarantine,
            settings.s3_bucket_protected,
            settings.s3_bucket_public,
            settings.s3_bucket_audit,
        ):
            try:
                self._client.head_bucket(Bucket=bucket)
            except ClientError:
                self._client.create_bucket(Bucket=bucket)

    def put(self, bucket: str, key: str, data: bytes, content_type: str | None = None) -> str:
        extra = {"ContentType": content_type} if content_type else {}
        self._client.put_object(Bucket=bucket, Key=key, Body=data, **extra)
        return f"s3://{bucket}/{key}"

    def get(self, bucket: str, key: str) -> bytes:
        response = self._client.get_object(Bucket=bucket, Key=key)
        return response["Body"].read()

    def delete(self, bucket: str, key: str) -> None:
        self._client.delete_object(Bucket=bucket, Key=key)

    def presigned_get_url(self, bucket: str, key: str, expires_seconds: int = 300) -> str:
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=expires_seconds,
        )


_storage: ObjectStorage | None = None


def get_storage() -> ObjectStorage:
    """Singleton perezoso; el cliente boto3 es reutilizable entre hilos."""
    global _storage  # noqa: PLW0603
    if _storage is None:
        _storage = ObjectStorage()
    return _storage
