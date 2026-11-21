import re
import pytest
from core.writer import WriterAgent, sanitize_cv_formatting


def test_sanitize_cv_formatting_removes_intra_bullet_bolds_and_perfil_asterisks():
    dirty_cv = """# SAM ORTEGA
ANALISTA FUNCIONAL
Ciudad Ejemplo, Buenos Aires | 1155550100

## PERFIL
Analista con más de **7 años de experiencia** y foco en **APIs REST**.

## EXPERIENCIA
### Analista | Freelance | 2025 - 2026
• **Definición Funcional:** Traduje requerimientos en **Jira** con **Python** y **SQL** logrando alta eficiencia.
• **QA & Pruebas:** Diseñé matrices de testing con **Postman** y **Swagger**.

## HABILIDADES TÉCNICAS
• **Gestión Funcional:** Jira, Scrum.
• **Desarrollo:** Python, SQL.

## EDUCACIÓN
• Tecnicatura en Informática

## IDIOMAS
• Español: Nativo
"""

    cleaned = sanitize_cv_formatting(dirty_cv)
    assert "**7 años de experiencia**" not in cleaned
    assert "más de 7 años de experiencia y foco en APIs REST." in cleaned
    assert "• **Definición Funcional:** Traduje requerimientos en Jira con Python y SQL logrando alta eficiencia." in cleaned
    assert "• **QA & Pruebas:** Diseñé matrices de testing con Postman y Swagger." in cleaned
