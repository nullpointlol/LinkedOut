"""Tests para Task 3: Rebalanceo del Prompt del Redactor, Economía de 1 Página y Ortografía con *-icé*."""
import re
from pathlib import Path
import pytest

from core.prompts_evaluacion import WRITER_PROMPT
from core.writer import WriterAgent

# La base de prueba (el perfil real de cada candidato no se versiona)
EXP_PATH = Path(__file__).resolve().parent / "fixtures" / "candidato" / "experiencia.txt"


def test_experiencia_source_file_has_no_automatize_with_z():
    """La base de prueba no contiene 'Automatizé' ni 'automatizé'."""
    assert EXP_PATH.exists(), f"No se encontró {EXP_PATH}"
    content = EXP_PATH.read_text(encoding="utf-8")

    assert "Automatizé" not in content, "Se encontró 'Automatizé' en la base de prueba"
    assert "automatizé" not in content, "Se encontró 'automatizé' en la base de prueba"
    assert "Automaticé" in content, "Se esperaba 'Automaticé' en la base de prueba"


def test_writer_prompt_contains_ice_orthography_mandate():
    """Verifica que WRITER_PROMPT prohíbe explícitamente la terminación *-izé* y exige *-icé*."""
    prompt = WRITER_PROMPT

    # Prohibición explícita de *-izé* y mandato de *-icé*
    assert "-icé" in prompt, "WRITER_PROMPT debe exigir la terminación con -icé"
    assert "-izé" in prompt or "izé" in prompt, "WRITER_PROMPT debe prohibir explícitamente la terminación con -izé"

    # Verificación de verbos clave mencionados en el brief
    assert "estandaricé" in prompt, "WRITER_PROMPT debe incluir 'estandaricé'"
    assert "optimicé" in prompt, "WRITER_PROMPT debe incluir 'optimicé'"
    assert "automaticé" in prompt, "WRITER_PROMPT debe incluir 'automaticé'"


def test_writer_prompt_mandates_five_to_six_bullets_and_three_line_skills():
    """Verifica que WRITER_PROMPT establece la cuota total de 5-6 viñetas ponderadas y máximo 3 líneas en habilidades."""
    prompt = WRITER_PROMPT

    # 1. Cuota total de viñetas en experiencia
    assert "5 a 6 viñetas" in prompt or "5-6 viñetas" in prompt, (
        "WRITER_PROMPT debe establecer una cuota total de 5 a 6 viñetas en Experiencia"
    )

    # 2. Ponderación por afinidad
    assert "afinidad" in prompt.lower(), "WRITER_PROMPT debe mencionar ponderación según afinidad con la vacante"

    # 3. Habilidades técnicas: título + 2 viñetas (estrictamente 3 líneas)
    assert "3 líneas" in prompt or "3 renglones" in prompt, (
        "WRITER_PROMPT debe restringir Habilidades Técnicas a 3 líneas de altura"
    )
    assert "2 viñetas" in prompt, (
        "WRITER_PROMPT debe especificar 2 viñetas temáticas en Habilidades Técnicas"
    )

    # 4. Perfil: 3-4 líneas (en lugar de 4 a 6)
    assert "3 a 4 líneas" in prompt or "3-4 líneas" in prompt, (
        "WRITER_PROMPT debe instruir 3 a 4 líneas en la sección PERFIL"
    )


def test_deterministic_fallback_has_canonical_order_and_populated_sections():
    """Verifica que _deterministic_fallback produce el orden exacto, 6 viñetas ponderadas (3, 2, 1),

    Habilidades Técnicas en 3 líneas (título + 2 viñetas), Educación e Idiomas poblados y sin faltas de ortografía con *-izé*.
    """
    agent = WriterAgent()
    puesto = "Analista Técnico Funcional"
    empresa = "Telecom Argentina"
    analysis = {
        "puesto": puesto,
        "empresa": empresa,
        "dolores_empresa": ["Optimizar pipelines CI/CD", "Reducir incidentes en producción"],
        "requisitos": [
            {"nombre": "Python", "estado": "MATCH_DIRECTO", "approved": True},
            {"nombre": "SQL", "estado": "MATCH_DIRECTO", "approved": True},
            {"nombre": "GitLab CI", "estado": "MATCH_DIRECTO", "approved": True},
            {"nombre": "Codacy", "estado": "MATCH_DIRECTO", "approved": True},
            {"nombre": "PagerDuty", "estado": "MATCH_DIRECTO", "approved": True},
            {"nombre": "Jira", "estado": "MATCH_DIRECTO", "approved": True},
        ],
    }
    profile = agent.load_candidate_profile()

    result = agent._deterministic_fallback(puesto, empresa, analysis, profile)
    cv_md = result["cv_markdown"]

    # 1. No debe contener ninguna falta de ortografía con *-izé*
    assert "izé" not in cv_md.lower(), f"Se detectó terminación errónea -izé en el CV de fallback: {cv_md}"
    assert "Automaticé" in cv_md or "automaticé" in cv_md, "Se esperaba 'Automaticé' en el CV de fallback"

    # 2. Orden canónico de encabezados
    h2_indices = {}
    for h2 in ["PERFIL", "EXPERIENCIA", "HABILIDADES TÉCNICAS", "EDUCACIÓN", "IDIOMAS"]:
        pattern = rf"^##\s+{re.escape(h2)}"
        match = re.search(pattern, cv_md, re.MULTILINE)
        assert match is not None, f"Sección {h2} no encontrada en CV"
        h2_indices[h2] = match.start()

    assert h2_indices["PERFIL"] < h2_indices["EXPERIENCIA"], "PERFIL debe estar antes de EXPERIENCIA"
    assert h2_indices["EXPERIENCIA"] < h2_indices["HABILIDADES TÉCNICAS"], "EXPERIENCIA antes de HABILIDADES"
    assert h2_indices["HABILIDADES TÉCNICAS"] < h2_indices["EDUCACIÓN"], "HABILIDADES antes de EDUCACIÓN"
    assert h2_indices["EDUCACIÓN"] < h2_indices["IDIOMAS"], "EDUCACIÓN antes de IDIOMAS"

    # 3. Ponderación de viñetas en Experiencia (exactamente 6 viñetas en total: 3 Freelance, 2 VELORA SISTEMAS, 1 KORVA)
    lines = cv_md.splitlines()
    bullets_by_company = {"FREELANCE": 0, "VELORA SISTEMAS": 0, "KORVA": 0}
    current_company = None
    in_skills = False
    skills_bullets = []
    in_educacion = False
    educacion_bullets = []
    in_idiomas = False
    idiomas_bullets = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            in_skills = "HABILIDADES" in stripped.upper()
            in_educacion = "EDUCACIÓN" in stripped.upper() or "EDUCACION" in stripped.upper()
            in_idiomas = "IDIOMAS" in stripped.upper()
            if "EXPERIENCIA" not in stripped.upper():
                current_company = None
            continue

        if stripped.startswith("### "):
            upper_sub = stripped.upper()
            if "FREELANCE" in upper_sub:
                current_company = "FREELANCE"
            elif "VELORA SISTEMAS" in upper_sub:
                current_company = "VELORA SISTEMAS"
            elif "KORVA" in upper_sub:
                current_company = "KORVA"
            else:
                current_company = None

        if current_company and stripped.startswith(("•", "*", "-")):
            bullets_by_company[current_company] += 1

        if in_skills and stripped.startswith(("•", "*", "-")):
            skills_bullets.append(stripped)

        if in_educacion and stripped.startswith(("•", "*", "-")):
            educacion_bullets.append(stripped)

        if in_idiomas and stripped.startswith(("•", "*", "-")):
            idiomas_bullets.append(stripped)

    # 6 viñetas totales en Experiencia (3 Freelance, 2 VELORA SISTEMAS, 1 KORVA)
    assert bullets_by_company["FREELANCE"] == 3, f"Freelance debe tener exactamente 3 viñetas, tuvo {bullets_by_company['FREELANCE']}"
    assert bullets_by_company["VELORA SISTEMAS"] == 2, f"VELORA SISTEMAS debe tener exactamente 2 viñetas, tuvo {bullets_by_company['VELORA SISTEMAS']}"
    assert bullets_by_company["KORVA"] == 1, f"KORVA debe tener exactamente 1 viñeta, tuvo {bullets_by_company['KORVA']}"
    total_exp_bullets = sum(bullets_by_company.values())
    assert total_exp_bullets == 6, f"Experiencia debe tener exactamente 6 viñetas en total, tuvo {total_exp_bullets}"

    # Habilidades técnicas: exactamente 2 viñetas (altura estricta de 3 líneas con el H2)
    assert len(skills_bullets) == 2, f"Habilidades Técnicas debe tener exactamente 2 viñetas, tuvo {len(skills_bullets)}"

    # Educación e Idiomas poblados
    assert len(educacion_bullets) >= 2, f"Educación debe tener al menos 2 viñetas, tuvo {len(educacion_bullets)}"
    assert any("Tecnicatura" in b for b in educacion_bullets)
    assert any("Bachiller" in b for b in educacion_bullets)

    assert len(idiomas_bullets) >= 2, f"Idiomas debe tener al menos 2 viñetas, tuvo {len(idiomas_bullets)}"
    assert any("Inglés" in b or "Ingles" in b for b in idiomas_bullets)
    assert any("Español" in b or "Espanol" in b for b in idiomas_bullets)
