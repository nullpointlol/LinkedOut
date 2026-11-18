import os
from pathlib import Path
import docx
from core.docx_generator import compile_markdown_to_docx

def test_compile_markdown_to_docx():
    sample_md = """# SAM ORTEGA
Belgrano, Buenos Aires | sam.ortega@example.com | +54 9 11 0000-0000

## PERFIL
Analista Técnico Funcional con 7 años de experiencia en automatización de software.

## EXPERIENCIA
### Analista Técnico Funcional | VELORA SISTEMAS | 2018 - 2025
• Automatizó la recolección de métricas de calidad en 90 aplicaciones reduciendo deuda técnica.
• Redujo en 35% los tiempos de resolución de incidentes críticos.

## HABILIDADES TÉCNICAS
• Python, SQL, Jenkins, Codacy, APIs

## EDUCACIÓN
• Tecnicatura en Informática - 2016

## IDIOMAS
• Inglés avanzado, Español nativo
"""
    output_path = compile_markdown_to_docx(sample_md, output_filename="test_output.docx")
    assert output_path.exists()
    assert output_path.suffix == ".docx"
    
    # Validar que el docx se abre y tiene contenido estructurado
    doc = docx.Document(str(output_path))
    assert len(doc.paragraphs) >= 8
    
    # Validar que el nombre del candidato está en el primer párrafo
    text_content = "\n".join([p.text for p in doc.paragraphs])
    assert "SAM ORTEGA" in text_content
    assert "VELORA SISTEMAS" in text_content
    assert "PERFIL" in text_content

    # Limpiar archivo de prueba
    if output_path.exists():
        os.remove(output_path)
