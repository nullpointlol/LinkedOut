import re
from collections import Counter

import pytest
from unittest.mock import AsyncMock, MagicMock
from core.auditor import AuditorAgent, evaluate_ats_compliance, scan_anti_ai_writing


SAMPLE_CV_72_ATS = """# ALEX RIVERA
ANALISTA SENIOR DE PROCESOS
San Isidro, Buenos Aires, Argentina | +54 9 11 5555-0100 | alex.rivera@example.com

## PERFIL
Profesional con sólida experiencia en optimización de procesos de negocio y transformación digital.

## EXPERIENCIA
### Analista Funcional Técnico | Freelance | 2025 - 2026
• **Automatización de Procesos:** Diseñé flujos de trabajo con Python reduciendo tiempos operativos.
• **Garantía de Calidad:** Supervisé relevamientos funcionales.

### Analista Técnico Funcional | VELORA SISTEMAS | 2018 - 2025
• **Gestión de Procesos:** Participé en análisis funcional de incidentes críticos.
• **Base de Datos:** Ejecuté consultas SQL para análisis de datos.

### Operador de Monitoreo | KORVA | 2011 - 2017
• **Monitoreo 24x7:** Supervisé infraestructura con ServiceNow y Nagios.
• **Soporte Bilingüe:** Atendí escalamientos en inglés para cuentas internacionales.

## HABILIDADES TÉCNICAS
• Python, SQL, ServiceNow

## EDUCACIÓN
• Tecnicatura en Informática

## IDIOMAS
• Español nativo, Inglés intermedio
"""


def test_auditor_rejects_cv_with_sub_80_ats():
    """Un CV con ATS < 80% DEBE reprobar y requerir remediación, sin ser salvado por un 100% en Anti-IA."""
    auditor = AuditorAgent()
    keywords = ["BPMN", "Bizagi", "Visio", "Jira", "Confluence", "Lean Six Sigma", "RPA", "Scrum", "Kanban", "KPIs"]
    # Este CV no tiene métricas cuantitativas empíricas y le faltan muchas keywords -> ATS < 80
    report = auditor.audit(SAMPLE_CV_72_ATS, "Pitch de prueba", keywords)

    assert report["ats_score"] < 80.0
    assert report["approved"] is False, "Un CV con ATS < 80% jamás debe aprobarse como pase a entrevista."
    assert report["verdict"] == "REQUIERE REMEDIACIÓN"


def test_auditor_rejects_cv_with_sub_80_keyword_coverage():
    """Una cobertura de palabras clave aprobadas menor a 80% DEBE reprobar la auditoría."""
    auditor = AuditorAgent()
    keywords = [
        "Relevamiento AS IS", "Diseño TO BE", "BPMN 2.0", "Bizagi", "Visio",
        "Jira", "Confluence", "Lean Six Sigma", "RPA", "Scrum", "Kanban",
        "KPIs", "Gestión de Stakeholders", "Automatización"
    ]
    report = auditor.audit(SAMPLE_CV_72_ATS, "Pitch de prueba", keywords)

    assert report["diagnostico_documento"]["keyword_coverage"] < 0.80
    assert report["approved"] is False, "Una cobertura documental menor al 80% no debe aprobarse."
    assert report["verdict"] == "REQUIERE REMEDIACIÓN"


def test_no_artificial_2500_char_truncation():
    """Un CV de más de 2500 caracteres con viñetas concisas y 3 empresas canónicas NO debe ser penalizado en ATS ni truncado."""
    long_cv = SAMPLE_CV_72_ATS + "\n" + ("• **Gestión de Calidad:** Implementé controles de trazabilidad y métricas de desempeño operativo.\n" * 16)
    char_count = len(long_cv)
    assert char_count > 2500

    eval_result = evaluate_ats_compliance(long_cv, ["Python", "SQL", "ServiceNow"])
    # No debe tener penalización por longitud fija si las viñetas son concisas
    assert "longitud" in eval_result
    assert "caracteres" in eval_result["longitud"]
    # Debe proveer estimación de lectura en minutos
    assert "lectura_estimada_min" in eval_result["longitud"]


@pytest.mark.asyncio
@pytest.mark.parametrize("intentos", [1, 3])
@pytest.mark.parametrize("progreso", [False, True])
async def test_circuit_breaker_caps_automatic_remediation_attempts(intentos, progreso):
    """Cada destino tiene un freno y el total queda acotado aun con progreso."""
    mock_bridge = MagicMock()

    async def responder(prompt, **kwargs):
        # Cambiar texto sin resolver los hallazgos permite agotar el límite real.
        linea = re.search(r"^(\d+): (.+)$", prompt, flags=re.MULTILINE)
        return {"lineas": [{"n": int(linea[1]), "texto": linea[2] + " Registro."}]
                if progreso else [], "pitch": ""}

    mock_bridge.run_prompt = AsyncMock(side_effect=responder)

    auditor = AuditorAgent(bridge=mock_bridge)
    keywords = ["BPMN", "Bizagi", "Visio", "Jira", "Confluence", "Lean", "RPA", "Scrum", "KPIs", "Tableau"]

    # Ejecutar ciclo de auditoría y remediación
    cv_res, pitch_res, report = await auditor.audit_and_remediate(
        SAMPLE_CV_72_ATS, "Pitch inicial", keywords, max_attempts=intentos
    )

    # En el contrato nuevo, todas las secciones marcadas se consolidan en 1 sola llamada
    assert mock_bridge.run_prompt.call_count == 1
    call_prompt = mock_bridge.run_prompt.call_args[0][0]
    assert "HABILIDADES" in call_prompt
    assert "PERFIL" in call_prompt
    assert "EXPERIENCIA" in call_prompt
    assert pitch_res == "Pitch inicial"
    assert all(empresa in cv_res for empresa in ("Freelance", "VELORA SISTEMAS", "KORVA"))
    assert report["approved"] is False
    # Debe reportar hallazgos unificados claros
    assert "hallazgos_unificados" in report
    assert len(report["hallazgos_unificados"]["keywords_faltantes"]) > 0


@pytest.mark.asyncio
async def test_auditor_zero_deterministic_tampering_rule_of_gold():
    """Regla de Oro: Si el LLM no subsana todas las brechas, NO se inyecta texto determinístico programático."""
    mock_bridge = MagicMock()
    # Simular respuesta LLM
    mock_bridge.run_prompt = AsyncMock(return_value={
        "cv_markdown": SAMPLE_CV_72_ATS,
        "pitch": "Pitch"
    })

    auditor = AuditorAgent(bridge=mock_bridge)
    keywords = ["BPMN", "Bizagi", "Visio", "Lean Six Sigma"]

    cv_res, pitch_res, report = await auditor.audit_and_remediate(
        SAMPLE_CV_72_ATS, "Pitch", keywords, max_attempts=1
    )

    # El texto devuelto debe ser exactamente el producido por el LLM, sin inyecciones forzadas por código
    assert "Bizagi" not in cv_res, "No se debe inyectar texto determinístico si el LLM no lo incluyó."
    assert report["approved"] is False
    assert report["verdict"] == "REQUIERE REMEDIACIÓN"


def test_remediation_endpoint_triggers_task(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from server import app
    import server
    import core.database as db

    # DB aislada
    test_db = tmp_path / "test_remediation.db"
    monkeypatch.setattr(db, "DB_PATH", test_db)
    # Mockear remediate_cv_task para respuesta inmediata
    mock_task = AsyncMock()
    monkeypatch.setattr(server, "remediate_cv_task", mock_task)
    db.init_db()

    post_id = db.create_postulacion(
        titulo="Analista Senior",
        empresa="Tech Co",
        contenido="Oferta con BPMN y Bizagi",
        status="READY"
    )

    client = TestClient(app)
    resp = client.post(f"/api/ofertas/{post_id}/remediar")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "REMEDIATING"
    assert data["id"] == post_id


def test_api_detalle_oferta_returns_matriz_oferta(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from server import app
    import core.database as db

    test_db = tmp_path / "test_matriz.db"
    monkeypatch.setattr(db, "DB_PATH", test_db)
    db.init_db()

    post_id = db.create_postulacion(
        titulo="Analista de Procesos",
        empresa="Tech Co",
        contenido="Requisitos: BPMN, Bizagi, Scrum",
        status="READY"
    )
    reqs = [
        {"nombre": "BPMN 2.0", "categoria": "Herramientas", "estado": "MATCH_DIRECTO", "approved": True},
        {"nombre": "Scrum", "categoria": "Metodologías", "estado": "MATCH_DIRECTO", "approved": True},
        {"nombre": "Rediseño AS IS / TO BE", "categoria": "Responsabilidades", "estado": "TRANSFERIBLE", "approved": True}
    ]
    db.update_postulacion(post_id, requisitos=reqs, analisis={"requisitos": reqs})

    client = TestClient(app)
    resp = client.get(f"/api/ofertas/{post_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert "analisis" in data
    assert "matriz_oferta" in data["analisis"]
    matriz = data["analisis"]["matriz_oferta"]
    assert "herramientas" in matriz
    assert "metodologias" in matriz
    assert "responsabilidades" in matriz
    assert any(r["nombre"] == "BPMN 2.0" for r in matriz["herramientas"])


