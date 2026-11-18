import pytest
from core.strategist import StrategistAgent, calculate_match_score
from core.writer import WriterAgent
from core.auditor import AuditorAgent, scan_anti_ai_writing, evaluate_ats_compliance

def test_calculate_match_score():
    tags = [
        {"nombre": "Python", "estado": "MATCH_DIRECTO", "approved": True},
        {"nombre": "SQL", "estado": "MATCH_DIRECTO", "approved": True},
        {"nombre": "AWS", "estado": "TRANSFERIBLE", "approved": True},    # Cuenta ponderado
        {"nombre": "Docker", "estado": "TRANSFERIBLE", "approved": False}, # No cuenta si no está aprobado
        {"nombre": "C++", "estado": "BRECHA_REAL", "approved": False},
    ]
    # 5 requisitos (08/11: transferible sin confirmar vale 0,4):
    # 2 directos (2.0) + 1 transferible aprobado (0.70) + 1 sin aprobar (0.4) = 3.10 / 5.0 = 62%
    score = calculate_match_score(tags)
    assert score == 62.0

    # Si se aprueba Docker:
    tags[3]["approved"] = True
    # 2.0 + 2 * 0.70 = 3.4 / 5.0 = 68%
    new_score = calculate_match_score(tags)
    assert new_score > score
    assert new_score == 68.0

def test_scan_anti_ai_writing_detects_prohibited_words():
    clean_text = "Desarrollé una automatización en Python para reducir en un 40% el tiempo de generación de reportes."
    dirty_text = "Delve into the pivotal tapestry of fostering holistic synergies. Moreover, this stands as a testament."
    
    clean_report = scan_anti_ai_writing(clean_text)
    assert clean_report["clean"] is True
    assert len(clean_report["detected_words"]) == 0
    assert clean_report["anti_ai_score"] == 100

    dirty_report = scan_anti_ai_writing(dirty_text)
    assert dirty_report["clean"] is False
    assert "delve" in dirty_report["detected_words"]
    assert "pivotal" in dirty_report["detected_words"]
    assert "tapestry" in dirty_report["detected_words"]
    assert "fostering" in dirty_report["detected_words"]
    assert "moreover" in dirty_report["detected_words"]
    assert dirty_report["anti_ai_score"] < 50

def test_evaluate_ats_compliance():
    valid_cv = """
# ALEX RIVERA
alex.rivera@example.com | Buenos Aires, Argentina

## PERFIL
Analista Técnico Funcional con más de 7 años de experiencia.

## EXPERIENCIA
### Analista Técnico Funcional | VELORA SISTEMAS | 2018 - 2025
• **Calidad de Código:** Automatizó la recolección de métricas de **Codacy** en más de **90 aplicaciones** reduciendo un **30%** la deuda técnica.
• **Optimización CI/CD:** Minimizó tiempos de despliegue en un **40%** implementando pipelines automatizados en **Jenkins**.

## HABILIDADES TÉCNICAS
• Python, SQL, Jenkins, Codacy, APIs

## EDUCACIÓN
• Tecnicatura en Informática - 2016

## IDIOMAS
• Inglés avanzado, Español nativo
"""
    keywords = ["Python", "SQL", "Jenkins", "Codacy"]
    result = evaluate_ats_compliance(valid_cv, keywords)
    assert result["ats_score"] >= 85
    assert result["has_all_sections"] is True
    assert result["keyword_coverage"] == 1.0

@pytest.mark.asyncio
async def test_auditor_remediation_cycle():
    auditor = AuditorAgent()
    # Texto con palabras prohibidas
    dirty_cv = """
# ALEX RIVERA
## PERFIL
Delving into the pivotal tapestry of software development.
## EXPERIENCIA
### Analista | 2020 - 2024
• Fostered synergies.
## HABILIDADES TÉCNICAS
• Python
## EDUCACIÓN
• Terciario
## IDIOMAS
• Español
"""
    # Deterministic remediation purge
    cleaned_cv, audit = auditor.remediate_deterministic(dirty_cv, keywords=["Python"])
    assert "delve" not in cleaned_cv.lower()
    assert "pivotal" not in cleaned_cv.lower()
    assert "tapestry" not in cleaned_cv.lower()
    assert audit["anti_ai_score"] > 80


@pytest.mark.asyncio
async def test_strategist_marks_deterministic_fallback_when_llm_fails():
    """El Radar necesita distinguir el fallback determinístico de un análisis real."""
    class BridgeCaido:
        effort = "low"

        async def run_prompt(self, *a, **k):
            raise RuntimeError("agy no responde")

    res = await StrategistAgent(bridge=BridgeCaido()).analyze_offer("Buscamos Analista Funcional\nAcme\n- SQL")
    assert res["analisis_fallback"] is True
    assert "requisitos" in res


@pytest.mark.asyncio
async def test_strategist_passes_timeout_only_when_given():
    vistos = []

    class Bridge:
        effort = "high"

        async def run_prompt(self, prompt, **k):
            vistos.append(k.get("timeout_seconds"))
            return {"requisitos": [{"nombre": "SQL", "estado": "MATCH_DIRECTO"}]}

    agente = StrategistAgent(bridge=Bridge())
    await agente.analyze_offer("Buscamos Analista")
    await agente.analyze_offer("Buscamos Analista", timeout_seconds=420)
    assert vistos == [None, 420]  # el flujo manual conserva el timeout por defecto del bridge
