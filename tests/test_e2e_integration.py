import os
import pytest
from core.strategist import StrategistAgent, calculate_match_score
from core.writer import WriterAgent
from core.auditor import AuditorAgent
from core.docx_generator import compile_markdown_to_docx

SAMPLE_JOB_OFFER = """
Posición: Senior Technical Business Analyst / Data & DevOps Integrator
Empresa: Mercado Global Inc.

Acerca del rol:
Buscamos un Analista Técnico Funcional Sénior para liderar el relevamiento y la automatización de nuestros procesos críticos de calidad y pipelines de CI/CD.

Responsabilidades y Requisitos Clave:
• Más de 5 años transformando necesidades operativas en requerimientos funcionales y técnicos accionables.
• Experiencia sólida en automatizaciones con Python para extracción de datos y consumo de APIs REST/SOAP.
• Dominio de bases de datos relacionales y consultas complejas en SQL (PostgreSQL / MySQL / Oracle).
• Mantenimiento y optimización de flujos de integración continua (Jenkins, GitLab CI o GitHub Actions).
• Experiencia trabajando con herramientas de calidad estática de código (Codacy) y observabilidad (PagerDuty o Grafana).
• Deseable: Experiencia en entornos Cloud (AWS o GCP) y orquestación con Docker / Kubernetes.
• Enfoque metódico, orientación a métricas de impacto y excelente comunicación técnica.
"""

@pytest.mark.asyncio
@pytest.mark.skipif(
    os.getenv("RUN_LIVE_LLM_TESTS") != "1",
    reason="Test de integración vivo que invoca agy.exe (3 llamadas LLM de ~6 min). Para ejecutarlo, definir RUN_LIVE_LLM_TESTS=1.",
)
async def test_full_pipeline_end_to_end():
    # 1. Subagente Estratega
    strategist = StrategistAgent()
    analisis = await strategist.analyze_offer(SAMPLE_JOB_OFFER)

    assert "requisitos" in analisis
    assert len(analisis["requisitos"]) >= 3
    assert len(analisis.get("dolores_empresa", [])) >= 1
    assert 0 <= analisis["match_score"] <= 100

    estados = {r["estado"] for r in analisis["requisitos"]}
    assert "MATCH_DIRECTO" in estados or "TRANSFERIBLE" in estados

    # 2. Subagente Redactor
    writer = WriterAgent()
    redaccion = await writer.generate_cv_and_pitch(analisis)

    cv_md = redaccion["cv_markdown"]
    pitch = redaccion["pitch"]

    assert len(cv_md) > 200
    assert "SAM ORTEGA" in cv_md.upper()
    assert "EXPERIENCIA" in cv_md.upper()
    assert "•" in cv_md or "*" in cv_md or "-" in cv_md

    # Validar formato del Pitch (3 párrafos estructurados)
    pitch_chunks = [p.strip() for p in pitch.strip().split("\n\n") if p.strip()]
    # Si incluye saludo corto al inicio, filtrar para contar párrafos de cuerpo
    body_paragraphs = [p for p in pitch_chunks if not (len(p) < 80 and (p.endswith(":") or p.endswith(",")))]
    assert len(body_paragraphs) == 3 or len(pitch_chunks) in (3, 4)

    # 3. Subagente Auditor Ácido
    auditor = AuditorAgent()
    keywords = [r["nombre"] for r in analisis["requisitos"]]
    cv_clean, pitch_clean, reporte = await auditor.audit_and_remediate(cv_md, pitch, keywords)

    assert reporte["ats_score"] >= 75 or reporte["ats"]["ats_score"] >= 75
    assert reporte["anti_ai_score"] >= 80 or reporte["anti_ai"]["anti_ai_score"] >= 80
    assert len(reporte["anti_ai"]["detected_words"]) == 0

    # 4. Compilación DOCX Ejecutiva
    output_docx = compile_markdown_to_docx(cv_clean, output_filename="test_e2e_cv.docx")
    assert output_docx.exists()
    assert output_docx.stat().st_size > 1000

    # Limpieza
    if output_docx.exists():
        os.remove(output_docx)
