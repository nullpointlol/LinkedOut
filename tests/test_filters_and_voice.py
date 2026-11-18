"""Tests para verificar narración en primera persona, indicadores dobles y buscador/filtros dinámicos con fecha."""
import pytest
from pathlib import Path
from core.prompts_evaluacion import WRITER_PROMPT
from core.writer import WriterAgent
from core.auditor import AuditorAgent, scan_anti_ai_writing, evaluate_ats_compliance

INDEX_PATH = Path("static/index.html")
APP_JS_PATH = Path("static/app.js")


def test_writer_prompt_mandates_first_person_narrative():
    """WRITER_PROMPT debe exigir narración en primera persona singular y prohibir tercera persona."""
    prompt_upper = WRITER_PROMPT.upper()
    assert "PRIMERA PERSONA" in prompt_upper
    assert "TERCERA PERSONA" in prompt_upper  # Prohibición explícita


def test_writer_fallback_uses_first_person_verbs():
    """El fallback determinístico del redactor debe utilizar verbos en primera persona ('Reduje', 'Centralicé', etc.)."""
    writer = WriterAgent()
    fallback = writer._deterministic_fallback(
        puesto="Analista Técnico Funcional",
        empresa="TechCorp",
        analysis={"dolores_empresa": [], "requisitos": []},
        profile=writer.load_candidate_profile()
    )
    cv_text = fallback["cv_markdown"]

    # Verbos en primera persona
    first_person_indicators = [
        "Reduje", "Centralicé", "Aceleré", "Monitoreé", "Automaticé", "Elevé",
        "Definí", "Gestioné", "Conduje", "Relevé", "Coordiné", "Estandaricé",
        "Diseñé", "Desarrollé", "Transformé", "Implementé", "Ejecuté", "Lideré"
    ]
    matches = [v for v in first_person_indicators if v in cv_text]
    assert len(matches) >= 4, f"Se esperaban viñetas en primera persona, encontrados: {matches} en:\n{cv_text}"

    # No debe tener verbos en tercera persona
    third_person_indicators = ["Redujo en un", "Centralizó la", "Aceleró en un", "Monitoreó infra", "Automatizó rep"]
    for tp in third_person_indicators:
        assert tp not in cv_text, f"Se detectó verbo en tercera persona: {tp}"


def test_auditor_provides_dual_separated_indicators():
    """AuditorAgent.audit debe proveer diagnostico_documento (ATS/Keywords) y evaluacion_profesional separados."""
    auditor = AuditorAgent()
    cv_sample = """# SAM ORTEGA
ANALISTA
Ciudad Ejemplo | 1155550100 | a@a.test
## PERFIL
Analista en primera persona.
## EXPERIENCIA
### Freelance | 2025 - 2026
• **Automatización:** Implementé flujos en Python.
### VELORA SISTEMAS | 2018 - 2025
• **Observabilidad:** Centralicé métricas en SQL.
### KORVA | 2011 - 2017
• **Monitoreo:** Monitoreé servidores 24x7.
## HABILIDADES TÉCNICAS
• **Herramientas:** Python, SQL, Docker.
## EDUCACIÓN
• Carrera
## IDIOMAS
• Español, Inglés.
"""
    rep = auditor.audit(cv_sample, "Pitch de prueba", ["Python", "SQL", "Docker", "Kubernetes"])

    assert "diagnostico_documento" in rep, "Falta la clave 'diagnostico_documento'"
    assert "evaluacion_profesional" in rep, "Falta la clave 'evaluacion_profesional'"

    doc = rep["diagnostico_documento"]
    assert "ats_score" in doc
    assert "keyword_coverage" in doc
    assert "missing_keywords" in doc
    assert "Kubernetes" in doc["missing_keywords"]


def test_ui_has_search_and_filter_elements():
    """index.html debe contener el buscador, chips de filtro rápido y selector de orden."""
    html = INDEX_PATH.read_text(encoding="utf-8")
    assert 'id="inputBuscarOfertas"' in html
    assert 'id="chipFiltroTodas"' in html
    assert 'id="chipFiltroSinCV"' in html
    assert 'id="chipFiltroListas"' in html
    assert 'id="selectOrdenOfertas"' in html


def test_app_js_implements_filtering_sorting_and_date_formatting():
    """app.js debe definir funciones para búsqueda, ordenamiento y formateo de fecha opaca."""
    js = APP_JS_PATH.read_text(encoding="utf-8")
    assert "function filtrarOfertasLista" in js or "function filtrarYOrdenarOfertas" in js
    assert "function formatearFechaOpaca" in js
    assert "text-zinc-400 font-mono text-xs" in js


def test_scan_anti_ai_penalizes_third_person_verbs():
    cv_with_third_person = """
    • **Automatización:** Desarrolló scripts en Python para extracción de métricas.
    • **Infraestructura:** Optimizó y administró pipelines de CI/CD.
    • **Base de Datos:** Depuró consultas complejas en SQL.
    """
    scan = scan_anti_ai_writing(cv_with_third_person)
    assert not scan["clean"], "Debe detectar verbos en 3ª persona como falta de voz activa en 1ª persona"
    assert scan["anti_ai_score"] < 100, "El score no puede ser 100 si está redactado en 3ª persona"
    detected = " ".join(scan["detected_words"]).lower()
    assert "desarrolló" in detected or "3ª persona" in detected


def test_evaluate_ats_rejects_superficial_single_token_matching():
    cv_text = "Especialista en integraciones de sistemas comerciales."
    # Keyword compuesta donde solo coincide la palabra genérica 'integraciones'
    keywords = ["Integraciones tarjetas, QR y billeteras digitales"]
    ats = evaluate_ats_compliance(cv_text, keywords)
    assert ats["keyword_coverage"] < 1.0, "No debe otorgar 100% de cobertura por coincidir solo con 'integraciones'"
    assert "Integraciones tarjetas, QR y billeteras digitales" in ats["missing_keywords"]

