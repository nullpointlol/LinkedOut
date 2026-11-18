"""CV en inglés: detección del idioma de la oferta, Redactor, Auditor y keywords del ATS."""
import pytest

from core.auditor import AuditorAgent, evaluate_ats_compliance, scan_anti_ai_writing
from core.idioma import EN, ES, idioma_cv_de_oferta, idioma_de_cv, idioma_de_texto, seccion_canonica
from core.writer import WriterAgent, ensure_cv_length

OFERTA_EN = """We're Hiring: Data Analyst (Remote). You will work with our team to build dashboards in Power BI
and write SQL queries. Requirements: 3+ years of experience with SQL and Python, strong communication skills."""
OFERTA_ES = """Buscamos Analista de Datos para nuestro equipo. Requisitos: experiencia de 3 años con SQL y Python,
inglés avanzado excluyente. Enviá tu CV a rrhh@empresa.test."""

CV_EN = """# ANA TEST
DATA ANALYST

## SUMMARY
Data analyst with ten years in transactional insurance systems and SQL reporting.

## EXPERIENCE

### Acme
Analyst | 2018 - Present
• Wrote Python scripts that query REST APIs and cut the audit of 90 applications from two days to one hour.
• Fixed data issues with SQL and wrote a guide the support team still uses.
• Diagnosed ESB integration incidents with PagerDuty and Insomnia.
• Triaged 24x7 alerts for US and European accounts.

## TECHNICAL SKILLS
• **Data:** SQL, Python, Power BI.

## EDUCATION
• Computer Science degree.

## LANGUAGES
• Spanish: Native.
• English: Advanced.
"""

CV_EN_IA = CV_EN.replace(
    "Data analyst with ten years", "Results-driven data analyst with ten years").replace(
    "cut the audit of 90 applications from two days to one hour.", "reducing the overall time.").replace(
    "wrote a guide the support team still uses.", "ensuring data consistency.").replace(
    "with PagerDuty and Insomnia.", "with PagerDuty, improving efficiency.").replace(
    "for US and European accounts.", "for US accounts, ensuring uptime.")


@pytest.mark.parametrize("texto, idioma", [
    (OFERTA_EN, EN),
    (OFERTA_ES, ES),
    (OFERTA_ES.replace("Enviá tu CV a", "Enviá tu CV en inglés a"), EN),
    ("Buscamos analista. Enviar curriculum (en idioma inglés) a rrhh@x.test", EN),
    ("Analista funcional. Please send your resume in English.", EN),
    ("Analista funcional. Excluyente: English CV.", EN),
    ("", ES),
])
def test_cv_language_comes_from_the_offer(texto, idioma):
    assert idioma_cv_de_oferta(texto) == idioma


def test_sections_are_recognized_in_both_languages():
    assert seccion_canonica("## PROFESSIONAL SUMMARY") == "PERFIL"
    assert seccion_canonica("## Work Experience") == "EXPERIENCIA"
    assert seccion_canonica("## HABILIDADES TÉCNICAS") == "HABILIDADES"
    assert seccion_canonica("## EDUCATION") == "EDUCACION"
    assert seccion_canonica("## LANGUAGES") == "IDIOMAS"
    assert idioma_de_cv(CV_EN) == EN and idioma_de_cv("## PERFIL\nx\n## EXPERIENCIA\ny") == ES
    assert idioma_de_texto("Hi, I am applying for the role and I have the experience you need.") == EN


def test_english_cv_scores_like_a_spanish_one():
    r = evaluate_ats_compliance(CV_EN, ["SQL", "Python", "Power BI"], expected_companies=["Acme"])
    assert r["has_all_sections"] and r["missing_sections"] == []
    assert r["dimensiones"]["escaneabilidad"] == 20.0
    assert r["dimensiones"]["metricas_empiricas"] > 0
    assert scan_anti_ai_writing(CV_EN)["clean"] is True


def test_english_ai_patterns_are_detected():
    detectadas = " | ".join(scan_anti_ai_writing(CV_EN_IA)["detected_words"])
    assert "simetría sintáctica" in detectadas
    assert "apertura formulaica" in detectadas
    assert "impacto vago" in detectadas


def test_deterministic_fix_keeps_english_cv_in_english():
    agente = AuditorAgent(bridge=object())
    cv = CV_EN.replace("Wrote Python scripts", "Leveraged Python scripts")
    assert "Used Python scripts" in agente.remediate_text_deterministic(cv)
    assert "aprovechando" not in agente.remediate_text_deterministic(cv.replace("Leveraged", "leveraging"))


def test_english_cv_keeps_english_default_headers():
    cv = "# ANA\nANALYST\n\n## SUMMARY\nAnalyst.\n\n## EXPERIENCE\n### Acme\n• Built x.\n"
    salida = ensure_cv_length(cv)
    assert "## EDUCATION" in salida and "## LANGUAGES" in salida
    assert "## EDUCACIÓN" not in salida and "## IDIOMAS" not in salida


class _Bridge:
    def __init__(self, respuesta):
        self.respuesta = respuesta
        self.prompts = []

    async def run_prompt(self, prompt, json_mode=True, **kw):
        self.prompts.append(prompt)
        self.kwargs = kw
        return self.respuesta


@pytest.mark.asyncio
async def test_writer_asks_for_english_and_returns_keyword_terms():
    bridge = _Bridge({
        "cv_markdown": CV_EN, "pitch": "Hi",
        "pitches_multicanal": {"inmail": "Hi", "email": "Subject: x", "web_ats": "I am"},
        "keywords_cv": {"Tableros en Power BI (MATCH_DIRECTO)": "Power BI dashboards", "vacío": ""},
    })
    analisis = {"puesto": "Data Analyst", "empresa": "X", "idioma_cv": EN,
                "requisitos": [{"nombre": "Tableros en Power BI", "estado": "MATCH_DIRECTO"}]}
    r = await WriterAgent(bridge=bridge).generate_cv_and_pitch(analisis, profile={"experiencia": "", "personales": ""})
    assert "IDIOMA DEL CV: INGLÉS" in bridge.prompts[0] and "SUMMARY, EXPERIENCE" in bridge.prompts[0]
    assert r["idioma_cv"] == EN and r["keywords_cv"] == {"Tableros en Power BI": "Power BI dashboards"}


@pytest.mark.asyncio
async def test_writer_in_spanish_does_not_mention_english():
    bridge = _Bridge({"cv_markdown": "## PERFIL\nx", "pitch": "Hola",
                      "pitches_multicanal": {"inmail": "a", "email": "b", "web_ats": "c"}})
    r = await WriterAgent(bridge=bridge).generate_cv_and_pitch(
        {"puesto": "Analista", "requisitos": []}, profile={"experiencia": "", "personales": ""})
    assert "IDIOMA DEL CV: INGLÉS" not in bridge.prompts[0] and r["idioma_cv"] == ES
    assert "keywords_cv" not in r


def test_ats_keywords_use_the_english_terms_of_the_cv():
    from server import _keywords_del_cv
    analisis = {"idioma_cv": EN, "keywords_cv": {"Tableros en Power BI": "Power BI dashboards"}}
    assert _keywords_del_cv(["Tableros en Power BI", "SQL"], analisis) == ["Power BI dashboards", "SQL"]
    assert _keywords_del_cv(["Tableros en Power BI"], {**analisis, "idioma_cv": ES}) == ["Tableros en Power BI"]
    assert _keywords_del_cv(["SQL"], None) == ["SQL"]


@pytest.mark.asyncio
async def test_writer_with_third_party_offer_runs_agy_outside_the_repo():
    """Regenerar un CV del Radar: la oferta va delimitada y agy corre en un directorio vacío (con
    permisos de herramientas, en el repo seguía lo que pedía la oferta y se colgaba)."""
    from pathlib import Path
    bridge = _Bridge({"cv_markdown": CV_EN, "pitch": "Hi", "pitches_multicanal": {"inmail": "a", "email": "b", "web_ats": "c"}})
    await WriterAgent(bridge=bridge).generate_cv_and_pitch(
        {"puesto": "Analyst", "idioma_cv": EN, "requisitos": []},
        profile={"experiencia": "", "personales": ""}, datos_de_terceros=True)
    cwd = Path(bridge.kwargs["cwd"])
    assert "<oferta>" in bridge.prompts[0]
    assert Path.cwd() not in cwd.parents and cwd != Path.cwd()
