import pytest
import re
import os
import docx
from docx.shared import Pt

from core.auditor import ANTI_AI_REPLACEMENTS, AuditorAgent
from core.pdf_generator import render_cv_html
from core.docx_generator import compile_markdown_to_docx


def test_anti_ai_replacements_handle_puffery_and_cliches():
    text = "Poseo una sólida trayectoria y un marcado interés en retail, articulando soluciones de punta a punta."
    cleaned = text
    for pattern, sub in ANTI_AI_REPLACEMENTS.items():
        cleaned = re.sub(pattern, sub, cleaned, flags=re.IGNORECASE)
    assert "sólida trayectoria" not in cleaned.lower()
    assert "marcado interés" not in cleaned.lower()
    assert "de punta a punta" not in cleaned.lower()
    assert "articulando" not in cleaned.lower()


def test_anti_ai_replacements_all_puffery_mappings():
    samples = [
        ("Cuento con sólida trayectoria en proyectos.", "experiencia comprobada"),
        ("Tengo sólida experiencia en microservicios.", "experiencia comprobada"),
        ("Demuestro marcado interés en finanzas.", "interés profesional"),
        ("Aporto vasto conocimiento de arquitectura.", "conocimiento técnico"),
        ("Poseo vasta experiencia en producción.", "amplia práctica"),
        ("Reconocido por mi destacada labor en el equipo.", "labor técnica"),
        ("A lo largo de mi carrera alcancé hitos clave.", "en mi trayectoria"),
    ]
    for orig, expected_sub in samples:
        cleaned = orig
        for pattern, sub in ANTI_AI_REPLACEMENTS.items():
            cleaned = re.sub(pattern, sub, cleaned, flags=re.IGNORECASE)
        assert expected_sub in cleaned.lower(), f"Expected '{expected_sub}' in '{cleaned}'"


def test_anti_ai_replacements_preserves_technical_domain_terms():
    text = "Validé la consistencia de datos en bases de datos relacionales y optimicé consultas SQL para sistemas transaccionales y auditoría de procesos."
    cleaned = text
    for pattern, sub in ANTI_AI_REPLACEMENTS.items():
        cleaned = re.sub(pattern, sub, cleaned, flags=re.IGNORECASE)
    assert "consistencia de datos" in cleaned.lower()
    assert "consultas sql" in cleaned.lower()
    assert "sistemas transaccionales" in cleaned.lower()
    assert "auditoría de procesos" in cleaned.lower()


def test_pdf_html_maintains_readable_executive_font_size():
    html_output = render_cv_html("# Juan Pérez\n## PERFIL\nTexto\n## EXPERIENCIA\n### Empresa\n• Viñeta.")
    # El cuerpo del texto debe ser de al menos 9.5pt, no micro-fuente
    assert "font-size: 9.5pt" in html_output or "font-size: 10pt" in html_output
    assert "font-size: 8pt" not in html_output and "font-size: 7pt" not in html_output
    # Viñetas y párrafos deben estar blindados a 9.5pt y line-height 1.35 (no 9pt ni 1.32)
    assert "ul.cv-bullets li {\n      font-size: 9.5pt;\n      line-height: 1.35;" in html_output
    assert "p.cv-paragraph {\n      margin: 3px 0;\n      font-size: 9.5pt;\n      line-height: 1.35;" in html_output
    # Line height estándar ejecutivo 1.35 en body
    assert "line-height: 1.35;" in html_output


def test_docx_maintains_readable_executive_font_size_and_spacing():
    test_md = "# Juan Pérez\n## PERFIL\nAnalista con trayectoria.\n## EXPERIENCIA\n### Empresa\n• Logro cuantificado en 20%."
    out_path = compile_markdown_to_docx(test_md, target_role="Analista", output_filename="test_typography_check.docx")
    try:
        doc = docx.Document(str(out_path))
        # Verificar que ningún run de texto de párrafo o viñeta sea micro-fuente (< 8.5pt)
        body_runs = []
        for p in doc.paragraphs:
            if p.runs and not any(r.font.size == Pt(1.0) for r in p.runs):
                for r in p.runs:
                    if r.font.size:
                        body_runs.append(r.font.size.pt)
        assert all(size >= 8.5 for size in body_runs)
        assert any(size >= 9.5 for size in body_runs)
        assert not any(size in [7.0, 8.0] for size in body_runs)

        # Verificar interlineado 1.35 en viñetas y párrafos regulares
        bullet_p = [p for p in doc.paragraphs if p.text.startswith("•")][0]
        assert bullet_p.paragraph_format.line_spacing == 1.35

        profile_p = [p for p in doc.paragraphs if "Analista con trayectoria." in p.text][0]
        assert profile_p.paragraph_format.line_spacing == 1.35
    finally:
        if out_path.exists():
            out_path.unlink()
