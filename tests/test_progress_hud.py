"""Tests automatizados para el Agentic Progress HUD, Stepper de 3 Fases y Telemetría SSE."""
import pytest

from tests.cv_final_prueba import pdf_de_prueba
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from pathlib import Path
import server
from core.bridge import AntigravityBridge


@pytest.fixture(autouse=True)
def flujo_maestro_falso(monkeypatch):
    """CV desde el maestro (14/11): reemplaza al Redactor y al Auditor. Flujo falso que pasa por la auditoría
    (fase 3 del HUD) y aprueba; sin IA real."""
    from types import SimpleNamespace as _NS
    from unittest.mock import AsyncMock as _AM
    from core.titulo_cv import DecisionTitulo

    class _Flujo:
        def __init__(self, **kw):
            self.kw, self.segundos, self.historial = kw, {}, []

        async def generar(self):
            if self.kw.get("al_ajustar"):
                self.kw["al_ajustar"]("# ANA PRUEBA - CV de prueba")
            if self.kw.get("al_auditar"):
                await self.kw["al_auditar"]()
            return _NS(markdown="# ANA PRUEBA - CV de prueba", correcciones=0,
                       reporte={"approved": True, "ats_score": 95}, keywords_cv={}, segundos={},
                       pdf=pdf_de_prueba())

    monkeypatch.setattr(server.cv_maestro, "FlujoCV", _Flujo)
    monkeypatch.setattr(server, "asegurar_anios_por_area", _AM(return_value=[]))
    monkeypatch.setattr(server, "decidir_titulo", _AM(return_value=DecisionTitulo(
        caso=1, clave=None, titulo="Analista", roles_reales=(), puesto_oferta="Analista")))



@pytest.mark.asyncio
async def test_run_pipeline_task_emits_structured_hud_events():
    """Verifica que run_pipeline_task emita eventos de estado estructurados con phase, percent, agent, etc."""
    fake_postulacion = {
        "id": 999,
        "titulo": "Posición Técnica",
        "empresa": "Confidencial",
        "contenido_oferta": "Buscamos Desarrollador Python Senior con experiencia en FastApi y Docker.",
        "status": "PENDING",
    }

    mock_analysis = {
        "puesto": "Senior Python Backend",
        "empresa": "TechCorp",
        "dolores_empresa": ["Escalabilidad"],
        "requisitos": [{"nombre": "Python", "tipo": "EXCLUYENTE", "estado": "MATCH_DIRECTO", "score": 1.0}],
        "match_score": 95.0,
    }

    mock_redaccion = {
        "cv_markdown": "# CV Senior Python",
        "pitch": "Estimado equipo...",
    }

    mock_auditoria = (
        "# CV Senior Python Auditado",
        "Estimado equipo Auditado...",
        {"score_ats": 96, "aprobado": True, "remediado": False},
    )

    emitted_events = []

    async def mock_broadcast(event_type, data):
        emitted_events.append((event_type, data))

    with patch("server.get_postulacion", return_value=fake_postulacion), \
         patch("server.update_postulacion"), \
         patch("server.broadcast_event", side_effect=mock_broadcast), \
         patch("server.StrategistAgent") as MockStrategist, \
         patch("server.WriterAgent") as MockWriter, \
         patch("server.AuditorAgent") as MockAuditor:

        mock_strat_inst = AsyncMock()
        mock_strat_inst.analyze_offer.return_value = mock_analysis
        MockStrategist.return_value = mock_strat_inst

        mock_writer_inst = AsyncMock()
        mock_writer_inst.generate_cv_and_pitch.return_value = mock_redaccion
        mock_writer_inst.bridge = MagicMock()
        mock_writer_inst.bridge.last_telemetry = {"model": "gemini-3.8-flash-high", "tokens": 120}
        MockWriter.return_value = mock_writer_inst

        mock_auditor_inst = AsyncMock()
        mock_auditor_inst.audit_and_remediate.return_value = mock_auditoria
        mock_auditor_inst.bridge = MagicMock()
        mock_auditor_inst.bridge.last_telemetry = {"model": "gemini-3.8-flash-high", "tokens": 150}
        MockAuditor.return_value = mock_auditor_inst

        await server.run_pipeline_task(999)

    status_events = [data for ev_type, data in emitted_events if ev_type == "status"]
    complete_events = [data for ev_type, data in emitted_events if ev_type == "complete"]

    # 3 Fases emitidas
    assert len(status_events) == 3

    # Fase 1: Estratega
    assert status_events[0]["phase"] == 1
    assert status_events[0]["total_phases"] == 3
    assert status_events[0]["percent"] == 15
    assert status_events[0]["agent"] == "ESTRATEGA"
    assert "Estratega" in status_events[0]["agent_title"]
    assert "detail" in status_events[0]
    assert status_events[0]["model"] == AntigravityBridge.active_model

    # Fase 2: Redactor
    assert status_events[1]["phase"] == 2
    assert status_events[1]["percent"] == 50
    assert status_events[1]["agent"] == "REDACTOR"
    assert "Redactor" in status_events[1]["agent_title"]
    assert "detail" in status_events[1]

    # Fase 3: Auditor
    assert status_events[2]["phase"] == 3
    assert status_events[2]["percent"] == 85
    assert status_events[2]["agent"] == "AUDITOR"
    assert "Auditor" in status_events[2]["agent_title"]
    assert "Auditando" in status_events[2]["step"]

    # Finalización
    assert len(complete_events) == 1
    assert complete_events[0]["phase"] == 4
    assert complete_events[0]["percent"] == 100
    assert complete_events[0]["status"] == "READY"
    assert "éxito" in complete_events[0]["message"]


@pytest.mark.asyncio
async def test_recalculate_cv_task_emits_structured_hud_events():
    """Verifica que recalculate_cv_task emita eventos estructurados para la regeneración."""
    fake_postulacion = {
        "id": 999,
        "titulo": "Senior Python Backend",
        "empresa": "TechCorp",
        "dolores_empresa": [],
        "requisitos": [],
        "match_score": 90.0,
        "status": "READY",
    }

    mock_redaccion = {
        "cv_markdown": "# CV Recalculado",
        "pitch": "Estimado equipo...",
    }

    mock_auditoria = (
        "# CV Recalculado Auditado",
        "Estimado equipo Recalculado...",
        {"score_ats": 98, "aprobado": True, "remediado": False},
    )

    emitted_events = []

    async def mock_broadcast(event_type, data):
        emitted_events.append((event_type, data))

    with patch("server.get_postulacion", return_value=fake_postulacion), \
         patch("server.update_postulacion"), \
         patch("server.broadcast_event", side_effect=mock_broadcast), \
         patch("server.WriterAgent") as MockWriter, \
         patch("server.AuditorAgent") as MockAuditor:

        mock_writer_inst = AsyncMock()
        mock_writer_inst.generate_cv_and_pitch.return_value = mock_redaccion
        mock_writer_inst.bridge = MagicMock()
        mock_writer_inst.bridge.last_telemetry = None
        MockWriter.return_value = mock_writer_inst

        mock_auditor_inst = AsyncMock()
        mock_auditor_inst.audit_and_remediate.return_value = mock_auditoria
        mock_auditor_inst.bridge = MagicMock()
        mock_auditor_inst.bridge.last_telemetry = None
        MockAuditor.return_value = mock_auditor_inst

        await server.recalculate_cv_task(999)

    status_events = [data for ev_type, data in emitted_events if ev_type == "status"]
    complete_events = [data for ev_type, data in emitted_events if ev_type == "complete"]

    assert len(status_events) == 2
    assert status_events[0]["phase"] == 2
    assert status_events[0]["agent"] == "REDACTOR"
    assert status_events[1]["phase"] == 3
    assert status_events[1]["agent"] == "AUDITOR"

    assert len(complete_events) == 1
    assert complete_events[0]["percent"] == 100
    assert complete_events[0]["status"] == "READY"


def test_index_html_contains_hud_elements():
    """Valida que static/index.html contenga todos los elementos del nuevo HUD y Stepper."""
    html_path = Path("static/index.html")
    assert html_path.exists()
    content = html_path.read_text(encoding="utf-8")

    assert 'id="agenticProgressHUD"' in content
    assert 'id="hudProgressBar"' in content
    assert 'id="hudPercent"' in content
    assert 'id="hudTimer"' in content
    assert 'id="hudAgentBadge"' in content
    assert 'id="hudStepTitle"' in content
    assert 'id="hudDetail"' in content
    assert 'id="hudModelBadge"' in content
    assert 'id="stepPill-1"' in content
    assert 'id="stepPill-2"' in content
    assert 'id="stepPill-3"' in content


def test_app_js_contains_hud_controllers():
    """Valida que static/app.js defina los controladores del cronómetro y del HUD."""
    js_path = Path("static/app.js")
    assert js_path.exists()
    content = js_path.read_text(encoding="utf-8")

    assert "iniciarCronometroHUD" in content
    assert "detenerCronometroHUD" in content
    assert "actualizarHUDProgreso" in content
    assert "actualizarNodosStepper" in content
    assert "completarHUDProgreso" in content
    assert "ocultarHUDProgreso" in content
