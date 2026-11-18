"""Test para verificar que las viñetas en DOCX no tengan sangría deformada."""
from pathlib import Path
from docx import Document
from docx.shared import Pt
from core.docx_generator import compile_markdown_to_docx

def test_bullet_paragraphs_have_zero_indent():
    """Valida que los párrafos de viñeta no tengan sangría colgante o deformada."""
    md_content = """# SAM ORTEGA
ANALISTA TÉCNICO
Ciudad Ejemplo | +54911 | email@test.test | linkedin.com

## EXPERIENCIA
### Analista Técnico Funcional | VELORA SISTEMAS | 2018 - 2025
• **Soporte N1/N2:** Mantenimiento preventivo y correctivo de hardware, servidores y redes.
• **Escalamiento Técnico:** Liderazgo en diagnóstico e hipótesis técnica para incidentes.
"""
    output_filename = "test_bullets.docx"
    result_path = compile_markdown_to_docx(md_content, target_role="ANALISTA TÉCNICO", output_filename=output_filename)
    assert result_path.exists()

    doc = Document(str(result_path))
    bullet_paragraphs = [
        p for p in doc.paragraphs
        if p.text.startswith("• ")
    ]

    assert len(bullet_paragraphs) == 2
    for p in bullet_paragraphs:
        # La sangría debe ser 0 o None (sin sangría colgante forzada)
        left = p.paragraph_format.left_indent
        first_line = p.paragraph_format.first_line_indent
        assert left is None or left == Pt(0), f"left_indent debe ser 0 o None, pero fue {left}"
        assert first_line is None or first_line == Pt(0), f"first_line_indent debe ser 0 o None, pero fue {first_line}"
