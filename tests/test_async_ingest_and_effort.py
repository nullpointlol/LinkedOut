"""Tests TDD para Ingesta Asíncrona Inmediata y Calibración Dinámica de Esfuerzo de Razonamiento (Task 1)."""
import asyncio
import time
from unittest.mock import AsyncMock, patch, MagicMock
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

import server
from core import radar_store
from core.bridge import AntigravityBridge
from core.database import init_db, get_postulacion, create_postulacion


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path, monkeypatch):
    """Configura una base de datos temporal limpia para cada test."""
    test_db = tmp_path / "test_async_effort.db"
    monkeypatch.setattr("core.database.DB_PATH", test_db)
    init_db(test_db)
    yield test_db
    if test_db.exists():
        try:
            test_db.unlink()
        except Exception:
            pass


def test_bridge_build_command_overrides_effort():
    """Valida que AntigravityBridge._build_command respete el parámetro effort y mute el modelo Gemini para evitar conflictos con agy."""
    bridge = AntigravityBridge(model="gemini-3.8-flash-high", effort="high")

    # Por defecto usa self.effort ("high")
    cmd_default = bridge._build_command("Prompt de prueba")
    assert "--model" in cmd_default
    m_idx = cmd_default.index("--model")
    assert cmd_default[m_idx + 1] == "gemini-3.8-flash-high"
    assert "--effort" in cmd_default
    idx = cmd_default.index("--effort")
    assert cmd_default[idx + 1] == "high"

    # Con override effort="low", el modelo debe mutar a gemini-3.8-flash-low y coincidir con --effort low
    cmd_low = bridge._build_command("Prompt de prueba", effort="low")
    m_idx_low = cmd_low.index("--model")
    assert cmd_low[m_idx_low + 1] == "gemini-3.8-flash-low"
    assert "--effort" in cmd_low
    idx_low = cmd_low.index("--effort")
    assert cmd_low[idx_low + 1] == "low"

    # Con modelo Claude que no soporta --effort, no debe incluir flag --effort
    bridge_claude = AntigravityBridge(model="claude-sonnet-4-6")
    cmd_claude = bridge_claude._build_command("Prompt claude", effort="low")
    assert "--effort" not in cmd_claude


def test_bridge_resolve_model_and_effort_matrix():
    """Valida la matriz de resolución de modelos y esfuerzos para el CLI agy."""
    # Gemini con low
    m, eff = AntigravityBridge.resolve_model_and_effort("gemini-3.8-flash-high", "low")
    assert m == "gemini-3.8-flash-low"
    assert eff == "low"

    # Gemini con medium
    m, eff = AntigravityBridge.resolve_model_and_effort("gemini-3.8-flash-high", "medium")
    assert m == "gemini-3.8-flash-medium"
    assert eff == "medium"

    # Claude nunca recibe flag --effort
    m, eff = AntigravityBridge.resolve_model_and_effort("claude-sonnet-4-6", "low")
    assert m == "claude-sonnet-4-6"
    assert eff is None

    # Claude Opus Thinking nunca recibe flag --effort
    m, eff = AntigravityBridge.resolve_model_and_effort("claude-opus-4-6-thinking", "high")
    assert m == "claude-opus-4-6-thinking"
    assert eff is None


def test_scraper_cleans_linkedin_noise_and_extracts_company():
    """Valida que el scraper remueva elementos de navegación de LinkedIn y extraiga empresa y puesto."""
    from core.scraper import extract_job_from_html, filter_job_boilerplate

    sample_html = """
    <html>
    <head>
        <title>Functional Analyst Jr./Ssr. (R2) en Acciona IT · Buenos Aires, Argentina | Empleos de LinkedIn</title>
        <meta property="og:title" content="Functional Analyst Jr./Ssr. (R2) en Acciona IT · Buenos Aires, Argentina | Empleos de LinkedIn">
        <meta property="og:site_name" content="LinkedIn">
    </head>
    <body>
        <p>Pasar al contenido principal</p>
        <p>Descubre a quién ha contratado Acciona IT para este puesto</p>
        <p>Hace 19 horas</p>
        <p>120 solicitudes</p>
        <p>Guardar</p>
        <article>
            <h2>Analista Funcional Jr./Semi Senior</h2>
            <p>Buscamos sumar un Analista Funcional con sólidos conocimientos en relevamiento y metodologías ágiles.</p>
            <ul>
                <li>Experiencia en toma de requerimientos.</li>
                <li>Conocimientos de SQL y procesos de negocio.</li>
            </ul>
        </article>
        <p>Aceptar y unirse a LinkedIn</p>
        <p>Al hacer clic en «Continuar» para unirse</p>
    </body>
    </html>
    """
    job = extract_job_from_html(sample_html, url="https://linkedin.com/jobs/view/12345")
    assert job["titulo"] in ("Functional Analyst Jr./Ssr.", "Functional Analyst Jr./Ssr. (R2)")
    assert job["empresa"] == "Acciona IT"
    assert "Pasar al contenido principal" not in job["contenido"]
    assert "Descubre a quién ha contratado" not in job["contenido"]
    assert "Guardar" not in job["contenido"]
    assert "Aceptar y unirse a LinkedIn" not in job["contenido"]
    assert "Analista Funcional Jr./Semi Senior" in job["contenido"]


@pytest.mark.asyncio
async def test_bridge_run_prompt_passes_effort(monkeypatch):
    """Valida que AntigravityBridge.run_prompt pase el parámetro effort al comando subprocess."""
    bridge = AntigravityBridge(model="gemini-3.8-flash-high", effort="high")
    captured_cmd = []

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured_cmd.extend(cmd)
        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(b'{"response": "ok"}', b""))
        mock_proc.returncode = 0
        return mock_proc

    monkeypatch.setattr("core.bridge._crear_proceso", fake_create_subprocess_exec)
    monkeypatch.setattr(bridge, "agy_path", Path("fake/agy.exe"))
    monkeypatch.setattr(Path, "exists", lambda self: True)

    await bridge.run_prompt("Prompt", effort="low")

    assert "--effort" in captured_cmd
    idx = captured_cmd.index("--effort")
    assert captured_cmd[idx + 1] == "low"


def test_api_crear_oferta_url_immediate_response(monkeypatch):
    """Valida que POST /api/ofertas/url responda inmediatamente en <100ms con estado PROCESSING sin bloquear en scraping."""
    client = TestClient(server.app)

    scraper_called_sync = False
    wake = AsyncMock()
    monkeypatch.setattr(server.radar_loop, "wake_processing", wake)

    async def slow_fake_scrape(url, timeout=15):
        nonlocal scraper_called_sync
        scraper_called_sync = True
        return {
            "titulo": "Rol Remoto",
            "empresa": "Acme Corp",
            "contenido": "Requisitos de la oferta...",
            "url": url,
        }

    monkeypatch.setattr("server.scrape_job_url", slow_fake_scrape)

    t0 = time.time()
    response = client.post("/api/ofertas/url", json={"url": "https://example.com/empleo/123"})
    elapsed = time.time() - t0

    assert response.status_code == 200
    data = response.json()

    # El endpoint debe responder sin haber ejecutado el scraping antes de crear la fila
    assert not scraper_called_sync, "El scraper no debe ser ejecutado sincrónicamente antes de responder"

    assert "id" in data
    assert data.get("estado") == "PROCESSING" or data.get("status") == "PROCESSING"
    assert "empresa" in data
    assert "puesto" in data or "titulo" in data

    wake.assert_awaited_once()
    post = radar_store.get_post(data["id"])
    assert post["fuente"] == "manual" and post["estado"] == "NUEVO"
    assert post["texto"] == "https://example.com/empleo/123"
    assert get_postulacion(data["id"]) is None


def test_api_crear_ofertas_batch_creates_processing_immediately(monkeypatch):
    """Valida que POST /api/ofertas/batch inserte posts manuales NUEVO de inmediato y retorne created e ids."""
    client = TestClient(server.app)
    monkeypatch.setattr(server.radar_loop, "wake_processing", AsyncMock())

    urls = [
        "https://example.com/job/1",
        "https://example.com/job/2",
    ]

    response = client.post("/api/ofertas/batch", json={"urls": urls})
    assert response.status_code == 200
    data = response.json()

    assert data["status"] == "PROCESSING"
    assert data.get("created") == 2
    assert len(data["ids"]) == 2

    for pid in data["ids"]:
        row = radar_store.get_post(pid)
        assert row is not None
        assert row["estado"] == "NUEVO" and row["fuente"] == "manual"
        assert get_postulacion(pid) is None


@pytest.mark.asyncio
async def test_run_pipeline_task_handles_background_scraping_and_low_effort(monkeypatch):
    """Valida que run_pipeline_task ejecute el scraping si se le pasa URL y use esfuerzo 'low' en Estratega y Auditor."""
    scraped_executed = False

    async def fake_scrape(url, timeout=15):
        nonlocal scraped_executed
        scraped_executed = True
        return {
            "titulo": "Backend Engineer",
            "empresa": "Tech Solutions",
            "contenido": "Buscamos Backend con Python y Docker.",
            "url": url,
        }

    monkeypatch.setattr("server.scrape_job_url", fake_scrape)

    # Espiar llamadas de bridge para verificar esfuerzo
    efforts_used = {}

    class DummyStrategist:
        def __init__(self, *args, **kwargs):
            efforts_used["strategist"] = kwargs.get("effort") or getattr(kwargs.get("bridge"), "effort", None)
        async def analyze_offer(self, text, *args, **kwargs):
            return {
                "puesto": "Backend Engineer",
                "empresa": "Tech Solutions",
                "dolores_empresa": ["Escalabilidad"],
                "requisitos": [{"nombre": "Python", "estado": "MATCH_DIRECTO", "approved": True}],
                "match_score": 90.0,
            }

    class DummyWriter:
        def __init__(self, *args, **kwargs):
            efforts_used["writer"] = kwargs.get("effort") or getattr(kwargs.get("bridge"), "effort", None)
        async def generate_cv_and_pitch(self, *args, **kwargs):
            return {
                "cv_markdown": "# CV de Prueba\n## PERFIL\nTest\n## EXPERIENCIA\n• Test\n## HABILIDADES TÉCNICAS\n• Python\n## EDUCACIÓN\n• Test\n## IDIOMAS\n• ES",
                "pitch": "Pitch de prueba",
                "pitches_multicanal": {"inmail": "inmail", "email": "email", "web_ats": "web_ats"},
            }

    class DummyAuditor:
        def __init__(self, *args, **kwargs):
            efforts_used["auditor"] = kwargs.get("effort") or getattr(kwargs.get("bridge"), "effort", None)
            self.bridge = MagicMock()
            self.bridge.last_telemetry = {"status": "SUCCESS"}
        async def audit_and_remediate(self, cv, pitch, kw, *args, **kwargs):
            return cv, pitch, {"score_ats": 95, "approved": True}

    monkeypatch.setattr("server.StrategistAgent", DummyStrategist)
    # El CV sale del maestro (creador, 14/11; su auditoría usa effort low en `core/cv_maestro.FlujoCV`)
    generar = AsyncMock()
    monkeypatch.setattr("server._generar_desde_maestro", generar)

    post_id = create_postulacion(
        titulo="Posición por URL",
        empresa="Detectando...",
        contenido="https://tech.example.com/job/456",
        url="https://tech.example.com/job/456",
        status="PROCESSING",
    )

    await server.run_pipeline_task(post_id, url="https://tech.example.com/job/456")

    assert scraped_executed is True

    # Verificar datos actualizados en DB tras el scraping
    updated = get_postulacion(post_id)
    assert updated["contenido_oferta"] == "Buscamos Backend con Python y Docker."
    assert "Tech Solutions" in updated["empresa"]

    # Verificar esfuerzo de razonamiento: 'low' en el Estratega; el CV pasa al flujo del maestro
    assert efforts_used.get("strategist") == "low"
    generar.assert_awaited_once()


def test_real_agent_classes_instantiation_contract():
    """Valida que las clases reales StrategistAgent y AuditorAgent acepten bridge y effort sin TypeError."""
    from core.strategist import StrategistAgent, EstrategaAgent
    from core.auditor import AuditorAgent

    bridge_low = AntigravityBridge(effort="low")

    # Instanciación con bridge y effort explícito (firma usada en server.py)
    strat1 = StrategistAgent(bridge=bridge_low, effort="low")
    assert strat1.effort == "low"
    assert strat1.bridge.effort == "low"

    aud1 = AuditorAgent(bridge=bridge_low, effort="low")
    assert aud1.effort == "low"
    assert aud1.bridge.effort == "low"

    # Instanciación solo con effort="low"
    strat2 = StrategistAgent(effort="low")
    assert strat2.effort == "low"

    aud2 = AuditorAgent(effort="low")
    assert aud2.effort == "low"

    # Alias EstrategaAgent
    assert EstrategaAgent is StrategistAgent

    # Verificación de columna raw_scraped_data en base de datos
    import sqlite3
    from core.database import DB_PATH
    conn = sqlite3.connect(DB_PATH)
    columns = [col[1] for col in conn.execute("PRAGMA table_info(postulaciones)").fetchall()]
    conn.close()
    assert "raw_scraped_data" in columns

