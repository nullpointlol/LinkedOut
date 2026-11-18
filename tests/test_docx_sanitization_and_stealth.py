import docx
import pytest
from pathlib import Path
from core.docx_generator import compile_markdown_to_docx

def test_docx_contact_line_has_no_emojis_and_uses_bullet_separators(tmp_path, monkeypatch):
    monkeypatch.setattr("core.docx_generator.EXPORTS_DIR", tmp_path)

    cv_markdown = """# ALEX RIVERA
Buenos Aires, Argentina | +54 9 11 5555-0100 | alex.rivera@example.com | https://linkedin.com/in/alex-rivera

## PERFIL PROFESIONAL
Ingeniero de Software y Analista Técnico Funcional con experiencia en Python y AWS.

## EXPERIENCIA LABORAL
### Tech Lead — Freelance | 2025 - Presente
• Lideré la arquitectura de microservicios con Docker y FastAPI.
"""
    output_docx = compile_markdown_to_docx(cv_markdown, keywords=["Docker", "FastAPI", "Python", "AWS"])
    assert output_docx.exists()

    doc = docx.Document(str(output_docx))
    all_text = " ".join(p.text for p in doc.paragraphs)

    # 1. Verify NO emojis in the whole document
    emojis = ["📍", "📱", "✉️", "🔗"]
    for emo in emojis:
        assert emo not in all_text, f"Emoji {emo} found in DOCX text"

    # 2. Verify contact paragraph has bullet separator and clean text
    contact_p = None
    for p in doc.paragraphs:
        if "alex.rivera@example.com" in p.text or "+54 9 11 5555-0100" in p.text:
            contact_p = p
            break
    assert contact_p is not None, "Contact paragraph not found"
    assert "•" in contact_p.text, "Contact line should use ' • ' as clean separator"

def test_docx_discreet_ats_appendix_formatting(tmp_path, monkeypatch):
    monkeypatch.setattr("core.docx_generator.EXPORTS_DIR", tmp_path)

    cv_markdown = """# ALEX RIVERA
Buenos Aires, Argentina | +54 9 11 5555-0100 | alex.rivera@example.com | https://linkedin.com/in/alex-rivera

## PERFIL
Ingeniero con experiencia en Docker y Python.
"""
    keywords = ["Docker", {"nombre": "Kubernetes"}, "Python", "AWS", "FastAPI"]
    output_docx = compile_markdown_to_docx(cv_markdown, keywords=keywords)

    doc = docx.Document(str(output_docx))
    
    # 1. Ensure title "[Índice Técnico de Compatibilidad ATS:" is completely absent
    for p in doc.paragraphs:
        assert "Índice Técnico de Compatibilidad ATS" not in p.text
        assert "[Índice Técnico" not in p.text

    # 2. Check the ATS keywords paragraph at the end: only delta keywords, no human title
    last_p = doc.paragraphs[-1]
    assert "Palabras clave y tecnologías:" not in last_p.text
    assert "Docker" not in last_p.text, "Docker ya está en el CV, no debe repetirse en el apéndice"
    assert "Python" not in last_p.text, "Python ya está en el CV, no debe repetirse en el apéndice"
    assert "Kubernetes" in last_p.text
    assert "AWS" in last_p.text
    assert "FastAPI" in last_p.text

    # 3. Check styling: size must be <= 1.0 pt, color white (255, 255, 255), vanish
    run = last_p.runs[0]
    assert run.font.size.pt <= 1.0, f"ATS run font size must be <= 1.0pt, got {run.font.size.pt}"
    assert run.font.color.rgb == docx.shared.RGBColor(255, 255, 255), "ATS run color must be white #FFFFFF"
    rPr = run._r.get_or_add_rPr()
    assert len(rPr.xpath("./w:vanish")) > 0


def test_docx_stealth_ats_appendix_omitted_when_no_delta_keywords(tmp_path, monkeypatch):
    monkeypatch.setattr("core.docx_generator.EXPORTS_DIR", tmp_path)

    cv_markdown = """# ALEX RIVERA
Buenos Aires, Argentina | +54 9 11 5555-0100 | alex.rivera@example.com

## PERFIL
Ingeniero con experiencia en Docker y Python.
"""
    keywords = ["Docker", "Python"]
    output_docx = compile_markdown_to_docx(cv_markdown, keywords=keywords)

    doc = docx.Document(str(output_docx))
    last_p = doc.paragraphs[-1]
    # No appendix paragraph should be appended
    assert "Ingeniero con experiencia en Docker y Python." in last_p.text

def test_docx_strips_backticks_from_markdown(tmp_path, monkeypatch):
    monkeypatch.setattr("core.docx_generator.EXPORTS_DIR", tmp_path)

    cv_markdown = """# ALEX RIVERA
Buenos Aires, Argentina | +54 9 11 5555-0100 | alex.rivera@example.com | https://linkedin.com/in/alex-rivera

## PERFIL
Desarrollo con `Python` y bases de datos `PostgreSQL`.

## EXPERIENCIA
### Desarrollador | Freelance | 2025 - 2026
• **Logro Principal:** Implementé `FastAPI` con `Redis` en producción.
"""
    output_docx = compile_markdown_to_docx(cv_markdown)
    doc = docx.Document(str(output_docx))

    all_text = " ".join(p.text for p in doc.paragraphs)
    assert "`" not in all_text, "Found backtick in generated DOCX text"
    assert "Python" in all_text
    assert "PostgreSQL" in all_text
    assert "FastAPI" in all_text
    assert "Redis" in all_text


def test_docx_stealth_ats_appendix_is_hidden_and_contains_only_delta_keywords(tmp_path, monkeypatch):
    monkeypatch.setattr("core.docx_generator.EXPORTS_DIR", tmp_path)
    cv_markdown = """# ALEX RIVERA
Buenos Aires, Argentina | +54 9 11 5555-0100 | alex.rivera@example.com
## PERFIL
Especialista en Python y PostgreSQL.
## EXPERIENCIA
### VELORA SISTEMAS | Analista Técnico | 2018 - 2025
• **Desarrollo:** Desarrollé APIs REST y consultas SQL.
"""
    # Keywords donde "Python", "SQL" y "APIs REST" YA están en el CV, y "Docker" no lo está
    keywords = ["Python", "SQL", "APIs REST", "Docker", "Kubernetes"]
    output_docx = compile_markdown_to_docx(cv_markdown, keywords=keywords)

    doc = docx.Document(str(output_docx))
    last_p = doc.paragraphs[-1]

    # 1. No debe contener título explícito visible para el ojo humano
    assert "Palabras clave y tecnologías:" not in last_p.text
    assert "[Índice Técnico de Compatibilidad ATS:" not in last_p.text

    # 2. Solo debe incluir las palabras faltantes (delta), NO las ya presentes
    assert "Python" not in last_p.text, "Python ya está en el CV, no debe repetirse en el apéndice"
    assert "SQL" not in last_p.text, "SQL ya está en el CV, no debe repetirse en el apéndice"
    assert "Docker" in last_p.text
    assert "Kubernetes" in last_p.text

    # 3. Estilo invisible: tamaño <= 1pt, color blanco, vanish xml
    run = last_p.runs[0]
    assert run.font.size.pt <= 1.0
    assert run.font.color.rgb == docx.shared.RGBColor(255, 255, 255)
    rPr = run._r.get_or_add_rPr()
    assert len(rPr.xpath("./w:vanish")) > 0

