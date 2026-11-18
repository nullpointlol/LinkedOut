"""Integration E2E test for CV formatting consistency and single A4 page synthesis.

Validates:
1. DOCX compilation has zero bottom borders (w:bottom / w:pBdr), 10pt contact line,
   and combined role/company/dates in a single paragraph.
2. PDF compilation produces strictly 1 single A4 page (len(reader.pages) == 1).
3. Auditor awards high global score (>= 90 pts) and full escaneabilidad (20.0 pts)
   with surgical bolds, concise bullets (<= 250 chars), and 1st person action verbs.
"""
import pytest
from pathlib import Path
import docx
from docx.shared import Pt
from docx.oxml.ns import qn
import pypdf

from core.docx_generator import (
    compile_markdown_to_docx,
    COLOR_NAVY,
    COLOR_SLATE,
)
from core.pdf_generator import compile_markdown_to_pdf
from core.auditor import evaluate_ats_compliance, AuditorAgent


SAMPLE_E2E_CV = """# ALEX RIVERA
San Isidro, Buenos Aires, Argentina | +54 9 11 5555-0100 | alex.rivera@example.com | https://www.linkedin.com/in/alex-rivera-ejemplo/

## PERFIL
Analista Técnico Funcional con trayectoria de 7 años en relevamiento de procesos e interlocución técnica entre áreas de negocio y desarrollo.

## EXPERIENCIA
### Analista Funcional Técnico | Freelance | 2025 - Presente
• Lideré el diseño funcional de integraciones con **APIs REST** y disminuí incidencias operativas un 35%.
• Confeccioné historias de usuario y matrices de prueba para procesos transaccionales de alta disponibilidad.

### Analista Técnico Funcional | VELORA SISTEMAS | 2018 - 2025
• Automaticé el seguimiento de más de 90 aplicaciones con observabilidad unificada y menor latencia de respuesta.
• Definí criterios de aceptación y gestioné pases a producción en ambientes de certificación y testing.

### Desarrollador de Software | KORVA | 2016 - 2018
• Desarrollé consultas y procedimientos optimizados en **SQL** para mejorar los tiempos de respuesta un 25%.

## HABILIDADES TÉCNICAS
• Análisis funcional, APIs REST, SQL, Microservicios, Testing, Metodologías Ágiles, Relevamiento funcional.

## EDUCACIÓN
• Licenciatura en Sistemas de Información | Universidad de Buenos Aires (2014 - 2019)

## IDIOMAS
• Español: Nativo | Inglés: C1 Profesional
"""

KEYWORDS = ["APIs REST", "SQL", "Análisis funcional", "Testing", "Microservicios"]
EXPECTED_COMPANIES = ["Freelance", "VELORA SISTEMAS", "KORVA"]


def test_docx_e2e_formatting_zero_borders_and_single_heading_and_contact_10pt(tmp_path):
    """1. Compilar DOCX y validar:
       - Cero elementos w:bottom y w:pBdr.
       - Contacto en tamaño 10pt (w:sz == 20).
       - Título de puesto, empresa y fechas en un único párrafo combinado.
    """
    output_docx = tmp_path / "cv_e2e_verified.docx"
    result_path = compile_markdown_to_docx(
        SAMPLE_E2E_CV,
        target_role="Analista Funcional Técnico",
        output_filename=str(output_docx)
    )
    assert result_path.exists(), f"El archivo DOCX no fue generado en {result_path}"

    doc = docx.Document(str(result_path))

    # A) Validación de CERO bordes/separadores inferiores
    doc_xml = doc._element.xml
    assert "<w:bottom" not in doc_xml, "Se detectó elemento <w:bottom> en el DOCX; deben eliminarse bordes inferiores."
    assert "w:pBdr" not in doc_xml, "Se detectó elemento <w:pBdr> en el DOCX; no debe haber bordes de párrafo."
    assert len(doc._element.xpath('//w:bottom')) == 0, "No debe haber nodos w:bottom en el XML del DOCX"
    assert len(doc._element.xpath('//w:pBdr')) == 0, "No debe haber nodos w:pBdr en el XML del DOCX"

    # B) Validación de línea de contacto a 10pt
    contact_paragraphs = [
        p for p in doc.paragraphs
        if "San Isidro" in p.text and ("linkedin" in p.text.lower() or "alex.rivera" in p.text.lower())
    ]
    assert len(contact_paragraphs) == 1, "Debe existir exactamente un párrafo de contacto"
    contact_p = contact_paragraphs[0]

    for r in contact_p.runs:
        if r.text.strip():
            assert r.font.size == Pt(10), f"Run '{r.text}' en contacto tiene tamaño {r.font.size}, se esperaba Pt(10)"

    sz_elements = contact_p._element.xpath('.//w:sz')
    assert len(sz_elements) > 0, "Debe haber elementos w:sz en el párrafo de contacto"
    for sz in sz_elements:
        val = sz.get(qn('w:val'))
        assert val == "20", f"Elemento de tamaño w:sz tiene valor {val} ({int(val)/2} pt), se esperaba 20 (10 pt)"

    # C) Validación de títulos de experiencia en un único párrafo
    for comp in EXPECTED_COMPANIES:
        matching_ps = [p for p in doc.paragraphs if comp in p.text and ("|" in p.text or "Analista" in p.text or "Desarrollador" in p.text)]
        assert len(matching_ps) == 1, f"Se esperaba un único párrafo para la experiencia en {comp}, encontrados {len(matching_ps)}"
        p_exp = matching_ps[0]
        # Validar que rol y fechas están en el mismo párrafo
        assert " | " in p_exp.text
        # Validar jerarquía de runs: rol en 10pt bold Navy, separador y subheadings en 9.5pt Slate normal
        assert len(p_exp.runs) >= 3
        r_role = p_exp.runs[0]
        assert r_role.bold is True
        assert r_role.font.size == Pt(10)
        assert r_role.font.color.rgb == COLOR_NAVY
        r_sep = p_exp.runs[1]
        assert r_sep.text == " | "
        assert r_sep.font.size == Pt(9.5)
        r_sub = p_exp.runs[2]
        assert not r_sub.bold
        assert r_sub.font.size == Pt(9.5)
        assert r_sub.font.color.rgb == COLOR_SLATE


def test_pdf_e2e_single_a4_page_geometry(tmp_path):
    """2. Compilar PDF y verificar con pypdf que estrictamente ocupa 1 sola página A4."""
    output_filename = "test_cv_e2e_a4_single_page.pdf"
    pdf_path = compile_markdown_to_pdf(
        SAMPLE_E2E_CV,
        target_role="Analista Funcional Técnico",
        output_filename=output_filename,
        keywords=KEYWORDS
    )
    assert pdf_path.exists(), f"El archivo PDF no fue generado en {pdf_path}"
    assert pdf_path.stat().st_size > 0, f"El archivo PDF generado está vacío: {pdf_path}"

    reader = pypdf.PdfReader(str(pdf_path))
    num_pages = len(reader.pages)
    assert num_pages == 1, f"El CV en PDF supera la cota de 1 página A4: contiene {num_pages} páginas"


def test_auditor_e2e_high_global_score_and_full_scanability():
    """3. Verificar que la auditoría califica la escaneabilidad con nota perfecta (20.0 pts)
       y otorga nota global alta (>= 90 pts, alcanzando 100 pts en CV óptimo).
    """
    # Verificación directa de evaluación ATS
    ats_eval = evaluate_ats_compliance(SAMPLE_E2E_CV, keywords=KEYWORDS, expected_companies=EXPECTED_COMPANIES)
    assert ats_eval["dimensiones"]["escaneabilidad"] == 20.0, (
        f"Se esperaba escaneabilidad perfecta (20.0 pts), obtenido: {ats_eval['dimensiones']['escaneabilidad']}"
    )
    assert ats_eval["dimensiones"]["estructura"] == 20.0
    assert ats_eval["dimensiones"]["metricas_empiricas"] == 25.0
    assert ats_eval["dimensiones"]["keywords"] == 35.0
    assert ats_eval["ats_score"] >= 90.0
    assert ats_eval["deducciones"] == []

    # Verificación a través del AuditorAgent
    agent = AuditorAgent()
    audit_report = agent.audit(
        cv_markdown=SAMPLE_E2E_CV,
        pitch="Hola equipo, les comparto mi postulación como Analista Funcional Técnico.",
        keywords=KEYWORDS,
        expected_companies=EXPECTED_COMPANIES
    )
    assert audit_report["global_score"] >= 90.0, (
        f"Se esperaba global_score >= 90 pts, obtenido: {audit_report['global_score']}"
    )
    assert audit_report["approved"] is True
    assert audit_report["verdict"] == "PASE A ENTREVISTA"
    assert audit_report["ats"]["dimensiones"]["escaneabilidad"] == 20.0
    assert len(audit_report["deducciones"]) == 0


@pytest.mark.asyncio
async def test_auditor_agent_audit_and_remediate_e2e_no_remediation_needed():
    """Verificar que audit_and_remediate aprueba directamente un CV que cumple con el estándar A4."""
    agent = AuditorAgent()
    cv_out, pitch_out, report = await agent.audit_and_remediate(
        cv_markdown=SAMPLE_E2E_CV,
        pitch="Hola equipo, les comparto mi postulación como Analista Funcional Técnico.",
        keywords=KEYWORDS,
        max_attempts=1,
        expected_companies=EXPECTED_COMPANIES
    )
    assert cv_out == SAMPLE_E2E_CV
    assert report["approved"] is True
    assert report["global_score"] >= 90.0
    assert report["ats"]["dimensiones"]["escaneabilidad"] == 20.0
