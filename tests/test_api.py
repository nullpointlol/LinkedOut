import pytest
from fastapi.testclient import TestClient
from core import database, radar_store
from core.database import init_db, get_db_connection
from server import app

@pytest.fixture(autouse=True)
def setup_test_db(tmp_path, monkeypatch):
    test_db = tmp_path / "test_postulaciones.db"
    monkeypatch.setattr("core.database.DB_PATH", test_db)
    init_db(test_db)

    async def fake_run_prompt(self, prompt, json_mode=True, timeout_seconds=180):
        if "Estratega" in prompt:
            return {
                "empresa": "TechCorp",
                "puesto": "Senior Backend Engineer",
                "dolores_empresa": ["Optimizar CI/CD"],
                "requisitos": [
                    {"nombre": "Python", "estado": "MATCH_DIRECTO", "approved": True, "justificacion": "Python en VELORA"},
                    {"nombre": "AWS", "estado": "TRANSFERIBLE", "approved": False, "justificacion": "Infraestructura"}
                ],
                "match_score": 50.0
            }
        elif "Redactor" in prompt:
            return {
                "cv_markdown": "# SAM ORTEGA\n## PERFIL\nAnalista\n## EXPERIENCIA\n• Logró X\n## HABILIDADES TÉCNICAS\n• Python\n## EDUCACIÓN\n• Tec\n## IDIOMAS\n• ES",
                "pitch": "Párrafo 1\n\nPárrafo 2\n\nPárrafo 3"
            }
        elif "Auditor" in prompt:
            return {
                "cv_markdown": "# SAM ORTEGA\n## PERFIL\nAnalista\n## EXPERIENCIA\n• Logró X\n## HABILIDADES TÉCNICAS\n• Python\n## EDUCACIÓN\n• Tec\n## IDIOMAS\n• ES",
                "pitch": "Párrafo 1\n\nPárrafo 2\n\nPárrafo 3"
            }
        return {"status": "OK"}

    monkeypatch.setattr("core.bridge.AntigravityBridge.run_prompt", fake_run_prompt)

    yield
    if test_db.exists():
        try:
            test_db.unlink()
        except Exception:
            pass

def test_api_ofertas_crud(monkeypatch):
    from unittest.mock import AsyncMock
    import server
    monkeypatch.setattr(server.radar_loop, "wake_processing", AsyncMock())
    client = TestClient(app)

    # 1. Crear oferta
    payload = {
        "titulo": "Senior Backend Engineer",
        "empresa": "TechCorp",
        "contenido": "Buscamos Ingeniero con experiencia sólida en Python, SQL, Jenkins y AWS.",
        "url": "https://example.com/job/1"
    }
    response = client.post("/api/ofertas", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert "id" in data
    assert radar_store.get_post(data["id"])["fuente"] == "manual"
    assert database.list_postulaciones(origen=None) == []
    # Los endpoints históricos de detalle/edición reciben ids de postulaciones.
    oferta_id = database.create_postulacion(**payload)
    import asyncio
    # El análisis es del modelo (sin IA no hay análisis): se simula su respuesta
    analisis = {"empresa": "TechCorp", "puesto": "Senior Backend Engineer", "dolores_empresa": [], "match_score": 50.0,
                "requisitos": [{"nombre": "Python", "estado": "MATCH_DIRECTO", "approved": True,
                                "tipo": "REQUISITO_EXCLUYENTE"},
                               {"nombre": "AWS", "estado": "BRECHA_REAL", "approved": False,
                                "tipo": "REQUISITO_EXCLUYENTE"}]}
    monkeypatch.setattr(server.StrategistAgent, "analyze_offer", AsyncMock(return_value=analisis))
    asyncio.run(server.run_pipeline_task(oferta_id))

    # 2. Listar ofertas
    list_resp = client.get("/api/ofertas")
    assert list_resp.status_code == 200
    ofertas = list_resp.json()
    assert len(ofertas) >= 1
    assert ofertas[0]["id"] == oferta_id

    # 3. Obtener detalle de oferta
    detail_resp = client.get(f"/api/ofertas/{oferta_id}")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["titulo"] == "Senior Backend Engineer"
    assert detail["empresa"] == "TechCorp"
    assert isinstance(detail["requisitos"], list)

    # 4. Toggle tag
    assert detail["requisitos"]
    toggle_resp = client.post(f"/api/ofertas/{oferta_id}/toggle-tag", json={"tag_index": 0})
    assert toggle_resp.status_code == 200
    updated = toggle_resp.json()
    assert "match_score" in updated
