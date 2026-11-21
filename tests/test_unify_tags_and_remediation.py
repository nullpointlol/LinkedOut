import json
import pytest

from tests.cv_final_prueba import pdf_de_prueba
from unittest.mock import AsyncMock, patch
from pathlib import Path
from fastapi.testclient import TestClient
from server import app
import core.aprendizajes as aprendizajes_module
from core.database import create_postulacion, get_db_connection, get_postulacion

def test_legacy_modal_and_button_purged_from_index_html():
    html_path = Path("static/index.html")
    assert html_path.exists()
    content = html_path.read_text(encoding="utf-8")
    assert "btnAbrirExpandirPerfil" not in content, "Legacy button btnAbrirExpandirPerfil must be removed"
    assert "modalExpandirPerfil" not in content, "Legacy modal modalExpandirPerfil must be removed"
    assert "btnExpandirBrechas" not in content, "Legacy button btnExpandirBrechas must be removed"

@pytest.mark.asyncio
async def test_remediate_and_recalculate_incorporate_learned_tags(monkeypatch, tmp_path):
    test_file = tmp_path / "aprendizajes_unify_test.json"
    monkeypatch.setattr(aprendizajes_module, "APRENDIZAJES_FILE", test_file)

    # Register an active learning
    aprendizajes_module.registrar_aprendizaje("Kubernetes", "MATCH_DIRECTO")
    aprendizajes_module.registrar_aprendizaje("Go", "TRANSFERIBLE")

    post_id = create_postulacion(
        empresa="CloudCorp",
        titulo="Cloud Engineer",
        contenido="Requerimientos: Linux, Kubernetes, Go y Rust"
    )

    with get_db_connection() as conn:
        analisis_json = json.dumps({
            "coincidencias": ["Linux"],
            "transferibles": [],
            "brechas": ["Kubernetes", "Go", "Rust"]
        })
        conn.execute("UPDATE postulaciones SET analisis = ?, status = 'READY' WHERE id = ?", (analisis_json, post_id))

    # Mock WriterAgent and AuditorAgent to inspect keywords passed
    passed_analisis = {}
    passed_keywords = []

    class DummyWriter:
        async def generate_cv_and_pitch(self, analisis, *, datos_de_terceros=False):
            assert datos_de_terceros is True
            nonlocal passed_analisis
            passed_analisis = analisis
            return {"cv_markdown": "# CV", "pitch": "Pitch"}

    class DummyAuditor:
        def __init__(self, *args, **kwargs):
            self.bridge = AsyncMock()
            self.bridge.last_telemetry = {}
        async def audit_and_remediate(self, cv, pitch, keywords, max_attempts=1):
            nonlocal passed_keywords
            passed_keywords = keywords
            return cv, pitch, {"score": 90}

    monkeypatch.setattr("server.WriterAgent", DummyWriter)
    monkeypatch.setattr("server.AuditorAgent", DummyAuditor)

    import server
    from types import SimpleNamespace
    from core.titulo_cv import DecisionTitulo

    class FlujoCapturador:
        """CV desde el maestro: el análisis con lo aprendido llega al flujo del maestro."""
        def __init__(self, **kw):
            nonlocal passed_analisis
            passed_analisis = kw["analisis"]
            self.segundos, self.historial = {}, []

        async def generar(self):
            return SimpleNamespace(markdown="# CV", correcciones=0, reporte={"approved": True}, keywords_cv={},
                                   segundos={}, pdf=pdf_de_prueba())

    monkeypatch.setattr(server.cv_maestro, "FlujoCV", FlujoCapturador)
    monkeypatch.setattr(server, "asegurar_anios_por_area", AsyncMock(return_value=[]))
    monkeypatch.setattr(server, "decidir_titulo", AsyncMock(return_value=DecisionTitulo(
        caso=1, clave=None, titulo="Cloud Engineer", roles_reales=(), puesto_oferta="Cloud Engineer")))
    await server.recalculate_cv_task(post_id)
    # recalculate_cv_task should have integrated Kubernetes and Go from aprendizajes
    assert "Kubernetes" in passed_analisis.get("coincidencias", [])
    assert "Go" in passed_analisis.get("transferibles", [])

    passed_analisis = {}
    await server.remediate_cv_task(post_id)
    # Un CV del maestro se remedia con su flujo, también con lo aprendido
    assert "Kubernetes" in passed_analisis.get("coincidencias", [])
    assert "Go" in passed_analisis.get("transferibles", [])
