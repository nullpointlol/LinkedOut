import pytest
from pathlib import Path
import docx
from docx.shared import Pt, RGBColor
from docx.oxml.ns import qn

from core.docx_generator import (
    compile_markdown_to_docx,
    COLOR_NAVY,
    COLOR_SLATE,
)

SAMPLE_CV = """# ALEX RIVERA
San Isidro, Buenos Aires, Argentina | +54 9 11 5555-0100 | https://wa.me/5491155550100 | alex.rivera@example.com | https://www.linkedin.com/in/alex-rivera-ejemplo/

## PERFIL
Analista Técnico Funcional con más de 7 años de experiencia.

## EXPERIENCIA
### Analista Funcional Técnico | Freelance | 2025 - 2026
• Redujo en un 40% el tiempo de procesamiento manual.
• Elevó la trazabilidad funcional al 100%.

### Analista Técnico Funcional | VELORA SISTEMAS | 2018 - 2025
• Centralizó la observabilidad de más de 90 aplicaciones.

## EDUCACIÓN
• Tecnicatura en Informática - 2016
"""

def test_docx_has_zero_bottom_borders(tmp_path):
    output_docx = tmp_path / "cv_no_borders.docx"
    result_path = compile_markdown_to_docx(
        SAMPLE_CV,
        target_role="Analista Funcional Técnico",
        output_filename=str(output_docx)
    )
    assert result_path.exists()

    doc = docx.Document(str(result_path))
    doc_xml = doc._element.xml

    # El documento generado NO debe contener elementos <w:bottom> ni bordes de párrafo <w:pBdr>
    assert "<w:bottom" not in doc_xml, "Se encontró el elemento <w:bottom> en el documento DOCX; deben eliminarse todos los separadores/bordes inferiores."
    assert "w:pBdr" not in doc_xml, "Se encontró <w:pBdr> en el documento DOCX; no debe haber bordes de párrafo."
    assert len(doc._element.xpath('//w:bottom')) == 0, "No debe haber elementos w:bottom en el árbol XML del documento."
    assert len(doc._element.xpath('//w:pBdr')) == 0, "No debe haber elementos w:pBdr en el árbol XML del documento."


def test_experience_role_company_date_in_single_paragraph(tmp_path):
    output_docx = tmp_path / "cv_single_paragraph.docx"
    result_path = compile_markdown_to_docx(
        SAMPLE_CV,
        target_role="Analista Funcional Técnico",
        output_filename=str(output_docx)
    )
    assert result_path.exists()

    doc = docx.Document(str(result_path))

    # Verificar que el rol y la empresa/fecha estén en un ÚNICO párrafo
    role_company_paragraphs = [
        p for p in doc.paragraphs
        if "Analista Funcional Técnico" in p.text and "Freelance" in p.text
    ]
    assert len(role_company_paragraphs) == 1, (
        f"Se esperaba exactamente 1 párrafo combinando Rol y Empresa/Fechas, pero se encontraron {len(role_company_paragraphs)}"
    )

    p = role_company_paragraphs[0]
    assert p.text == "Analista Funcional Técnico | Freelance | 2025 - 2026"

    # Verificar que NO exista un párrafo separado que contenga sólo "Freelance | 2025 - 2026"
    assert not any(p_sub.text.strip() == "Freelance | 2025 - 2026" for p_sub in doc.paragraphs)

    # Verificar formato de runs: rol en 10pt Navy Negrita, separador y empresa/fechas en 9.5pt Slate Normal
    assert len(p.runs) >= 3
    r_role = p.runs[0]
    assert r_role.text == "Analista Funcional Técnico"
    assert r_role.bold is True
    assert r_role.font.size == Pt(10)
    assert r_role.font.color.rgb == COLOR_NAVY

    r_sep = p.runs[1]
    assert r_sep.text == " | "
    assert r_sep.font.size == Pt(9.5)
    assert r_sep.font.color.rgb == COLOR_SLATE

    r_sub = p.runs[2]
    assert r_sub.text == "Freelance | 2025 - 2026"
    assert r_sub.font.size == Pt(9.5)
    assert r_sub.font.color.rgb == COLOR_SLATE
    assert not r_sub.bold


def test_contact_paragraph_font_size_is_10pt(tmp_path):
    output_docx = tmp_path / "cv_contact_10pt.docx"
    result_path = compile_markdown_to_docx(
        SAMPLE_CV,
        target_role="Analista Funcional Técnico",
        output_filename=str(output_docx)
    )
    assert result_path.exists()

    doc = docx.Document(str(result_path))

    # Identificar el párrafo de contacto
    contact_paragraphs = [
        p for p in doc.paragraphs
        if "San Isidro" in p.text and ("LinkedIn" in p.text or "alex.rivera" in p.text)
    ]
    assert len(contact_paragraphs) == 1, "Debe existir un único párrafo de contacto"
    contact_p = contact_paragraphs[0]

    # Verificar que los runs estándar tengan tamaño 10pt (o 9-10pt para separadores)
    for r in contact_p.runs:
        if r.text.strip():
            assert r.font.size == Pt(10), f"Run '{r.text}' tiene tamaño {r.font.size}, se esperaba Pt(10)"

    # Verificar todos los w:sz en los elementos oxml del párrafo de contacto (incluyendo hipervínculos)
    sz_elements = contact_p._element.xpath('.//w:sz')
    assert len(sz_elements) > 0, "Debe haber elementos de tamaño de fuente en el párrafo de contacto"

    for sz in sz_elements:
        val = sz.get(qn('w:val'))
        # 10pt equivale a 20 half-points. 8.5pt equivalía a 17 half-points.
        assert val == "20", f"Elemento de tamaño tiene valor {val} ({int(val)/2} pt), se esperaba 20 (10 pt)"
