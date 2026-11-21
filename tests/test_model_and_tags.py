"""Pruebas TDD para la selección real de Gemini 3.8 Flash High, telemetría y extracción exhaustiva de etiquetas."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient
from core.bridge import AntigravityBridge, DEFAULT_MODEL
from core.strategist import StrategistAgent, calculate_match_score, calculate_match_score_with_breakdown
from server import app

SAMPLE_DETAILED_OFFER = """
Posición: Senior Technical Business Analyst & Integration Specialist
Empresa: Tech Enterprise SA

Acerca de la empresa:
Somos una compañía global líder en soluciones de software corporativo con más de 15 años en el mercado.

Responsabilidades Principales:
• Relevamiento funcional y técnico exhaustivo con stakeholders y equipos de negocio.
• Definición y documentación de especificaciones técnicas, historias de usuario y diagramas BPMN.
• Automatización de flujos operativos y extracción de datos mediante scripts en Python.
• Mantenimiento y optimización de pipelines de integración continua en Jenkins y GitLab CI.
• Monitoreo de observabilidad, calidad estática en Codacy y análisis de métricas en PagerDuty.
• Coordinación de pases a producción y aseguramiento de trazabilidad operativa.

Requisitos Excluyentes:
• Más de 5 años de experiencia comprobable en análisis funcional técnico de sistemas complejos.
• Dominio avanzado de SQL relacional (PostgreSQL, Oracle o MySQL) para consultas y diagnósticos.
• Experiencia sólida consumiendo y testeando APIs REST y servicios SOAP.
• Metodologías ágiles de trabajo (Scrum / Kanban / Jira).

Requisitos Deseables:
• Experiencia en entornos Cloud (AWS o GCP).
• Conocimientos de orquestación con Docker o Kubernetes.
• Nivel de inglés técnico intermedio/avanzado.

Beneficios y Compensaciones:
• Prepaga Salud Plus para grupo familiar.
• Clases de inglés in-company y refrigerios.
• Tres semanas de vacaciones y bono anual.
"""

def test_bridge_defaults_to_gemini_38_flash_high():
    bridge = AntigravityBridge()
    assert bridge.model == "gemini-3.8-flash-high"
    assert bridge.effort == "high"
    assert DEFAULT_MODEL == "gemini-3.8-flash-high"


def test_bridge_builds_command_with_model_and_skip_permissions():
    bridge = AntigravityBridge()
    cmd = bridge._build_command("Hola mundo", json_mode=True)

    assert "--model" in cmd
    assert "gemini-3.8-flash-high" in cmd
    assert "--dangerously-skip-permissions" in cmd
    assert "--output-format" in cmd
    assert "json" in cmd
    assert "-p" in cmd
    assert "Hola mundo" in cmd


def test_bridge_records_telemetry_on_execution():
    bridge = AntigravityBridge()
    fake_stdout = (
        '{"conversation_id": "test-convo-123", "status": "SUCCESS", '
        '"duration_seconds": 6.5, '
        '"usage": {"input_tokens": 1200, "output_tokens": 400, "thinking_tokens": 350, "total_tokens": 1600}, '
        '"response": "```json\\n{\\"resultado\\": \\"ok\\"}\\n```"}'
    )

    with patch("core.bridge._crear_proceso") as mock_exec:
        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(fake_stdout.encode("utf-8"), b""))
        mock_proc.returncode = 0
        mock_exec.return_value = mock_proc

        import asyncio
        res = asyncio.run(bridge.run_prompt("Test prompt", json_mode=True))

        assert res == {"resultado": "ok"}
        assert bridge.last_telemetry is not None
        assert bridge.last_telemetry["model"] == "gemini-3.8-flash-high"
        assert bridge.last_telemetry["thinking_tokens"] == 350
        assert bridge.last_telemetry["total_tokens"] == 1600
        assert bridge.last_telemetry["duration_seconds"] == 6.5
        assert bridge.last_telemetry["conversation_id"] == "test-convo-123"


def test_bridge_get_model_info_returns_structure():
    bridge = AntigravityBridge()
    info = bridge.get_model_info()

    assert "configured_model" in info
    assert info["configured_model"] == "gemini-3.8-flash-high"
    assert info["display_name"] == "Gemini 3.8 Flash (High)"
    assert info["effort"] == "high"
    assert "cli_available" in info
    assert "available_models" in info
    assert isinstance(info["available_models"], list)
    # Si la CLI está instalada, debe listar gemini-3.8-flash-high
    if info["cli_available"]:
        assert "gemini-3.8-flash-high" in info["available_models"]


def test_api_model_info_endpoint():
    client = TestClient(app)
    res = client.get("/api/model-info")
    assert res.status_code == 200
    data = res.json()
    assert data["configured_model"] == "gemini-3.8-flash-high"
    assert data["effort"] == "high"
    assert "available_models" in data
    assert "last_telemetry" in data


@pytest.mark.asyncio
async def test_strategist_extracts_all_types_with_mocked_llm():
    strategist = StrategistAgent()
    mock_llm_response = {
        "empresa": "Tech Enterprise SA",
        "puesto": "Senior Technical Business Analyst",
        "dolores_empresa": ["Optimizar procesos de CI/CD", "Automatizar tareas manuales"],
        "requisitos": [
            {"nombre": "Relevamiento con stakeholders", "tipo": "RESPONSABILIDAD", "estado": "MATCH_DIRECTO", "justificacion": "Experiencia previa", "equivalencia": None},
            {"nombre": "Automatización con Python", "tipo": "RESPONSABILIDAD", "estado": "MATCH_DIRECTO", "justificacion": "Scripts en Python", "equivalencia": None},
            {"nombre": "Pipelines CI/CD", "tipo": "RESPONSABILIDAD", "estado": "MATCH_DIRECTO", "justificacion": "Jenkins y GitLab", "equivalencia": None},
            {"nombre": "SQL Avanzado", "tipo": "REQUISITO_EXCLUYENTE", "estado": "MATCH_DIRECTO", "justificacion": "Consultas complejas", "equivalencia": None},
            {"nombre": "Consumo de APIs REST", "tipo": "REQUISITO_EXCLUYENTE", "estado": "MATCH_DIRECTO", "justificacion": "Integración APIs", "equivalencia": None},
            {"nombre": "Metodologías Ágiles Scrum", "tipo": "REQUISITO_EXCLUYENTE", "estado": "MATCH_DIRECTO", "justificacion": "Jira y Scrum", "equivalencia": None},
            {"nombre": "Cloud AWS/GCP", "tipo": "REQUISITO_DESEABLE", "estado": "TRANSFERIBLE", "justificacion": "Equivalente on-premise", "equivalencia": "Linux", "approved": False},
            {"nombre": "Docker / Kubernetes", "tipo": "REQUISITO_DESEABLE", "estado": "BRECHA_REAL", "justificacion": "Sin antecedentes", "equivalencia": None, "approved": False},
        ]
    }

    with patch.object(strategist.bridge, "run_prompt", AsyncMock(return_value=mock_llm_response)):
        analisis = await strategist.analyze_offer(SAMPLE_DETAILED_OFFER)

        assert len(analisis["requisitos"]) == 8
        for r in analisis["requisitos"]:
            assert "tipo" in r
            assert r["tipo"] in ("RESPONSABILIDAD", "REQUISITO_EXCLUYENTE", "REQUISITO_DESEABLE")
            assert "approved" in r


def test_html_and_js_contain_telemetry_and_model_elements():
    from pathlib import Path
    html_content = Path("static/index.html").read_text(encoding="utf-8")
    js_content = Path("static/app.js").read_text(encoding="utf-8")

    # Header debe incluir badge y botón para ver telemetría
    assert "btnVerTelemetria" in html_content
    assert "labelModeloActivo" in html_content
    assert "dropdownTelemetria" in html_content or "modalTelemetria" in html_content

    # app.js debe consultar /api/model-info y actualizar telemetría
    assert "/api/model-info" in js_content
    assert "actualizarTelemetriaModelo" in js_content

    # UI debe contener contenedor y lógica para explicar camino al 100% de afinidad
    assert "caminoAl100Container" in html_content
    assert "camino_al_100" in js_content


def test_calculate_match_score_reaches_100_when_all_match_and_explains_gaps():
    # Todos en MATCH_DIRECTO deben dar 100.0%, no estar clavados en 92.0%
    tags_perfect = [{"nombre": "Python", "estado": "MATCH_DIRECTO", "approved": True}]
    score, desglose = calculate_match_score_with_breakdown(tags_perfect)
    assert score == 100.0
    assert calculate_match_score(tags_perfect) == 100.0
    assert len(desglose["brechas"]) == 0
    assert desglose["puntos_faltantes"] == 0.0

    # Con brechas reales, debe detallar exactamente qué falta para llegar al 100
    tags_mixed = [
        {"nombre": "Python", "estado": "MATCH_DIRECTO", "approved": True},
        {"nombre": "Docker", "estado": "BRECHA_REAL", "approved": False},
        {"nombre": "SQL", "estado": "TRANSFERIBLE", "approved": True},
    ]
    score_mix, desglose_mix = calculate_match_score_with_breakdown(tags_mixed)
    assert score_mix < 100.0
    assert "Docker" in [b["nombre"] for b in desglose_mix["brechas"]]
    assert "camino_al_100" in desglose_mix
    assert "Docker" in desglose_mix["camino_al_100"]


def test_api_cycle_state_returns_desglose_afinidad_and_camino_al_100():
    from core.database import create_postulacion, update_postulacion
    client = TestClient(app)
    oferta_id = create_postulacion("Tech Lead Python", "Global Corp", "Python y Docker")
    requisitos = [
        {"nombre": "Python", "estado": "BRECHA_REAL", "approved": False},
        {"nombre": "Docker", "estado": "MATCH_DIRECTO", "approved": True},
    ]
    update_postulacion(oferta_id, requisitos=requisitos)

    # Simular ciclo de tag a MATCH_DIRECTO
    cycle_res = client.post(f"/api/ofertas/{oferta_id}/tags/Python/cycle-state")
    assert cycle_res.status_code == 200
    data = cycle_res.json()
    assert "desglose_afinidad" in data
    assert "camino_al_100" in data["desglose_afinidad"]
    assert "puntos_faltantes" in data["desglose_afinidad"]

    # Detalle de oferta también debe exponer desglose_afinidad
    detail_res = client.get(f"/api/ofertas/{oferta_id}")
    assert detail_res.status_code == 200
    detail = detail_res.json()
    assert "desglose_afinidad" in detail
    assert "camino_al_100" in detail["desglose_afinidad"]
