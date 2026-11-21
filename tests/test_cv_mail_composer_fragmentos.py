"""El composer normaliza la prosa sin alterar fragmentos de identidad."""
from unittest.mock import AsyncMock

import pytest

from core import email_composer as composer


@pytest.mark.asyncio
@pytest.mark.parametrize("nombre", ["ANA PRUEBA", "ANA — PRUEBA"])
async def test_linkedin_en_prosa_no_pide_reescritura(nombre, monkeypatch):
    monkeypatch.setattr(composer, "_cargar_voz", lambda: {})
    perfil = {
        "nombre": nombre,
        "contacto": "+54 11 1234-5678 | https://linkedin.com/in/ana–prueba",
        "rol": "Analista de Calidad",
    }
    firma = f"{nombre}\n+54 11 1234-5678\nLinkedIn"
    enlace = "https://example.com/cv–qa"
    cuerpo = (
        "Hola,\n\nVi la búsqueda en LinkedIn — me interesa.\n"
        f"Probé integraciones con SQL. Mi CV: {enlace}\n\n"
        f"Adjunto mi CV.\nQuedo a disposición,\nSaludos,\n\n{firma}"
    )
    bridge = AsyncMock()
    bridge.run_prompt.return_value = {"subject": "Postulación QA", "body": cuerpo}

    resultado = await composer.compose_email(
        {"texto": "Buscamos analista de calidad.",
         "triage": {"puesto": "QA", "email_destino": "rrhh@example.com"}},
        {"pitch": "Probé integraciones con SQL."}, perfil, bridge,
    )

    assert resultado["body"] == cuerpo.replace(
        "LinkedIn — me interesa.", "LinkedIn - me interesa.")
    assert resultado["body"].endswith(firma)
    assert enlace in resultado["body"]
    assert "is_fallback" not in resultado
    assert bridge.run_prompt.await_count == 1
    assert 'href="https://linkedin.com/in/ana–prueba"' in composer.email_html(
        resultado["body"], perfil)


@pytest.mark.parametrize("literal", ["LinkedIn", "ANA — PRUEBA", "+54 11 1234-5678"])
def test_normalizacion_idempotente_protege_fragmento_no_linea(literal):
    firma = "ANA — PRUEBA\n+54 11 1234-5678\nLinkedIn"
    cuerpo = f"Antes — {literal} – después.\n\n{firma}"
    esperado = f"Antes - {literal} - después.\n\n{firma}"

    normalizado = composer._normalizar_cuerpo(cuerpo, firma)

    assert normalizado == esperado
    assert composer._normalizar_cuerpo(normalizado, firma) == esperado
