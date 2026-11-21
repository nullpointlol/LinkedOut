import asyncio
import server
from core.database import create_postulacion
import pytest
from fastapi.testclient import TestClient
from server import app, get_postulacion
import docx

client = TestClient(app)

@pytest.fixture(autouse=True)
def mock_llm_bridge(monkeypatch):
    """Garantiza ejecución rápida y determinista en tests E2E sin invocar el CLI externo de agy."""
    async def fake_run_prompt(self, prompt, json_mode=True, timeout_seconds=180, **kwargs):
        if "Estratega" in prompt:
            return {
                "empresa": "Acciona IT",
                "puesto": "Analista Funcional",
                "dolores_empresa": ["Optimizar procesos y automatizar pruebas"],
                "requisitos": [
                    {"nombre": "SQL", "tipo": "tecnica", "estado": "MATCH_DIRECTO", "approved": True, "justificacion": "SQL"},
                    {"nombre": "Docker", "tipo": "tecnica", "estado": "MATCH_DIRECTO", "approved": True, "justificacion": "Docker"},
                    {"nombre": "Python", "tipo": "tecnica", "estado": "MATCH_DIRECTO", "approved": True, "justificacion": "Python"},
                    {"nombre": "Relevar y documentar requerimientos", "tipo": "RESPONSABILIDAD", "estado": "MATCH_DIRECTO", "approved": True, "justificacion": "Relevamiento"},
                    {"nombre": "Metodologías ágiles", "tipo": "blanda", "estado": "MATCH_DIRECTO", "approved": True, "justificacion": "Agile"}
                ],
                "match_score": 85.0
            }
        elif "Redactor" in prompt:
            return {
                "cv_markdown": """# SAM ORTEGA
## PERFIL
Analista Funcional y QA Técnico con experiencia en metodologías ágiles.

## EXPERIENCIA LABORAL

### VELORA SISTEMAS — Analista Funcional Sr.
• Relevé y documenté requerimientos funcionales para sistemas bancarios.
• Diseñé matrices de prueba con SQL y Docker.

### Freelance — Consultor Técnico
• Implementé soluciones con Python y Docker.

### KORVA — Especialista de Soporte Técnico
• Gestioné incidentes y pruebas de integración.

## HABILIDADES TÉCNICAS
• SQL, Docker, Python, Metodologías ágiles, Relevamiento

## EDUCACIÓN
• Licenciatura en Sistemas

## IDIOMAS
• Español nativo, Inglés profesional
""",
                "pitch": "Estimado equipo:\n\nPresento mi postulación.\n\nSaludos cordiales.",
                "pitches_multicanal": {"inmail": "Inmail text", "email": "Email text", "web_ats": "Web ATS text"}
            }
        elif "Auditor" in prompt:
            return {
                "score_ats": 90,
                "score_anti_ia": 100,
                "hallazgos": [],
                "cv_markdown": """# SAM ORTEGA
## PERFIL
Analista Funcional y QA Técnico con experiencia en metodologías ágiles.

## EXPERIENCIA LABORAL

### VELORA SISTEMAS — Analista Funcional Sr.
• Relevé y documenté requerimientos funcionales para sistemas bancarios.
• Diseñé matrices de prueba con SQL y Docker.

### Freelance — Consultor Técnico
• Implementé soluciones con Python y Docker.

### KORVA — Especialista de Soporte Técnico
• Gestioné incidentes y pruebas de integración.

## HABILIDADES TÉCNICAS
• SQL, Docker, Python, Metodologías ágiles, Relevamiento

## EDUCACIÓN
• Licenciatura en Sistemas

## IDIOMAS
• Español nativo, Inglés profesional
""",
                "pitch": "Estimado equipo:\n\nPresento mi postulación.\n\nSaludos cordiales."
            }
        return {"status": "OK"}

    monkeypatch.setattr("core.bridge.AntigravityBridge.run_prompt", fake_run_prompt)


def test_e2e_user_journey_vacancy_clean_and_matrix_structure():
    # Ingesta con texto crudo que incluye basura típica de LinkedIn
    raw_job = """Functional Analyst Jr./Ssr. (R2)
Acciona IT
Ubicación: Belgrano, Buenos Aires
Modalidad: Presencial
Responsabilidades:
- Relevar y documentar requerimientos funcionales
- Diseñar matrices de prueba
Requisitos:
- SQL excluyente
- Metodologías ágiles
- Integraciones tarjetas y QR
Beneficios:
- Prepaga Salud Plus
Las recomendaciones duplican tus probabilidades
Mira a quién conoces
Otros perfiles vistos
Analista de Soporte - Gador"""

    post_id = create_postulacion("Analista Funcional", "Acciona IT", raw_job)
    asyncio.run(server.run_pipeline_task(post_id))

    detail = client.get(f"/api/ofertas/{post_id}").json()
    clean = detail.get("contenido_limpio", "")
    # No debe contener basura de LinkedIn
    assert "Otros perfiles vistos" not in clean
    assert "Mira a quién conoces" not in clean
    assert "Relevar y documentar" in clean

    # Matriz estructurada
    matriz = detail.get("analisis", {}).get("matriz_oferta", {})
    assert "herramientas" in matriz or "responsabilidades" in matriz


def test_e2e_tag_cycling_and_cv_recalculation_sync():
    # Crear postulación de prueba
    post_id = create_postulacion("Tech QA", "Test SA", "Requisitos: Docker, Python. Responsabilidades: Testing funcional.")
    asyncio.run(server.run_pipeline_task(post_id))

    # Ciclar estado de un tag
    cycle_res = client.post(f"/api/ofertas/{post_id}/tags/Docker/cycle-state")
    assert cycle_res.status_code == 200
    data = cycle_res.json()
    assert data["nuevo_estado"] in ("TRANSFERIBLE", "MATCH_DIRECTO", "BRECHA_REAL")

    # Recalcular CV
    recalc_res = client.post(f"/api/ofertas/{post_id}/recalculate-cv")
    assert recalc_res.status_code == 200
