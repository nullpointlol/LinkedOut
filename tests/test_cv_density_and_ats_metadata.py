"""Tests para Task 4: Rebalanceo de Densidad en WRITER_PROMPT y Soporte de Doble Frente ATS (Metadatos + Micro-Indexación)."""
import re
import pytest
from pathlib import Path
import docx
import pypdf
from fastapi.testclient import TestClient

from core.prompts_evaluacion import WRITER_PROMPT
from core.writer import WriterAgent
from core.docx_generator import compile_markdown_to_docx
from core.pdf_generator import (
    compile_markdown_to_pdf,
    render_cv_html,
    _compile_with_reportlab,
)
from core.database import create_postulacion, update_postulacion
from server import app


def test_writer_prompt_density_and_compact_skills():
    """Verifica que WRITER_PROMPT exige 3-4 líneas en perfil, 6-7 viñetas totales en Experiencia

    ponderadas por afinidad, habilidades en 3 líneas (2 viñetas), y prohíbe listados masivos al pie.
    """
    prompt = WRITER_PROMPT

    # 1. PERFIL: 3 a 4 líneas sustanciales
    assert "3 a 4 líneas" in prompt or "3-4 líneas" in prompt or "3 a 4 renglones" in prompt, (
        "WRITER_PROMPT debe instruir 3 a 4 líneas en la sección PERFIL"
    )

    # 2. EXPERIENCIA: cuota total de 6 a 7 viñetas ponderadas por afinidad
    assert "6 a 7 viñetas" in prompt or "6-7 viñetas" in prompt, (
        "WRITER_PROMPT debe instruir 6 a 7 viñetas en total para Experiencia"
    )
    assert "afinidad" in prompt.lower(), (
        "WRITER_PROMPT debe instruir ponderación según afinidad con la vacante"
    )

    # 3. HABILIDADES TÉCNICAS: 3 líneas de altura (título + 2 viñetas temáticas, máximo 10-15 tecnologías)
    assert ("3 líneas" in prompt or "3 renglones" in prompt) and (
        "10-15" in prompt or "10 a 15" in prompt
    ), "WRITER_PROMPT debe limitar Habilidades Técnicas a 3 líneas temáticas (máx 10-15 tecnologías)"

    # 4. Prohibición expresa de volcado masivo o listado kilométrico
    prompt_lower = prompt.lower()
    assert (
        "volcado masivo" in prompt_lower
        or "listado kilométrico" in prompt_lower
        or "listados masivos" in prompt_lower
    ), "WRITER_PROMPT debe prohibir terminantemente el volcado masivo o listado kilométrico"


def test_deterministic_fallback_reflects_balanced_density():
    """Verifica que el CV generado en fallback cumple con la distribución de viñetas,

    densidad de perfil en 1ª persona y habilidades técnicas compactas sin listas masivas.
    """
    agent = WriterAgent()
    puesto = "Analista Técnico Funcional"
    empresa = "Telecom Argentina"
    analysis = {
        "puesto": puesto,
        "empresa": empresa,
        "dolores_empresa": ["Optimizar pipelines CI/CD", "Reducir incidentes en producción"],
        "requisitos": [
            {"nombre": "Python", "estado": "MATCH_DIRECTO", "approved": True},
            {"nombre": "SQL", "estado": "MATCH_DIRECTO", "approved": True},
            {"nombre": "GitLab CI", "estado": "MATCH_DIRECTO", "approved": True},
            {"nombre": "Codacy", "estado": "MATCH_DIRECTO", "approved": True},
            {"nombre": "PagerDuty", "estado": "MATCH_DIRECTO", "approved": True},
            {"nombre": "Jira", "estado": "MATCH_DIRECTO", "approved": True},
        ],
    }
    profile = agent.load_candidate_profile()

    result = agent._deterministic_fallback(puesto, empresa, analysis, profile)
    cv_md = result["cv_markdown"]

    # 1. Verificar presencia de las 3 empresas canónicas
    assert "FREELANCE" in cv_md.upper()
    assert "VELORA SISTEMAS" in cv_md.upper()
    assert "KORVA" in cv_md.upper()

    # 2. Contar viñetas por empresa
    lines = cv_md.splitlines()
    bullets_by_company = {"FREELANCE": 0, "VELORA SISTEMAS": 0, "KORVA": 0}
    current_company = None
    in_perfil = False
    perfil_lines = []
    in_skills = False
    skills_lines = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            in_perfil = "PERFIL" in stripped.upper()
            in_skills = "HABILIDADES" in stripped.upper()
            if "EXPERIENCIA" not in stripped.upper():
                current_company = None
            continue

        if in_perfil and stripped:
            perfil_lines.append(stripped)

        if stripped.startswith("### "):
            upper_sub = stripped.upper()
            if "FREELANCE" in upper_sub:
                current_company = "FREELANCE"
            elif "VELORA SISTEMAS" in upper_sub:
                current_company = "VELORA SISTEMAS"
            elif "KORVA" in upper_sub:
                current_company = "KORVA"
            else:
                current_company = None

        if current_company and stripped.startswith(("•", "*", "-")):
            bullets_by_company[current_company] += 1

        if in_skills and stripped.startswith(("•", "*", "-")):
            skills_lines.append(stripped)

    # PERFIL sustancial: al menos 4 líneas (o 4 párrafos de contenido activo en 1ª persona)
    # y presencia de verbos activos en 1ª persona
    assert len(perfil_lines) >= 3, f"PERFIL debe ser sustancial (al menos 3-4 líneas), tiene {len(perfil_lines)}"
    perfil_text = " ".join(perfil_lines)
    assert any(v in perfil_text for v in ["Cuento", "Lideré", "Implementé", "Especializado", "Especialista"])

    # Viñetas Freelance: entre 3 y 4 (en fallback: 3)
    assert 3 <= bullets_by_company["FREELANCE"] <= 4, (
        f"Freelance debe tener entre 3 y 4 viñetas, tiene {bullets_by_company['FREELANCE']}"
    )

    # Viñetas VELORA SISTEMAS: entre 2 y 3 (en fallback: 2)
    assert 2 <= bullets_by_company["VELORA SISTEMAS"] <= 3, (
        f"VELORA SISTEMAS debe tener entre 2 y 3 viñetas, tiene {bullets_by_company['VELORA SISTEMAS']}"
    )

    # Viñetas KORVA: exactamente 1
    assert bullets_by_company["KORVA"] == 1, (
        f"KORVA debe tener 1 viñeta, tiene {bullets_by_company['KORVA']}"
    )

    # Habilidades técnicas: compactas (exactamente 2 líneas temáticas)
    assert len(skills_lines) == 2, (
        f"Habilidades Técnicas debe tener 2 líneas temáticas, tiene {len(skills_lines)}"
    )

    # Todas las viñetas de experiencia deben seguir el patrón • **Etiqueta:**
    bullet_lines = [l for l in lines if l.strip().startswith("• **")]
    assert len(bullet_lines) >= 6, f"Se esperaban al menos 6 viñetas con formato Google XYZ y etiquetas, hay {len(bullet_lines)}"


def test_docx_contains_metadata_and_micro_appendix(tmp_path):
    """Verifica que el archivo DOCX exportado tiene doc.core_properties pobladas

    y un párrafo de micro-indexación técnica discreta al pie en fuente 5pt.
    """
    sample_cv = """# ALEX RIVERA
ANALISTA TÉCNICO FUNCIONAL
San Isidro, Buenos Aires, Argentina | +54 9 11 5555-0100 | https://wa.me/5491155550100 | alex.rivera@example.com | LinkedIn: https://www.linkedin.com/in/alex-rivera-ejemplo/

## PERFIL
Analista Técnico Funcional con más de 12 años de trayectoria en optimización de flujos de trabajo e integración de sistemas.

## EXPERIENCIA
### Analista Funcional Técnico | Freelance | 2025 - 2026
• **Automatización de Procesos:** Redujo en un **40%** el procesamiento manual con **Python**.

### Analista Técnico Funcional y Soporte | VELORA SISTEMAS | 2018 - 2025
• **Observabilidad & Calidad:** Centralizó más de **90 aplicaciones** con **Python** y **Codacy**.

### Operador de Monitoreo & Soporte de Infraestructura | KORVA | 2011 - 2017
• **Monitoreo 24x7:** Supervisó infraestructuras críticas mediante **ServiceNow** y **Nagios**.

## HABILIDADES TÉCNICAS
• **Desarrollo & Datos:** Python, SQL, APIs REST/SOAP.
• **DevOps & Calidad:** Jenkins, GitLab CI, Codacy.
• **Gestión:** Jira, Scrum, Kanban.

## EDUCACIÓN
• Tecnicatura en Informática (2012 - 2015)

## IDIOMAS
• Inglés avanzado, Español nativo.
"""
    keywords = ["Python", "SQL", "GitLab CI", "Codacy", "PagerDuty", "Jira", "APIs REST", "Scrum"]
    target_role = "Analista Técnico Funcional"
    output_filename = "test_ats_metadata.docx"

    docx_path = compile_markdown_to_docx(
        sample_cv,
        target_role=target_role,
        output_filename=output_filename,
        keywords=keywords,
    )
    assert docx_path.exists()

    doc = docx.Document(str(docx_path))

    # 1. Metadatos oficiales de documento DOCX
    core_props = doc.core_properties
    assert "Alex Rivera" in core_props.author or "ALEX RIVERA" in core_props.author
    assert "Analista Técnico Funcional" in core_props.title or "ALEX RIVERA" in core_props.title
    assert core_props.keywords is not None
    for kw in ["Python", "SQL", "GitLab CI", "Codacy"]:
        assert kw in core_props.keywords

    # 2. Micro-Indexación Técnica Stealth al pie (solo delta keywords, invisible)
    last_p = doc.paragraphs[-1]
    assert "PagerDuty" in last_p.text
    assert "Python" not in last_p.text, "Python ya está en el CV y no debe duplicarse"
    assert "Codacy" not in last_p.text, "Codacy ya está en el CV y no debe duplicarse"

    # Verificar tipografía stealth <= 1.0pt, color blanco y vanish
    run = last_p.runs[0]
    assert run.font.size is not None
    assert run.font.size.pt <= 1.0, f"La fuente del apéndice ATS debe ser <= 1pt, fue {run.font.size.pt}"
    assert run.font.color.rgb == docx.shared.RGBColor(255, 255, 255)
    rPr = run._r.get_or_add_rPr()
    assert len(rPr.xpath("./w:vanish")) > 0


def test_pdf_contains_metadata_and_micro_appendix(tmp_path):
    """Verifica que el archivo PDF exportado (vía ReportLab o HTML) contiene metadatos

    y el bloque de micro-indexación técnica discreta.
    """
    sample_cv = """# ALEX RIVERA
ANALISTA TÉCNICO FUNCIONAL
San Isidro, Buenos Aires, Argentina | +54 9 11 5555-0100 | alex.rivera@example.com | https://linkedin.com/in/alex-rivera

## PERFIL
Analista Técnico Funcional con más de 12 años de trayectoria.

## EXPERIENCIA
### Analista Técnico Funcional | Freelance | 2025 - 2026
• **Automatización:** Reduje 40% tareas repetitivas en Python.

### Analista Técnico | VELORA SISTEMAS | 2018 - 2025
• **Observabilidad:** Integré Codacy en 90 aplicaciones.

### Operador | KORVA | 2011 - 2017
• **Monitoreo:** Gestión 24x7 en ServiceNow.

## HABILIDADES TÉCNICAS
• Python, SQL, Docker, APIs REST, Linux.

## EDUCACIÓN
• Tecnicatura en Informática (2016)

## IDIOMAS
• Inglés avanzado, Español nativo.
"""
    keywords = ["Python", "SQL", "Docker", "APIs REST", "Linux", "Microservicios"]
    target_role = "Analista Técnico Funcional"

    # 1. Verificar HTML generado para Edge/Chrome
    html = render_cv_html(sample_cv, target_role=target_role, keywords=keywords)
    assert '<meta name="keywords"' in html
    assert "Python" in html
    assert "Microservicios" in html
    assert '<meta name="author" content="Alex Rivera"' in html or '<meta name="author" content="ALEX RIVERA"' in html
    assert "ats-appendix" in html
    assert "0.5pt" in html
    assert "#FFFFFF" in html

    # 2. Verificar compilación con ReportLab (contingencia nativa)
    reportlab_pdf_path = tmp_path / "test_reportlab_ats.pdf"
    _compile_with_reportlab(sample_cv, target_role=target_role, output_path=reportlab_pdf_path, keywords=keywords)
    assert reportlab_pdf_path.exists()

    reader = pypdf.PdfReader(str(reportlab_pdf_path))
    metadata = reader.metadata
    assert metadata is not None
    assert "/Author" in metadata and ("Alex Rivera" in metadata["/Author"] or "ALEX RIVERA" in metadata["/Author"])
    assert "/Keywords" in metadata and "Python" in metadata["/Keywords"]
    assert "/Subject" in metadata

    # Verificar que el texto del PDF incluye el apéndice de tecnologías delta en blanco sin prefijo humano
    full_text = " ".join([page.extract_text() for page in reader.pages])
    assert "Palabras clave y tecnologías:" not in full_text
    assert "Python" in full_text
    assert "Microservicios" in full_text


def test_api_export_endpoints_pass_keywords(tmp_path):
    """Verifica que los endpoints /api/ofertas/{id}/export/docx y pdf pasan las keywords de la postulación."""
    client = TestClient(app)

    # Crear una postulación con requisitos / keywords
    post_id = create_postulacion(
        titulo="Senior Python Engineer",
        empresa="Mercado Libre",
        contenido="Buscamos Senior Python Engineer con experiencia en FastAPI, Docker, PostgreSQL, Kubernetes y CI/CD.",
        url="https://mercadolibre.com/jobs/123",
        status="PROCESSED"
    )

    sample_cv = """# ALEX RIVERA
SENIOR PYTHON ENGINEER
San Isidro, Buenos Aires | +54 9 11 5555-0100 | alex.rivera@example.com | linkedin.com

## PERFIL
Ingeniero de Software con foco en Python, Docker y microservicios.

## EXPERIENCIA
### Ingeniero | Freelance | 2025 - 2026
• **Python:** Implementé APIs con FastAPI y PostgreSQL.

### Analista | VELORA SISTEMAS | 2018 - 2025
• **DevOps:** Mantuve pipelines Docker y Kubernetes.

### Operador | KORVA | 2011 - 2017
• **Infraestructura:** Soporte y monitoreo continuo.

## HABILIDADES TÉCNICAS
• Python, FastAPI, Docker, PostgreSQL, Kubernetes.

## EDUCACIÓN
• Tecnicatura en Informática (2016)

## IDIOMAS
• Inglés avanzado, Español nativo.
"""
    update_postulacion(
        post_id,
        cv_markdown=sample_cv,
        requisitos=[
            {"nombre": "FastAPI", "estado": "MATCH_DIRECTO", "approved": True},
            {"nombre": "Docker", "estado": "MATCH_DIRECTO", "approved": True},
            {"nombre": "PostgreSQL", "estado": "MATCH_DIRECTO", "approved": True},
            {"nombre": "Kubernetes", "estado": "TRANSFERIBLE", "approved": True},
        ]
    )

    # 1. Exportar DOCX
    response_docx = client.get(f"/api/ofertas/{post_id}/export/docx")
    assert response_docx.status_code == 200
    assert response_docx.headers["content-type"] == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

    exported_docx_path = tmp_path / "api_exported.docx"
    exported_docx_path.write_bytes(response_docx.content)
    doc = docx.Document(str(exported_docx_path))
    assert doc.core_properties.keywords is not None
    assert "FastAPI" in doc.core_properties.keywords
    assert "PostgreSQL" in doc.core_properties.keywords

    # 2. Exportar PDF
    response_pdf = client.get(f"/api/ofertas/{post_id}/export/pdf")
    assert response_pdf.status_code == 200
    assert response_pdf.headers["content-type"] == "application/pdf"

    exported_pdf_path = tmp_path / "api_exported.pdf"
    exported_pdf_path.write_bytes(response_pdf.content)
    reader = pypdf.PdfReader(str(exported_pdf_path))
    full_text = " ".join([page.extract_text() for page in reader.pages])
    assert "FastAPI" in full_text or (reader.metadata and "/Keywords" in reader.metadata and "FastAPI" in reader.metadata["/Keywords"])
