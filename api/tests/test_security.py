import base64
import os

import pytest

from app.core import security
from app.core.config import get_settings


@pytest.fixture(autouse=True)
def _keys(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("APP_ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    monkeypatch.setenv("APP_HMAC_KEY", base64.b64encode(os.urandom(32)).decode())
    yield
    get_settings.cache_clear()


def test_encrypt_decrypt_roundtrip():
    ct = security.encrypt_text("María, vereda San Miguel")
    assert ct != b"Mar\xc3\xada, vereda San Miguel"
    assert security.decrypt_text(ct) == "María, vereda San Miguel"


def test_encrypt_produces_distinct_ciphertexts():
    a = security.encrypt_text("mismo valor")
    b = security.encrypt_text("mismo valor")
    assert a != b  # nonce aleatorio


def test_json_roundtrip():
    data = {"needs": [{"code": "SHELTER.MATTRESS", "qty": 5}], "ok": True}
    assert security.decrypt_json(security.encrypt_json(data)) == data


def test_hmac_deterministic_and_keyed():
    h1 = security.hmac_index("573001234567")
    h2 = security.hmac_index("573001234567")
    h3 = security.hmac_index("573001234568")
    assert h1 == h2
    assert h1 != h3
    assert len(h1) == 32


def test_normalize_phone_colombia():
    assert security.normalize_phone("+57 300 123 4567") == "573001234567"
    assert security.normalize_phone("3001234567") == "573001234567"
    assert security.normalize_phone("573001234567") == "573001234567"


def test_phone_hmac_equivalent_formats():
    assert security.phone_hmac("+57 300 123 4567") == security.phone_hmac("3001234567")


def test_missing_key_raises(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("APP_ENCRYPTION_KEY", "")
    with pytest.raises(RuntimeError, match="APP_ENCRYPTION_KEY"):
        security.encrypt_text("x")
