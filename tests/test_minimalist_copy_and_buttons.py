import pytest
from pathlib import Path
from core.writer import synthesize_multichannel_pitches
from core.prompts_evaluacion import WRITER_PROMPT, STRATEGIST_PROMPT

def test_synthesize_multichannel_pitches_minimalist_email():
    """El email formal por defecto debe ser conciso (< 650 caracteres) y con formato cordial en 2 párrafos."""
    pitches = synthesize_multichannel_pitches(
        pitch="",
        puesto="Tech Lead",
        empresa="MercadoLibre"
    )
    email = pitches.get("email", "")
    assert len(email) < 650, f"Email formal demasiado extenso: {len(email)} caracteres"
    assert "Asunto: Postulación Tech Lead - Alex Rivera" in email
    assert "Saludos cordiales," in email or "Atentamente," in email
    # Debe tener como máximo 2 párrafos en el cuerpo antes de la firma
    paragraphs = [p.strip() for p in email.split("\n\n") if p.strip() and not p.startswith("Asunto:")]
    assert len(paragraphs) <= 3, f"Se esperaban máximo 3 bloques (2 cuerpo + firma), se obtuvieron {len(paragraphs)}"

def test_writer_and_strategist_prompts_instruct_minimalism():
    """Los prompts deben indicar directivas explícitas de concisión para emails y dolores."""
    assert "minimalista y cordial" in WRITER_PROMPT or "2 párrafos concisos" in WRITER_PROMPT or "conciso" in WRITER_PROMPT
    assert "conceptos clave de una línea" in STRATEGIST_PROMPT or "1 línea" in STRATEGIST_PROMPT or "conciso" in STRATEGIST_PROMPT

def test_buttons_renamed_in_index_html():
    """Los botones de acción deben estar claramente desambiguados y rotulados:
    - 'Volver a analizar' en vez de sólo 'Regenerar'
    - 'Actualizar CV con Selección' en vez de 'Recalcular CV'
    """
    html = Path("static/index.html").read_text(encoding="utf-8")
    assert "Volver a analizar" in html, "btnRegenerarGlobal debe mostrar 'Volver a analizar'"
    assert "Actualizar CV con Selección" in html, "btnRecalcularTags debe mostrar 'Actualizar CV con Selección'"

def test_app_js_dolores_styling_is_minimalist():
    """En static/app.js los dolores deben renderizarse con diseño minimalista."""
    js = Path("static/app.js").read_text(encoding="utf-8")
    assert "flex items-start gap-1.5" in js or "fa-bolt" in js
