import re
import pytest
from core.writer import WriterAgent, sanitize_cv_formatting


def test_writer_cv_has_thematic_tags_and_target_role():
    writer = WriterAgent()
    analysis = {
        "puesto": "Senior Technical Business Analyst",
        "empresa": "Mercado Libre",
        "dolores_empresa": ["Optimizar integraciones y CI/CD"],
        "requisitos": [
            {"nombre": "Python", "estado": "MATCH_DIRECTO", "approved": True},
            {"nombre": "SQL", "estado": "MATCH_DIRECTO", "approved": True},
            {"nombre": "Jenkins", "estado": "MATCH_DIRECTO", "approved": True},
        ]
    }
    profile = writer.load_candidate_profile()
    result = writer._deterministic_fallback("Senior Technical Business Analyst", "Mercado Libre", analysis, profile)
    cv_md = result["cv_markdown"]

    # 1. Puesto objetivo presente en el encabezado
    assert "SENIOR TECHNICAL BUSINESS ANALYST" in cv_md.upper()
    assert "Ciudad Ejemplo" in cv_md

    # 2. Bullets con patrón **Etiqueta Temática:** Acción + contexto + herramientas + resultado
    bullets_with_tags = re.findall(r"^[•*-]\s+\*\*([A-Za-zÁÉÍÓÚáéíóúñÑ /&–-]+:)\*\*\s+(.+)$", cv_md, re.MULTILINE)
    assert len(bullets_with_tags) >= 4, f"Se esperaban al menos 4 viñetas con etiquetas temáticas, encontradas: {len(bullets_with_tags)}"

    # 3. Regla estricta de negritas limpias: CERO negritas internas dispersas
    perfil_match = re.search(r"## PERFIL\s*\n(.*?)(?=\n##|\Z)", cv_md, re.DOTALL)
    assert perfil_match, "Debe existir la sección ## PERFIL"
    assert "**" not in perfil_match.group(1), "## PERFIL no debe contener asteriscos ni negritas dispersas"

    # En viñetas de experiencia: el cuerpo de la viñeta (después de la etiqueta temática) NO debe contener **
    for tag, body in bullets_with_tags:
        assert "**" not in body, f"El cuerpo de la viñeta '{tag}' no debe tener negritas internas: {body}"

    # 4. Habilidades técnicas reclasificadas por afinidad
    assert "Habilidades Técnicas" in cv_md or "HABILIDADES TÉCNICAS" in cv_md
    habilidades_tags = re.findall(r"^[•*-]\s+\*\*([A-Za-zÁÉÍÓÚáéíóúñÑ /&]+:)\*\*", cv_md, re.MULTILINE)
    assert len(habilidades_tags) >= 2


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
