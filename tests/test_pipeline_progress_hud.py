"""Tests TDD para el Desglose del Pipeline con Heartbeat en Tiempo Real y Cronómetro Verídico (Task 3)."""
import pytest

from tests.cv_final_prueba import pdf_de_prueba
import asyncio
import time
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
async def test_track_phase_emits_periodic_heartbeats():
    """Valida que el context manager track_phase emita eventos periódicos con elapsed_seconds y datos verídicos."""
    emitted = []

    async def mock_broadcast(event_type, data):
        emitted.append((event_type, data))

    start_time = time.time()
    with patch("server.broadcast_event", side_effect=mock_broadcast):
        # Ejecutar track_phase simulando un trabajo de 2.2 segundos (al menos 2 heartbeats de ~1s)
        async with server.track_phase(
            postulacion_id=101,
            phase=1,
            agent="ESTRATEGA",
            agent_title="Subagente 1: Estratega de Brechas",
            step="Analizando vacante...",
            base_detail="Extrayendo requisitos y calculando afinidad",
            base_percent=15,
            target_percent=45,
            start_time=start_time,
            heartbeat_interval=0.5,  # intervalo rápido para tests deterministas
        ):
            await asyncio.sleep(1.2)

    heartbeats = [data for ev, data in emitted if ev == "status_heartbeat"]
    assert len(heartbeats) >= 2, f"Se esperaban al menos 2 heartbeats, se obtuvieron {len(heartbeats)}"

    hb1 = heartbeats[0]
    hb2 = heartbeats[1]

    assert hb1["id"] == 101
    assert hb1["phase"] == 1
    assert hb1["agent"] == "ESTRATEGA"
    assert "Estratega" in hb1["agent_title"]
    assert hb1["heartbeat"] is True
    assert "elapsed_seconds" in hb1
    assert "phase_elapsed_seconds" in hb1
    assert "substep" in hb1
    assert "detail" in hb1
    assert "model" in hb1
    assert "effort" in hb1
    assert hb1["percent"] >= 15
    assert hb1["percent"] <= 45

    # Los tiempos deben ser crecientes o iguales (monótonos)
    assert hb2["elapsed_seconds"] >= hb1["elapsed_seconds"]
    assert hb2["phase_elapsed_seconds"] >= hb1["phase_elapsed_seconds"]


@pytest.mark.asyncio
async def test_track_phase_cleans_up_cleanly_on_exception():
    """Valida que la corrutina de heartbeat se cancele limpiamente si ocurre una excepción en el bloque."""
    emitted = []

    async def mock_broadcast(event_type, data):
        emitted.append((event_type, data))

    start_time = time.time()
    with patch("server.broadcast_event", side_effect=mock_broadcast):
        with pytest.raises(RuntimeError, match="Error simulado en LLM"):
            async with server.track_phase(
                postulacion_id=102,
                phase=2,
                agent="REDACTOR",
                agent_title="Subagente 2: Redactor",
                step="Redactando...",
                base_detail="Formulando viñetas",
                base_percent=50,
                target_percent=80,
                start_time=start_time,
                heartbeat_interval=0.1,
            ):
                await asyncio.sleep(0.15)
                raise RuntimeError("Error simulado en LLM")

    # Si la cancelación no fuera limpia, quedaría una tarea huérfana o saltaría un error en el teardown
    # Esperar un tick para asegurar que el event loop procesó la cancelación
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_run_pipeline_task_with_heartbeat_and_milestones():
    """Verifica que run_pipeline_task emita heartbeats periódicos, hitos con métricas reales y complete."""
    fake_postulacion = {
        "id": 888,
        "titulo": "Posición Técnica",
        "empresa": "Confidencial",
        "contenido_oferta": "Buscamos Desarrollador Python Senior con experiencia en FastAPI y Docker.",
        "status": "PENDING",
    }

    mock_analysis = {
        "puesto": "Senior Python Backend",
        "empresa": "TechCorp",
        "dolores_empresa": ["Escalabilidad"],
        "requisitos": [
            {"nombre": "Python", "tipo": "EXCLUYENTE", "estado": "MATCH_DIRECTO", "score": 1.0},
            {"nombre": "Docker", "tipo": "DESEABLE", "estado": "BRECHA_REAL", "score": 0.0},
        ],
        "match_score": 85.0,
    }

    mock_redaccion = {
        "cv_markdown": "# CV Senior Python\n* Logro cuantificado en Python...",
        "pitch": "Estimado equipo...",
    }

    mock_auditoria = (
        "# CV Senior Python Auditado",
        "Estimado equipo Auditado...",
        {"score_ats": 94, "aprobado": True, "remediado": False},
    )

    emitted = []

    async def mock_broadcast(event_type, data):
        emitted.append((event_type, data))

    with patch("server.get_postulacion", return_value=fake_postulacion), \
         patch("server.update_postulacion"), \
         patch("server.broadcast_event", side_effect=mock_broadcast), \
         patch("server.StrategistAgent") as MockStrategist, \
         patch("server.WriterAgent") as MockWriter, \
         patch("server.AuditorAgent") as MockAuditor:

        mock_strat_inst = AsyncMock()
        async def slow_analyze(content, *, datos_de_terceros=False):
            assert datos_de_terceros is True
            await asyncio.sleep(0.25)
            return mock_analysis
        mock_strat_inst.analyze_offer.side_effect = slow_analyze
        MockStrategist.return_value = mock_strat_inst

        mock_writer_inst = AsyncMock()
        async def slow_writer(an, *, datos_de_terceros=False):
            assert datos_de_terceros is True
            await asyncio.sleep(0.25)
            return mock_redaccion
        mock_writer_inst.generate_cv_and_pitch.side_effect = slow_writer
        mock_writer_inst.bridge = MagicMock()
        mock_writer_inst.bridge.last_telemetry = {"model": "gemini-3.8-flash-high", "tokens": 120}
        MockWriter.return_value = mock_writer_inst

        mock_auditor_inst = AsyncMock()
        async def slow_auditor(*args, **kwargs):
            await asyncio.sleep(0.25)
            return mock_auditoria
        mock_auditor_inst.audit_and_remediate.side_effect = slow_auditor
        mock_auditor_inst.bridge = MagicMock()
        mock_auditor_inst.bridge.last_telemetry = {"model": "gemini-3.8-flash-high", "tokens": 150}
        MockAuditor.return_value = mock_auditor_inst

        # Patch heartbeat interval a 0.1s durante el test
        with patch("server.HEARTBEAT_INTERVAL", 0.1):
            await server.run_pipeline_task(888)

    status_events = [data for ev, data in emitted if ev == "status"]
    heartbeat_events = [data for ev, data in emitted if ev == "status_heartbeat"]
    complete_events = [data for ev, data in emitted if ev == "complete"]

    # Fases principales respetan el contrato anterior
    assert len(status_events) == 3

    # Heartbeats periódicos y/o hitos fueron emitidos
    assert len(heartbeat_events) >= 3

    # Verificar que al menos un evento contiene métricas reales de Estratega
    estratega_milestones = [
        hb for hb in heartbeat_events
        if hb.get("agent") == "ESTRATEGA" and hb.get("milestone") is True
    ]
    assert len(estratega_milestones) >= 1
    em = estratega_milestones[0]
    assert "metrics" in em
    assert em["metrics"]["total_requisitos"] == 2
    assert em["metrics"]["match_directo"] == 1
    assert em["metrics"]["brechas"] == 1
    assert "2 requisitos" in em["detail"]

    # Verificar hito de Redactor
    redactor_milestones = [
        hb for hb in heartbeat_events
        if hb.get("agent") == "REDACTOR" and hb.get("milestone") is True
    ]
    assert len(redactor_milestones) >= 1
    rm = redactor_milestones[0]
    assert "metrics" in rm
    assert rm["metrics"]["cv_chars"] > 0
    # pitch_chars: ya no hay pitch (CV desde el maestro, 14/11)

    # Evento de compleción contiene elapsed_seconds
    assert len(complete_events) == 1
    assert complete_events[0]["status"] == "READY"
    assert "elapsed_seconds" in complete_events[0]
    assert complete_events[0]["elapsed_seconds"] >= 0


@pytest.mark.asyncio
async def test_recalculate_cv_task_with_heartbeat_and_milestones():
    """Valida que recalculate_cv_task emita telemetría de heartbeat e hitos en fases 2 y 3."""
    fake_postulacion = {
        "id": 777,
        "titulo": "Senior Python Backend",
        "empresa": "TechCorp",
        "dolores_empresa": [],
        "requisitos": [{"nombre": "FastAPI", "tipo": "EXCLUYENTE", "estado": "MATCH_DIRECTO", "score": 1.0}],
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

    emitted = []

    async def mock_broadcast(event_type, data):
        emitted.append((event_type, data))

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

        with patch("server.HEARTBEAT_INTERVAL", 0.1):
            await server.recalculate_cv_task(777)

    status_events = [data for ev, data in emitted if ev == "status"]
    heartbeat_events = [data for ev, data in emitted if ev == "status_heartbeat"]
    complete_events = [data for ev, data in emitted if ev == "complete"]

    assert len(status_events) == 2
    assert len(complete_events) == 1
    assert "elapsed_seconds" in complete_events[0]

    # Milestones para Redactor y Auditor deben existir
    redactor_milestones = [hb for hb in heartbeat_events if hb.get("agent") == "REDACTOR" and hb.get("milestone")]
    auditor_milestones = [hb for hb in heartbeat_events if hb.get("agent") == "AUDITOR" and hb.get("milestone")]
    assert len(redactor_milestones) == 1
    assert len(auditor_milestones) == 1


def test_frontend_handles_status_heartbeat_and_timer_sync():
    """Comprueba que static/app.js y static/index.html manejen status_heartbeat y el cronómetro verídico."""
    html_content = Path("static/index.html").read_text(encoding="utf-8")
    js_content = Path("static/app.js").read_text(encoding="utf-8")

    # Elementos en HTML
    assert 'id="hudTimer"' in html_content
    assert 'id="hudDetail"' in html_content
    assert 'id="agenticProgressHUD"' in html_content

    # app.js debe escuchar status_heartbeat
    assert "status_heartbeat" in js_content

    # app.js debe contemplar data.elapsed_seconds para sincronización verídica
    assert "elapsed_seconds" in js_content
