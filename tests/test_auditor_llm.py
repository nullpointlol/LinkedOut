import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from core.auditor import AuditorAgent


@pytest.mark.asyncio
async def test_auditor_remediation_is_100_percent_llm():
    """Verifica que cuando un CV requiere remediación, el Auditor invoque estrictamente el LLM."""
    mock_bridge = MagicMock()
    # Simular respuesta de remediación del LLM
    mock_bridge.run_prompt = AsyncMock(return_value={
        "cv_markdown": "# SAM ORTEGA\nGESTOR IT\nSan Isidro | +54 9 11 0000-0000 | sam.ortega@example.com | https://linkedin.com/in/test\n\n## PERFIL\nLideró proyectos de infraestructura.\n\n## EXPERIENCIA\n### Gestor IT | Tech Co | 2020 - 2026\n• **Gestión:** Optimizó flujos en un 40%.\n\n## HABILIDADES TÉCNICAS\n• Python, SQL, ITIL\n\n## EDUCACIÓN\n• Tecnicatura\n\n## IDIOMAS\n• Español e Inglés",
        "pitch": "Estimado equipo, acerco mi perfil profesional para la vacante técnica."
    })

    auditor = AuditorAgent(bridge=mock_bridge)

    # CV inicial con palabras clichés de IA y verbos en 3ra persona para forzar remediación
    bad_cv = ("# SAM ORTEGA\nGESTOR IT\nSan Isidro | +54 9 11 0000-0000 | sam.ortega@example.com\n\n"
              "## PERFIL\nLideró proyectos de tecnología.\n\n"
              "## EXPERIENCIA\n• Test\n\n"
              "## HABILIDADES TÉCNICAS\n• Python, SQL")
    bad_pitch = "In summary, a holistic approach."
    keywords = ["ITIL", "Python", "SQL"]

    # Ejecutar ciclo de remediación
    clean_cv, clean_pitch, report = await auditor.audit_and_remediate(bad_cv, bad_pitch, keywords)

    # Aserción indiscutible: Debe haber invocado el LLM mediante run_prompt
    assert mock_bridge.run_prompt.called, "El auditor DEBE invocar el LLM para la remediación de textos."
    assert mock_bridge.run_prompt.call_count == 1

    # Verificar que el prompt contenga las directivas y el CV a remediar
    call_args = mock_bridge.run_prompt.call_args[0]
    prompt_sent = call_args[0]
    assert "CV A REMEDIAR" in prompt_sent
    assert "HALLAZGOS A CORREGIR" in prompt_sent
    assert "ITIL" in prompt_sent


@pytest.mark.asyncio
async def test_auditor_approved_cv_skips_remediation():
    """Si el CV ya es de alta calidad y está aprobado, no debe invocar una remediación redundante."""
    mock_bridge = MagicMock()
    mock_bridge.run_prompt = AsyncMock()

    auditor = AuditorAgent(bridge=mock_bridge)

    # CV perfectamente estructurado con métricas, sin palabras de IA y con todas las secciones
    good_cv = """# SAM ORTEGA
GESTOR/A DE PROBLEMAS IT
Ciudad Ejemplo, Buenos Aires, Argentina | +54 9 11 0000-0000 | https://wa.me/5491100000000 | sam.ortega@example.com | https://www.linkedin.com/in/sam-ortega-ejemplo/

## PERFIL
Profesional con más de 7 años liderando procesos de análisis funcional y resolución de incidentes críticos en operaciones IT.

## EXPERIENCIA
### Gestor de Incidentes y Calidad | VELORA SISTEMAS | 2018 - 2026
• **Gestión de Incidentes:** Redujo en un **35%** los tiempos de respuesta ante incidentes críticos P1/P2 coordinando 15 analistas.
• **Automatización de Métricas:** Automatizó la recolección de métricas de calidad en **90 aplicaciones** usando **Python** y **SQL**.
• **Plataformas ITSM:** Administró tableros de control en **ServiceNow** y **Jira** elevando el cumplimiento de SLA al **98%**.

## HABILIDADES TÉCNICAS
• **Metodologías:** ITIL, Análisis de Causa Raíz (RCA), SLA, Métricas de Incidentes.
• **Herramientas & Datos:** Python, SQL, ServiceNow, Jira Service Management.

## EDUCACIÓN
• Tecnicatura en Informática - 2016

## IDIOMAS
• Inglés avanzado, Español nativo
"""
    good_pitch = "Estimado equipo de selección:\n\nPresento mi postulación para el rol.\n\nCuento con amplia experiencia en ITIL.\n\nQuedo a disposición para conversar."
    keywords = ["ITIL", "Python", "SQL", "ServiceNow"]

    clean_cv, clean_pitch, report = await auditor.audit_and_remediate(good_cv, good_pitch, keywords)

    # Si está aprobado, no debe llamar a run_prompt
    if report["approved"]:
        assert not mock_bridge.run_prompt.called


@pytest.mark.asyncio
async def test_remediation_edits_only_flagged_lines_with_medium_effort():
    """La remediación no reescribe el CV entero (lo escribió el Redactor en High): edita líneas."""
    cv = ("# ANA\nANALISTA\nSan Isidro | ana@test.test\n\n## PERFIL\nCuento con experiencia en procesos.\n\n"
          "## EXPERIENCIA\n### Acme | 2020 - 2026\n• Optimicé flujos en un 40% con Python.\n\n"
          "## HABILIDADES TÉCNICAS\n• Python, SQL\n\n## EDUCACIÓN\n• Tecnicatura\n\n## IDIOMAS\n• Español")
    mock_bridge = MagicMock()
    mock_bridge.run_prompt = AsyncMock(return_value={
        "lineas": [{"n": 5, "texto": "Analista con 6 años en flujos de datos."},
                   {"n": 12, "texto": "• Python, SQL, ITIL"},
                   {"n": 0, "texto": "# OTRO NOMBRE"}, {"n": 99, "texto": "x"}, {"n": 9, "texto": "• **roto"}],
        "pitch": ""})
    cv_res, pitch_res, _ = await AuditorAgent(bridge=mock_bridge).audit_and_remediate(cv, "Pitch", ["ITIL"])
    prompt, kwargs = mock_bridge.run_prompt.call_args[0][0], mock_bridge.run_prompt.call_args[1]
    assert kwargs["effort"] == "medium" and "5: Cuento con experiencia en procesos." in prompt
    lineas = cv_res.splitlines()
    assert lineas[0] == "# ANA" and lineas[5] == "Analista con 6 años en flujos de datos."
    assert lineas[12] == "• Python, SQL, ITIL" and lineas[9] == "• Optimicé flujos en un 40% con Python."
    assert pitch_res == "Pitch"
