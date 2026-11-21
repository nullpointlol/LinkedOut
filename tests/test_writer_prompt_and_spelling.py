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
