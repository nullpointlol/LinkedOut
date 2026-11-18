"""Tests para Task 2: Reemplazo del patrón rígido de viñetas y restricción a 3-4 negritas críticas en el Redactor."""
import re
import pytest
from core.prompts_evaluacion import WRITER_PROMPT


def test_writer_prompt_does_not_mandate_rigid_bullet_labels():
    """Verifica que WRITER_PROMPT no contiene la exigencia de etiquetas temáticas rígidas en negrita al inicio de cada viñeta."""
    # 1. No debe exigir el patrón rígido de «• **Etiqueta Temática:**»
    assert "• **Etiqueta Temática:**" not in WRITER_PROMPT, (
        "WRITER_PROMPT no debe exigir el patrón rígido '• **Etiqueta Temática:**'"
    )
    assert "Cada viñeta DEBE seguir estrictamente el patrón" not in WRITER_PROMPT, (
        "WRITER_PROMPT no debe exigir el patrón estricto de viñetas con etiquetas"
    )

    # 2. Debe indicar formato natural de viñeta iniciando simplemente con '• '
    prompt_lower = WRITER_PROMPT.lower()
    assert "• " in WRITER_PROMPT or "•" in WRITER_PROMPT, "WRITER_PROMPT debe instruir viñetas limpias con •"
    assert "google xyz" in prompt_lower, "WRITER_PROMPT debe preservar el formato Google XYZ"


def test_writer_prompt_restricts_bolds_to_3_or_4_critical_items():
    """Verifica que WRITER_PROMPT restringe el uso de negritas a exactamente 3 o 4 ítems críticos en todo el CV."""
    prompt = WRITER_PROMPT

    # 1. Restricción explícita a 3 o 4 negritas
    assert ("3 o 4" in prompt or "3 a 4" in prompt or "3-4" in prompt) and "negrita" in prompt.lower(), (
        "WRITER_PROMPT debe contener la instrucción explícita de aplicar negrita a sólo 3 o 4 conceptos/palabras clave"
    )

    # 2. Prohibición de negritas en cada viñeta o etiquetas introductorias obligatorias en negrita
    prompt_lower = prompt.lower()
    assert "prohibido" in prompt_lower and ("cada viñeta" in prompt_lower or "etiquetas introductorias" in prompt_lower), (
        "WRITER_PROMPT debe prohibir aplicar negrita a cada viñeta o etiquetas obligatorias"
    )


def test_writer_prompt_enforces_a4_economy():
    """Verifica que WRITER_PROMPT establece de 5 a 6 viñetas totales en Experiencia (~120 a 190 caracteres) para economía A4."""
    prompt = WRITER_PROMPT

    # 1. Cuota total de 5 a 6 viñetas en experiencia
    assert "5 a 6 viñetas" in prompt or "5-6 viñetas" in prompt, (
        "WRITER_PROMPT debe establecer entre 5 y 6 viñetas en total para Experiencia"
    )

    # 2. Rango de caracteres de ~120 a 190 por viñeta
    assert "120" in prompt and "190" in prompt, (
        "WRITER_PROMPT debe especificar un rango de concisión de ~120 a 190 caracteres por viñeta"
    )
