import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from core.auditor import AuditorAgent

@pytest.mark.asyncio
async def test_audit_and_remediate_sanitizes_multichannel_pitches():
    auditor = AuditorAgent()
    cv_md = (
        "# Juan Pérez\n"
        "## PERFIL\nAnalista Funcional.\n"
        "## EXPERIENCIA\n### Empresa Uno | 2020 - 2024\n• **Rol:** Diseñé flujos de prueba con SQL en 2024.\n"
        "## HABILIDADES TÉCNICAS\n• **Tecnologías:** SQL, Jira.\n"
        "## EDUCACIÓN\n• Licenciatura en Sistemas.\n"
        "## IDIOMAS\n• Español: Nativo."
    )
    pitch = "Pitch básico."
    dirty_multichannel = {
        "inmail": "Hola! Cuento con sólida trayectoria articulando soluciones sin fricción.",
        "email": "Estimados, escribo articulando sinergias con foco en la excelencia.",
        "web_ats": "Postulo para coordinar proyectos con comunicación fluida de punta a punta."
    }
    
    cv_clean, pitch_clean, pitches_clean, report = await auditor.audit_and_remediate_all(
        cv_md, pitch, dirty_multichannel, keywords=["SQL", "Jira"], max_attempts=0
    )
    
    assert "articulando" not in pitches_clean["inmail"].lower()
    assert "sin fricción" not in pitches_clean["inmail"].lower()
    assert "foco en" not in pitches_clean["email"].lower()
    assert "de punta a punta" not in pitches_clean["web_ats"].lower()
    assert isinstance(pitches_clean, dict)
    assert "inmail" in pitches_clean
    assert "email" in pitches_clean
    assert "web_ats" in pitches_clean

@pytest.mark.asyncio
async def test_remediate_text_deterministic_replaces_cliches_and_spelling():
    auditor = AuditorAgent()
    sample = "Priorizé y optimizé procesos articulando sinergias de punta a punta con foco en la entrega."
    cleaned = auditor.remediate_text_deterministic(sample)

    assert "prioricé" in cleaned.lower()
    assert "optimicé" in cleaned.lower()
    assert "priorizé" not in cleaned.lower()
    assert "optimizé" not in cleaned.lower()
    assert "articulando sinergias" not in cleaned.lower()
    assert "de punta a punta" not in cleaned.lower()
    assert "con foco en" not in cleaned.lower()

@pytest.mark.asyncio
async def test_inmail_length_constraint_enforced_under_300_chars():
    auditor = AuditorAgent()
    cv_md = "# Candidato\n## PERFIL\nDev.\n## EXPERIENCIA\n• Dev\n## HABILIDADES TÉCNICAS\n• Python\n## EDUCACIÓN\n• Uni\n## IDIOMAS\n• ES"
    pitch = "Pitch"
    long_dirty_inmail = "Hola! " + ("articulando soluciones sin fricción de punta a punta con foco en la excelencia " * 4)
    dirty_multichannel = {
        "inmail": long_dirty_inmail,
        "email": "Email corto",
        "web_ats": "Web ATS corta"
    }

    cv_clean, pitch_clean, pitches_clean, report = await auditor.audit_and_remediate_all(
        cv_md, pitch, dirty_multichannel, keywords=["Python"], max_attempts=0
    )

    assert len(pitches_clean["inmail"]) <= 300
    assert "sin fricción" not in pitches_clean["inmail"].lower()

@pytest.mark.asyncio
async def test_audit_and_remediate_all_with_none_or_empty_pitches():
    auditor = AuditorAgent()
    cv_md = "# Candidato\n## PERFIL\nDev.\n## EXPERIENCIA\n• Dev\n## HABILIDADES TÉCNICAS\n• Python\n## EDUCACIÓN\n• Uni\n## IDIOMAS\n• ES"
    pitch = "Pitch"

    cv_clean, pitch_clean, pitches_clean, report = await auditor.audit_and_remediate_all(
        cv_md, pitch, None, keywords=["Python"], max_attempts=0
    )

    assert pitches_clean == {}
    assert cv_clean == cv_md
    assert pitch_clean == pitch

@pytest.mark.asyncio
async def test_server_pipeline_persists_sanitized_pitches():
    import server
    from unittest.mock import patch, AsyncMock, MagicMock

    post_data = {
        "id": 9999,
        "cv_markdown": "# CV",
        "pitch": "Pitch",
        "pitches_multicanal": {
            "inmail": "Hola! articulando sinergias sin fricción.",
            "email": "Email con foco en la excelencia.",
            "web_ats": "ATS de punta a punta."
        },
        "requisitos": [{"nombre": "Python", "approved": True}]
    }

    mock_update = MagicMock()
    mock_emit = AsyncMock()

    with patch("server.get_postulacion", return_value=post_data), \
         patch("server.update_postulacion", mock_update), \
         patch("server.emit_milestone", mock_emit), \
         patch("server.broadcast_event", AsyncMock()), \
         patch("core.bridge.AntigravityBridge.run_prompt", new_callable=AsyncMock, return_value={"cv_markdown": "# CV", "pitch": "Pitch"}):

        await server.remediate_cv_task(9999)

    assert mock_update.called
    # El dictamen rechazado deja la postulación en ERROR, pero los
    # pitches saneados se persisten igual: se mira la última escritura que los incluye.
    kwargs = [c.kwargs for c in mock_update.call_args_list if "pitches_multicanal" in c.kwargs][-1]
    assert "pitches_multicanal" in kwargs
    saved_pitches = kwargs["pitches_multicanal"]
    assert "articulando" not in saved_pitches["inmail"].lower()
    assert "foco en" not in saved_pitches["email"].lower()
    assert "de punta a punta" not in saved_pitches["web_ats"].lower()

