"""Tests para Task 3: Fallback Dinámico no Sesgado y Observabilidad de Contingencia."""
import pytest
from pathlib import Path
from unittest.mock import AsyncMock
from fastapi.testclient import TestClient

from core.writer import WriterAgent
from core.database import create_postulacion, update_postulacion
from server import app

ROOT_DIR = Path(__file__).resolve().parent.parent


@pytest.mark.asyncio
async def test_writer_fallback_flags_is_fallback_and_uses_candidate_data(monkeypatch):
    """Verifica que ante fallo del LLM, el fallback determinístico activa las banderas de observabilidad

    (is_fallback=True, fallback_reason) y redacta el CV y Pitch exclusivamente con los datos
    del candidato suministrado, sin ningún dato hardcodeado de Sam Ortega ni sus empresas.
    """
    agent = WriterAgent()
    # Forzar fallo del bridge
    agent.bridge.run_prompt = AsyncMock(side_effect=RuntimeError("Timeout simulado en AntigravityBridge"))

    custom_profile = {
        "personales": """Laura Gómez

Córdoba, Argentina | +54 9 351 987-6543 | laura.gomez@testmail.test | LinkedIn: https://linkedin.com/in/persona-ficticia-03

## EDUCACIÓN
• Licenciatura en Ciencias de la Computación - Universidad Nacional de Córdoba (2017 - 2022)

## IDIOMAS
• Español: Nativo
• Inglés: Avanzado (C1)
""",
        "experiencia": """# Experiencia profesional

## Empresa Uno
### Ingeniera de Software Senior | 2022 - 2025
• Diseñé microservicios de alta concurrencia con Python y FastAPI procesando millones de peticiones diarias.
• Implementé pipelines automatizados de CI/CD reduciendo tiempos de despliegue en un 45%.
• Lideré la migración de infraestructura a contenedores Docker y Kubernetes.

## Empresa Dos
### Desarrolladora Backend | 2019 - 2022
• Desarrollé APIs RESTful y optimicé consultas complejas en bases de datos PostgreSQL.
• Implementé pruebas automatizadas de integración elevando la cobertura al 92%.
""",
    }

    # Interceptar carga de perfil para que devuelva custom_profile
    monkeypatch.setattr(agent, "load_candidate_profile", lambda: custom_profile)

    analysis = {
        "puesto": "Tech Lead Python",
        "empresa": "Acme Innovations",
        "dolores_empresa": ["Optimizar latencia de microservicios", "Reducir tiempo de despliegues"],
        "requisitos": [
            {"nombre": "Python", "estado": "MATCH_DIRECTO", "approved": True},
            {"nombre": "FastAPI", "estado": "MATCH_DIRECTO", "approved": True},
            {"nombre": "Docker", "estado": "MATCH_DIRECTO", "approved": True},
            {"nombre": "PostgreSQL", "estado": "MATCH_DIRECTO", "approved": True},
        ],
    }

    res = await agent.generate_cv_and_pitch(analysis)

    # 1. Banderas de observabilidad
    assert res.get("is_fallback") is True, "Debe marcar is_fallback=True ante fallo del LLM"
    assert "Timeout simulado" in str(res.get("fallback_reason")), (
        f"Debe registrar el motivo del fallo en fallback_reason, obtuvo: {res.get('fallback_reason')}"
    )

    cv_md = res["cv_markdown"]
    pitch = res["pitch"]

    # 2. Datos del candidato arbitrario presentes
    assert "Laura Gómez" in cv_md, "El CV debe contener el nombre del candidato provisto"
    assert "Empresa Uno" in cv_md, "El CV debe contener las empresas del candidato provisto"
    assert "laura.gomez@testmail.test" in cv_md, "El CV debe contener los datos de contacto del candidato provisto"
    assert "Laura Gómez" in pitch, "El pitch debe estar firmado por el candidato provisto"

    # 3. Cero datos hardcodeados de Sam Ortega
    assert "Sam Ortega" not in cv_md, "No debe contener Sam Ortega"
    assert "VELORA SISTEMAS" not in cv_md, "No debe contener VELORA SISTEMAS"
    assert "KORVA" not in cv_md, "No debe contener KORVA"
    assert "0000-0000" not in cv_md, "No debe contener el teléfono fijo de Sam"
    assert "sam.ortega" not in cv_md, "No debe contener el email de Sam"
    assert "Instituto de Ejemplo" not in cv_md, "No debe contener la institución de Sam"
    assert "12 años" not in cv_md, "No debe contener los 12 años hardcodeados"


@pytest.mark.asyncio
async def test_writer_success_flags_is_fallback_false():
    """Verifica que cuando el LLM responde con éxito, is_fallback es False y fallback_reason es None."""
    agent = WriterAgent()
    mock_llm_result = {
        "cv_markdown": """# Laura Gómez
TECH LEAD PYTHON
Córdoba | laura@test.test

## PERFIL
Ingeniera de software con experiencia liderando equipos.

## EXPERIENCIA
### Tech Lead | Empresa Uno | 2022 - 2025
• **Arquitectura:** Diseñé soluciones escalables.

## HABILIDADES TÉCNICAS
• **Desarrollo:** Python, FastAPI.

## EDUCACIÓN
• Licenciatura en Computación

## IDIOMAS
• Español: Nativo
""",
        "pitch": "Hola equipo de Acme, postulo con gran interés...",
        "pitches_multicanal": {
            "inmail": "Hola! Vi la búsqueda de Tech Lead. Saludos! Laura Gómez",
            "email": "Asunto: Postulación Tech Lead - Laura Gómez\n\nEstimados...",
            "web_ats": "Postulo a la posición de Tech Lead en Acme Innovations...",
        },
    }
    agent.bridge.run_prompt = AsyncMock(return_value=mock_llm_result)

    analysis = {
        "puesto": "Tech Lead Python",
        "empresa": "Acme Innovations",
        "dolores_empresa": [],
        "requisitos": [],
    }

    res = await agent.generate_cv_and_pitch(analysis)

    assert res.get("is_fallback") is False, "Debe marcar is_fallback=False cuando el LLM responde con éxito"
    assert res.get("fallback_reason") is None, "fallback_reason debe ser None en caso de éxito"


def test_api_ofertas_exposes_fallback_status():
    """Verifica que el endpoint GET /api/ofertas/{id} expone fielmente is_fallback y fallback_reason

    calculándolo a partir de la postulación, reporte de auditoría o telemetría.
    """
    client = TestClient(app)

    # Caso A: Postulación generada en modo contingencia (fallback)
    post_id = create_postulacion(
        titulo="Ingeniero de Datos",
        empresa="DataCorp",
        contenido="Buscamos Data Engineer con experiencia en Spark y Python...",
    )
    update_postulacion(
        post_id,
        telemetry={
            "model": "gemini-2.5-flash",
            "status": "DETERMINISTIC_FALLBACK",
            "is_fallback": True,
            "fallback_reason": "Timeout de respuesta de 45s",
        },
        reporte_auditoria={
            "score_ats": 85,
            "is_fallback": True,
            "fallback_reason": "Timeout de respuesta de 45s",
        },
        status="READY",
    )

    resp = client.get(f"/api/ofertas/{post_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_fallback"] is True, "El endpoint debe exponer is_fallback=True para contingencias"
    assert data["fallback_reason"] == "Timeout de respuesta de 45s"

    # Caso B: Postulación generada normalmente por el LLM
    post_id_ok = create_postulacion(
        titulo="Desarrollador Backend",
        empresa="NormalCorp",
        contenido="Buscamos Desarrollador Backend...",
    )
    update_postulacion(
        post_id_ok,
        telemetry={
            "model": "gemini-2.5-flash",
            "status": "SUCCESS",
            "is_fallback": False,
        },
        reporte_auditoria={
            "score_ats": 92,
            "is_fallback": False,
        },
        status="READY",
    )

    resp_ok = client.get(f"/api/ofertas/{post_id_ok}")
    assert resp_ok.status_code == 200
    data_ok = resp_ok.json()
    assert data_ok["is_fallback"] is False, "El endpoint debe exponer is_fallback=False para casos normales"
    assert data_ok.get("fallback_reason") is None


def test_ui_contains_fallback_badge():
    """Verifica que el markup HTML de index.html y el JavaScript de app.js implementan

    el badge #badgeModoFallback y la lógica reactiva de visibilidad según data.is_fallback.
    """
    html_path = ROOT_DIR / "static" / "index.html"
    js_path = ROOT_DIR / "static" / "app.js"

    html_content = html_path.read_text(encoding="utf-8")
    js_content = js_path.read_text(encoding="utf-8")

    # 1. HTML contiene el elemento #badgeModoFallback
    assert 'id="badgeModoFallback"' in html_content, "index.html debe contener el elemento #badgeModoFallback"
    # Rediseño §8: «Modo Contingencia (Fallback)» → «CV hecho sin IA (la IA no respondió)»
    assert "CV hecho sin IA" in html_content, "index.html debe incluir el texto o etiqueta de contingencia"

    # 2. JS gestiona badgeModoFallback en función de data.is_fallback
    assert "badgeModoFallback" in js_content, "app.js debe referenciar badgeModoFallback"
    assert "is_fallback" in js_content, "app.js debe evaluar la propiedad is_fallback de la oferta"
