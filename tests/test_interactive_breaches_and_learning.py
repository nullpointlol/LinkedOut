import json
import pytest
from unittest.mock import AsyncMock
from pathlib import Path
from fastapi.testclient import TestClient
from server import app
import core.aprendizajes as aprendizajes_module

@pytest.fixture
def clean_aprendizajes_file(tmp_path, monkeypatch):
    test_file = tmp_path / "aprendizajes_test.json"
    monkeypatch.setattr(aprendizajes_module, "APRENDIZAJES_FILE", test_file)
    return test_file

def test_aprendizajes_registration_and_retrieval(clean_aprendizajes_file):
    # Initial state should be empty
    assert aprendizajes_module.obtener_aprendizajes() == {}

    # Register MATCH_DIRECTO
    res1 = aprendizajes_module.registrar_aprendizaje("Docker", "MATCH_DIRECTO")
    assert res1["estado"] == "MATCH_DIRECTO"
    assert "docker" in aprendizajes_module.obtener_aprendizajes()
    assert aprendizajes_module.obtener_aprendizajes()["docker"]["estado"] == "MATCH_DIRECTO"

    # Update to TRANSFERIBLE
    res2 = aprendizajes_module.registrar_aprendizaje("Docker", "TRANSFERIBLE")
    assert res2["estado"] == "TRANSFERIBLE"
    assert aprendizajes_module.obtener_aprendizajes()["docker"]["estado"] == "TRANSFERIBLE"

    # Register BRECHA_REAL (Docker se conserva con BRECHA_REAL)
    res3 = aprendizajes_module.registrar_aprendizaje("Docker", "BRECHA_REAL")
    assert res3["estado"] == "BRECHA_REAL"
    assert "docker" in aprendizajes_module.obtener_aprendizajes()
    assert aprendizajes_module.obtener_aprendizajes()["docker"]["estado"] == "BRECHA_REAL"

def test_aplicar_aprendizajes_a_analisis(clean_aprendizajes_file):
    aprendizajes_module.registrar_aprendizaje("Kubernetes", "MATCH_DIRECTO")
    aprendizajes_module.registrar_aprendizaje("FastAPI", "TRANSFERIBLE")

    analisis_mock = {
        "coincidencias": ["Python"],
        "brechas": ["Kubernetes", "Rust"],
        "transferibles": ["FastAPI"]
    }

    actualizado = aprendizajes_module.aplicar_aprendizajes_a_analisis(analisis_mock)
    # Kubernetes was in brechas, now should be promoted to coincidencias
    assert "Kubernetes" in actualizado["coincidencias"]
    assert "Kubernetes" not in actualizado["brechas"]
    # Rust should stay in brechas
    assert "Rust" in actualizado["brechas"]
    # FastAPI is transferible
    assert "FastAPI" in actualizado["transferibles"]

def test_endpoint_cycle_tag_state(monkeypatch, tmp_path):
    test_file = tmp_path / "aprendizajes_server_test.json"
    monkeypatch.setattr(aprendizajes_module, "APRENDIZAJES_FILE", test_file)
    monkeypatch.setattr("server.run_pipeline_task", AsyncMock())
    client = TestClient(app)

    # 1. Create offer
    from core.database import create_postulacion, get_db_connection
    post_id = create_postulacion(
        empresa="TechCorp",
        titulo="Tech Lead",
        contenido="Se requiere AWS, Docker y Go."
    )

    # Seed mock analysis in database
    with get_db_connection() as conn:
        analisis_json = json.dumps({
            "coincidencias": ["AWS"],
            "transferibles": ["Docker"],
            "brechas": ["Go"]
        })
        conn.execute("UPDATE postulaciones SET analisis = ?, status = 'READY' WHERE id = ?", (analisis_json, post_id))

    # Test cycling 'Go' (starts as BRECHA_REAL) -> TRANSFERIBLE
    res1 = client.post(f"/api/ofertas/{post_id}/tags/Go/cycle-state")
    assert res1.status_code == 200
    data1 = res1.json()
    assert data1["ok"] is True
    assert data1["anterior_estado"] == "BRECHA_REAL"
    assert data1["nuevo_estado"] == "TRANSFERIBLE"
    assert "go" in aprendizajes_module.obtener_aprendizajes()

    # Test cycling 'Go' again: TRANSFERIBLE -> MATCH_DIRECTO
    res2 = client.post(f"/api/ofertas/{post_id}/tags/Go/cycle-state")
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["anterior_estado"] == "TRANSFERIBLE"
    assert data2["nuevo_estado"] == "MATCH_DIRECTO"
    assert aprendizajes_module.obtener_aprendizajes()["go"]["estado"] == "MATCH_DIRECTO"

    # Test cycling 'Go' again: MATCH_DIRECTO -> BRECHA_REAL
    res3 = client.post(f"/api/ofertas/{post_id}/tags/Go/cycle-state")
    assert res3.status_code == 200
    data3 = res3.json()
    assert data3["anterior_estado"] == "MATCH_DIRECTO"
    assert data3["nuevo_estado"] == "BRECHA_REAL"
    assert aprendizajes_module.obtener_aprendizajes()["go"]["estado"] == "BRECHA_REAL"

def test_endpoint_get_aprendizajes(monkeypatch, tmp_path):
    test_file = tmp_path / "aprendizajes_list_test.json"
    monkeypatch.setattr(aprendizajes_module, "APRENDIZAJES_FILE", test_file)
    client = TestClient(app)

    aprendizajes_module.registrar_aprendizaje("Terraform", "MATCH_DIRECTO")
    aprendizajes_module.registrar_aprendizaje("PostgreSQL", "TRANSFERIBLE")

    res = client.get("/api/candidato/aprendizajes")
    assert res.status_code == 200
    aprendizajes = res.json()["aprendizajes"]
    assert "terraform" in aprendizajes
    assert "postgresql" in aprendizajes
    assert aprendizajes["terraform"]["estado"] == "MATCH_DIRECTO"


def test_app_js_render_matriz_oferta_does_not_use_undefined_data_id():
    """Valida que static/app.js en renderMatrizOferta use postulacionId u ofertaActual?.id sin lanzar ReferenceError sobre data.id."""
    app_js_path = Path("static/app.js")
    assert app_js_path.exists()
    content = app_js_path.read_text(encoding="utf-8")

    # Extraer la función renderMatrizOferta
    assert "function renderMatrizOferta" in content
    start = content.index("function renderMatrizOferta")
    end = content.find("\nfunction ", start + 1)
    func_code = content[start:end] if end != -1 else content[start:]

    # Debe recibir o resolver el id sin depender de variable global no declarada 'data.id'
    assert "${data.id}" not in func_code, "renderMatrizOferta no debe usar ${data.id} directamente porque 'data' está fuera de scope"
    assert "ciclarEstadoTag" in func_code


def test_endpoint_cycle_tag_updates_matriz_oferta_and_returns_match_score(monkeypatch, tmp_path):
    test_file = tmp_path / "aprendizajes_server_test2.json"
    monkeypatch.setattr(aprendizajes_module, "APRENDIZAJES_FILE", test_file)
    monkeypatch.setattr("server.run_pipeline_task", AsyncMock())
    client = TestClient(app)

    from core.database import create_postulacion, get_db_connection
    post_id = create_postulacion(
        empresa="DataSys",
        titulo="Cloud Engineer",
        contenido="Se requiere Docker y Python."
    )

    with get_db_connection() as conn:
        analisis_json = json.dumps({
            "coincidencias": ["Python"],
            "transferibles": [],
            "brechas": ["Docker"],
            "requisitos": [
                {"nombre": "Python", "tipo": "REQUISITO_EXCLUYENTE", "estado": "MATCH_DIRECTO", "approved": True},
                {"nombre": "Docker", "tipo": "REQUISITO_EXCLUYENTE", "estado": "BRECHA_REAL", "approved": False}
            ]
        })
        conn.execute("UPDATE postulaciones SET analisis = ?, requisitos = ?, match_score = 45.0, status = 'READY' WHERE id = ?",
                     (analisis_json, analisis_json, post_id))

    res = client.post(f"/api/ofertas/{post_id}/tags/Docker/cycle-state")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["anterior_estado"] == "BRECHA_REAL"
    assert data["nuevo_estado"] == "TRANSFERIBLE"
    assert "match_score" in data
    assert isinstance(data["match_score"], (int, float))
    assert "matriz_oferta" in data["analisis"]
    herramientas = data["analisis"]["matriz_oferta"].get("herramientas", [])
    docker_item = next((item for item in herramientas if item["nombre"].lower() == "docker"), None)
    assert docker_item is not None
    assert docker_item["estado"] == "TRANSFERIBLE"


