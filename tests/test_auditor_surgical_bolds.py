"""Tests para Task 3: Calibración de escaneabilidad en el Auditor Ácido para viñetas naturales y negritas quirúrgicas."""
import pytest
from core.auditor import evaluate_ats_compliance, AuditorAgent
from core.prompts_evaluacion import AUDITOR_PROMPT


def test_auditor_does_not_penalize_bullets_without_rigid_labels():
    """Un CV con viñetas limpias '• Desarrollé X...' con 3-4 negritas selectivas
    debe obtener 20.0 pts de escaneabilidad y cero deducciones por falta de etiquetas temáticas.
    """
    cv = """# JUAN PÉREZ - TECH LEAD & BACKEND ARCHITECT
juan@example.com | +54 11 1234-5678 | Buenos Aires, Argentina

## PERFIL
Tech Lead con más de 12 años liderando equipos y diseñando arquitecturas distribuidas resilientes de alto impacto.

## EXPERIENCIA

### FREELANCE - TECH LEAD & ARCHITECT
01/2025 - Presente
• Lideré la arquitectura cloud en **AWS** reduciendo costos operativos un 35% en 6 meses mediante migración serverless.
• Diseñé microservicios distribuidos en **Python** procesando más de 500k transacciones diarias con alta disponibilidad.

### VELORA SISTEMAS - SR SOFTWARE ENGINEER
03/2018 - 12/2024
• Automaticé pipelines de integración continua en **FastAPI** disminuyendo tiempos de despliegue en un 40% en 15 proyectos.
• Implementé componentes modulares bajo arquitectura hexagonal y matrices de prueba para banca regional.

### KORVA - SOFTWARE DEVELOPER
01/2011 - 02/2018
• Desarrollé servicios transaccionales en SQL garantizando consistencia de datos y tiempos de respuesta submilisegundo.

## HABILIDADES TÉCNICAS
• Lenguajes & Frameworks: Python, FastAPI, TypeScript, React, SQL.
• Infraestructura & DevOps: AWS, Docker, Kubernetes, CI/CD, Git.

## EDUCACIÓN
• Licenciatura en Sistemas de Información - Universidad de Buenos Aires (2006 - 2011).

## IDIOMAS
• Español: Nativo.
• Inglés: C1 Profesional / Fluido.
"""
    keywords = ["Python", "FastAPI", "AWS", "SQL", "Docker"]
    expected_companies = ["FREELANCE", "VELORA SISTEMAS", "KORVA"]

    res = evaluate_ats_compliance(cv, keywords, expected_companies=expected_companies)

    # 1. Escaneabilidad debe otorgar los 20.0 pts completos
    assert res["dimensiones"]["escaneabilidad"] == 20.0, (
        f"Se esperaban 20.0 pts de escaneabilidad para viñetas naturales concisas con verbos en 1ª persona, obtenido: {res['dimensiones']['escaneabilidad']}"
    )

    # 2. No debe haber ninguna deducción por falta de etiquetas temáticas
    deducciones_texto = " ".join(res.get("deducciones", []))
    assert "etiqueta" not in deducciones_texto.lower(), (
        f"No debe existir deducción que mencione etiquetas temáticas: {res.get('deducciones')}"
    )
    assert "escaneabilidad" not in deducciones_texto.lower(), (
        f"No debe existir deducción en escaneabilidad cuando las viñetas son concisas y en 1ª persona: {res.get('deducciones')}"
    )

    # 3. Score ATS total debe ser 100.0 con todas las dimensiones perfectas
    assert res["ats_score"] == 100.0
    assert res["deducciones"] == []


def test_auditor_prompt_updated():
    """AUDITOR_PROMPT no debe exigir el patrón rígido '• **Etiqueta Temática:**' en cada viñeta,
    sino concisión, legibilidad en 10-15s y uso sobrio de negritas (3-4 focos).
    """
    # 1. No debe exigir etiquetas obligatorias en cada viñeta
    assert "• **Etiqueta Temática:**" not in AUDITOR_PROMPT, (
        "AUDITOR_PROMPT no debe contener el patrón rígido '• **Etiqueta Temática:**'"
    )
    assert "• **Etiqueta" not in AUDITOR_PROMPT, (
        "AUDITOR_PROMPT no debe exigir etiquetas en negrita al inicio de las viñetas"
    )

    # 2. Debe evaluar concisión (<= 250 caracteres) y lectura en 10-15s
    prompt_lower = AUDITOR_PROMPT.lower()
    assert "10-15s" in prompt_lower or "10-15" in prompt_lower or "10 a 15" in prompt_lower, (
        "AUDITOR_PROMPT debe mencionar la escaneabilidad en 10-15s"
    )
    assert "250" in AUDITOR_PROMPT, (
        "AUDITOR_PROMPT debe mencionar el límite de concisión de 250 caracteres por viñeta"
    )

    # 3. Debe mencionar el uso sobrio de negritas quirúrgicas (3-4 focos)
    assert "3-4" in AUDITOR_PROMPT or "3 o 4" in AUDITOR_PROMPT or "3 a 4" in AUDITOR_PROMPT, (
        "AUDITOR_PROMPT debe mencionar el uso sobrio de negritas (3-4 focos indispensables)"
    )
