from fastapi.testclient import TestClient

from app.main import create_app


def test_health_ok():
    client = TestClient(create_app())
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_request_id_propagation():
    client = TestClient(create_app())
    resp = client.get("/health", headers={"X-Request-ID": "req-test-123"})
    assert resp.headers["X-Request-ID"] == "req-test-123"


def test_ready_reports_db_state():
    client = TestClient(create_app())
    resp = client.get("/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert "db" in body and "status" in body


def test_short_code_format():
    from app.core.ids import new_short_code

    code = new_short_code("CU")
    assert code.startswith("CU-")
    assert len(code) == 9
    # Sin caracteres ambiguos
    assert not any(c in code[3:] for c in "01OIL")
