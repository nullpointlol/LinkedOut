"""End-to-End Pipeline Mocked Tests para verificar que no haya regresiones."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from pathlib import Path
from core.database import init_db, create_postulacion, get_postulacion
import server
from tests.cv_final_prueba import simular_cv_maestro
from tests.test_cv_formatting_and_a4_e2e import SAMPLE_E2E_CV


CV_MOCK = SAMPLE_E2E_CV.replace("SAM ORTEGA", "CV Mock").replace(
    "• Análisis funcional,", "• Python, Docker, Análisis funcional,")


@pytest.fixture(autouse=True)
def setup_mock_db(tmp_path, monkeypatch):
    test_db = tmp_path / "test_pipeline_mocked.db"
    monkeypatch.setattr("core.database.DB_PATH", test_db)
    init_db(test_db)
    yield test_db
    if test_db.exists():
        try:
            test_db.unlink()
        except Exception:
            pass


@pytest.mark.asyncio
async def test_pipeline_e2e_mocked(monkeypatch):
    """Verifica que run_pipeline_task complete el ciclo de 3 subagentes con datos mockeados y estado READY."""
    class MockStrategist:
        def __init__(self, *args, **kwargs):
            pass
        async def analyze_offer(self, offer_text, *, datos_de_terceros=False):
            assert datos_de_terceros is True
            return {
                "empresa": "Tech Mock Corp",
                "puesto": "Tech Lead",
                "dolores_empresa": ["Deuda técnica"],
                "requisitos": [
                    {"nombre": "Python", "estado": "MATCH_DIRECTO", "approved": True},
                    {"nombre": "Docker", "estado": "MATCH_DIRECTO", "approved": True},
                ],
                "match_score": 92.0,
            }

    class MockWriter:
        def __init__(self, *args, **kwargs):
            pass
        async def generate_cv_and_pitch(self, analysis, *, datos_de_terceros=False):
            assert datos_de_terceros is True
            return {
                "cv_markdown": CV_MOCK,
                "pitch": "Pitch Mock",
                "pitches_multicanal": {"inmail": "inmail", "email": "email", "web_ats": "web_ats"},
            }

    class MockAuditor:
        def __init__(self, *args, **kwargs):
            self.bridge = MagicMock()
            self.bridge.last_telemetry = {"status": "SUCCESS", "model": "gemini-3.8-flash-low"}
        async def audit_and_remediate(self, cv, pitch, kw, max_attempts=1):
            return cv, pitch, {"score_ats": 98, "approved": True}

    monkeypatch.setattr("server.StrategistAgent", MockStrategist)
    monkeypatch.setattr("server.WriterAgent", MockWriter)
    monkeypatch.setattr("server.AuditorAgent", MockAuditor)
    simular_cv_maestro(monkeypatch, server)

    post_id = create_postulacion(
        titulo="Tech Lead",
        empresa="Tech Mock Corp",
        contenido="Buscamos Tech Lead con Python y Docker.",
        status="PROCESSING",
    )

    await server.run_pipeline_task(post_id)

    res = get_postulacion(post_id)
    assert res is not None
    assert res["status"] == "READY"
    assert res["match_score"] == 92.0
    assert "Tech Mock Corp" in res["empresa"]
    assert "SAM ORTEGA" in res["cv_markdown"]  # CV desde el maestro del candidato inventado
