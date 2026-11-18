import pytest
from fastapi.testclient import TestClient
from core import radar_store
from core.database import init_db
from server import app

@pytest.fixture(autouse=True)
def setup_test_env(tmp_path, monkeypatch):
    test_db = tmp_path / "test_batch.db"
    monkeypatch.setattr("core.database.DB_PATH", test_db)
    init_db(test_db)

    from unittest.mock import AsyncMock
    import server
    monkeypatch.setattr(server.radar_loop, "wake_processing", AsyncMock())

    yield
    if test_db.exists():
        try:
            test_db.unlink()
        except Exception:
            pass

def test_api_oferta_single_url():
    client = TestClient(app)
    response = client.post("/api/ofertas/url", json={"url": "https://jobs.example.com/ba-1"})
    assert response.status_code == 200
    data = response.json()
    assert "id" in data
    assert data["status"] == "PROCESSING"
    assert radar_store.get_post(data["id"])["fuente"] == "manual"

def test_api_ofertas_batch():
    client = TestClient(app)
    urls = [
        "https://jobs.example.com/job-1",
        "https://jobs.example.com/job-2",
        "https://jobs.example.com/job-3",
    ]
    response = client.post("/api/ofertas/batch", json={"urls": urls})
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 3
    assert len(data["ids"]) == 3

    assert all(radar_store.get_post(i)["fuente"] == "manual" for i in data["ids"])
