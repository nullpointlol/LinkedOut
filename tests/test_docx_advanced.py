import os
from pathlib import Path
import docx
from core.docx_generator import compile_markdown_to_docx

SAMPLE_CV_FOR_DOCX = """# ALEX RIVERA
SENIOR TECHNICAL BUSINESS ANALYST
San Isidro, Buenos Aires, Argentina | +54 9 11 5555-0100 | https://wa.me/5491155550100 | alex.rivera@example.com | LinkedIn: https://www.linkedin.com/in/alex-rivera-ejemplo/

## PERFIL
Analista Técnico Funcional con más de 7 años de trayectoria optimizando flujos de trabajo e integración de sistemas.

## EXPERIENCIA
### Analista Funcional Técnico | Freelance | 2025 - 2026
• **Automatización de Procesos:** Redujo en un **40%** el tiempo de procesamiento manual de pedidos mediante flujos con **Python**.
• **Garantía de Calidad & QA:** Elevó la trazabilidad funcional de operaciones críticas al **100%**.

### Analista Técnico Funcional y Soporte | VELORA SISTEMAS | 2018 - 2025
• **Observabilidad & Calidad de Código:** Centralizó la observabilidad de más de **90 aplicaciones** construyendo un extractor automatizado en **Python**.

## HABILIDADES TÉCNICAS
• **Lenguajes & Automatización:** Python, SQL, Bash, APIs REST/SOAP.
• **CI/CD & DevOps:** Jenkins, GitLab CI/CD, Codacy, Linux, Docker.

## EDUCACIÓN
• Tecnicatura en Informática - 2016

## IDIOMAS
• Inglés avanzado, Español nativo
"""

def test_compile_docx_advanced_formatting():
    output_path = compile_markdown_to_docx(
        SAMPLE_CV_FOR_DOCX,
        target_role="Senior Technical Business Analyst",
        output_filename="test_advanced_cv.docx"
    )
    assert output_path.exists()

    doc = docx.Document(str(output_path))
    paragraphs = doc.paragraphs
    assert len(paragraphs) >= 10

    # 1. Nombre en P0
    assert "ALEX RIVERA" in paragraphs[0].text

    # 2. Puesto Objetivo como subtítulo
    text_first_3 = " ".join([paragraphs[i].text for i in range(min(4, len(paragraphs)))])
    assert "SENIOR TECHNICAL BUSINESS ANALYST" in text_first_3.upper()

    # 3. Datos de contacto con iconos y texto limpio
    assert "San Isidro" in text_first_3
    # WhatsApp y LinkedIn
    assert "LinkedIn" in text_first_3

    # 4. Hipervínculos XML verificados en el documento
    xml_content = doc._element.xml
    rel_targets = " ".join([r.target_ref for r in doc.part.rels.values() if r.is_external])
    combined_links = f"{xml_content} {rel_targets}".lower()

    assert "wa.me" in combined_links or "whatsapp" in combined_links
    assert "mailto:alex.rivera@example.com" in combined_links
    assert "linkedin.com" in combined_links

    # Limpieza
    if output_path.exists():
        os.remove(output_path)


def test_summarize_role_slug_and_generate_cv_filename():
    from core.docx_generator import summarize_role_slug, generate_cv_filename

    assert summarize_role_slug("GESTOR/A DE PROBLEMAS IT") == "GESTOR-PROBLEMAS-IT"
    assert generate_cv_filename("GESTOR/A DE PROBLEMAS IT") == "CV_GESTOR-PROBLEMAS-IT_ALEX-RIVERA.docx"

    assert summarize_role_slug("Senior Technical Business Analyst / Data & DevOps Integrator") == "TECHNICAL-BUSINESS-ANALYST"
    assert generate_cv_filename("Senior Technical Business Analyst / Data & DevOps Integrator") == "CV_TECHNICAL-BUSINESS-ANALYST_ALEX-RIVERA.docx"

    assert summarize_role_slug("Analista Funcional Senior - Banca y Medios de Pago") == "ANALISTA-FUNCIONAL"
    assert generate_cv_filename("Analista Funcional Senior - Banca y Medios de Pago") == "CV_ANALISTA-FUNCIONAL_ALEX-RIVERA.docx"

    assert summarize_role_slug("Ingeniero de Datos / Data Engineer") == "INGENIERO-DATOS"
    assert generate_cv_filename("Ingeniero de Datos / Data Engineer") == "CV_INGENIERO-DATOS_ALEX-RIVERA.docx"

    assert summarize_role_slug("Scrum Master") == "SCRUM-MASTER"
    assert generate_cv_filename("Scrum Master") == "CV_SCRUM-MASTER_ALEX-RIVERA.docx"

    assert summarize_role_slug("") == "PROFESIONAL"
    assert generate_cv_filename("") == "CV_PROFESIONAL_ALEX-RIVERA.docx"


def test_contact_line_compact_and_linkedin_in_same_line():
    from core.docx_generator import compile_markdown_to_docx
    test_cv = """# ALEX RIVERA
GESTOR/A DE PROBLEMAS IT
San Isidro, Buenos Aires, Argentina | +54 9 11 5555-0100 | https://wa.me/5491155550100 | alex.rivera@example.com | https://www.linkedin.com/in/alex-rivera-ejemplo/

## PERFIL
Perfil profesional específico.
"""
    output_path = compile_markdown_to_docx(
        test_cv,
        target_role="GESTOR/A DE PROBLEMAS IT",
        output_filename="test_single_line_contact.docx"
    )
    assert output_path.exists()
    assert output_path.name == "test_single_line_contact.docx"

    doc = docx.Document(str(output_path))
    contact_p = doc.paragraphs[2]
    contact_text = contact_p.text

    # No debe tener saltos de línea internos
    assert "\n" not in contact_text
    assert "\r" not in contact_text

    # Debe contener todos los elementos en el mismo párrafo
    assert "San Isidro" in contact_text
    assert "+54 9 11 5555-0100" in contact_text
    assert "alex.rivera@example.com" in contact_text
    assert "LinkedIn" in contact_text

    # Los separadores no deben tener espacios desmedidos (" • " en vez de "   •   ")
    assert "   •   " not in contact_text
    assert " • " in contact_text

    # Verificar que el hipervínculo de LinkedIn tenga el icono y espacio indivisible
    xml_contact = contact_p._element.xml
    assert "LinkedIn" in xml_contact
    assert "wa.me" in xml_contact

    if output_path.exists():
        os.remove(output_path)


def test_api_export_docx_custom_filename():
    from fastapi.testclient import TestClient
    from server import app
    from core.database import create_postulacion, update_postulacion

    postulacion_id = create_postulacion(
        titulo="GESTOR/A DE PROBLEMAS IT",
        empresa="Empresa IT Test",
        contenido="Buscamos Gestor de Problemas IT con experiencia.",
        url="https://example.com/job/gestor-it"
    )
    update_postulacion(
        postulacion_id=postulacion_id,
        cv_markdown="# ALEX RIVERA\nGESTOR/A DE PROBLEMAS IT\nSan Isidro | +54 9 11 5555-0100 | alex.rivera@example.com | https://linkedin.com/in/test\n\n## PERFIL\nTest",
        pitch="Pitch de prueba."
    )

    client = TestClient(app)
    response = client.get(f"/api/ofertas/{postulacion_id}/export/docx")
    assert response.status_code == 200

    content_disp = response.headers.get("content-disposition", "")
    assert 'CV_GESTOR-PROBLEMAS-IT_ALEX-RIVERA.docx' in content_disp

