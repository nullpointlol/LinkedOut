"""Regresiones de voz y validación final, con datos y bridge sintéticos."""
import os

import pytest

from core import email_composer as composer
from tests.conftest import VOZ_TEST


PROFILE = {"nombre": "ANA PRUEBA", "contacto": "ana@example.com | https://linkedin.com/in/ana-prueba",
           "rol": "Analista de Calidad", "anios_experiencia": 7}
POST = {"texto": "Buscamos QA con 99 años de experiencia.",
        "triage": {"puesto": "QA Analyst", "email_destino": "rrhh@example.com"}}
PITCH = {"pitch": "Probé integraciones con SQL.", "dolores_empresa": ["Regresiones en producción"]}
FIJAS = "\n".join([VOZ_TEST["disponibilidad"], VOZ_TEST["ubicacion"]])
LIMPIO = {"subject": "QA", "body": "Hola, me interesa. Probé integraciones con SQL.\n\n" + FIJAS}


class BridgeFalso:
    def __init__(self, *respuestas):
        self.respuestas = iter(respuestas)
        self.calls = []

    async def run_prompt(self, prompt, **kwargs):
        self.calls.append(prompt)
        respuesta = next(self.respuestas)
        if isinstance(respuesta, Exception):
            raise respuesta
        return respuesta


async def _invariantes():
    bridge = BridgeFalso(LIMPIO)
    r = await composer.compose_email(POST, PITCH, PROFILE, bridge)
    # invariante: identidad, firma literal, destinatario y cuota del caso limpio.
    assert r["to"] == "rrhh@example.com"
    assert r["body"].endswith("ANA PRUEBA\nLinkedIn")
    assert len(bridge.calls) == 1
    assert r["is_fallback"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("anios", [7, None, "7", True, 7.5])
async def test_prompt_rol_anios_y_dolores(anios):
    await _invariantes()
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    p = composer.build_composer_prompt(POST, PITCH, {**PROFILE, "anios_experiencia": anios}, {})
    assert "Soy Analista de Calidad" in p
    assert "Regresiones en producción" in p
    assert "responsabilidades" in p and "conocimientos" in p
    assert "sin frases sueltas en presente" in p and "No uses XYZ" in p
    if type(anios) is int:
        assert "con +7 años" in p  # 08/11: formato «+N» pedido por el creador
    else:
        assert "sin cantidad de años" in p
        resumen = p.split("  3.", 1)[1].split("  4.", 1)[0]
        assert "con 7 años" not in resumen and "con 99 años" not in resumen
    # Sin rol en el perfil (el candidato de prueba no declara Puesto:) no se inventa uno (13/11)
    sin_rol = composer.build_composer_prompt(POST, PITCH, {}, {})
    assert "Soy Analista" not in sin_rol and "«Tengo experiencia en [frente 1]" in sin_rol


@pytest.mark.asyncio
@pytest.mark.parametrize("canal,campo", [("email", "subject"), ("email", "body"), ("whatsapp", "body")])
async def test_segunda_version_y_asunto_invalidos(campo, canal):
    await _invariantes()
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    bridge = BridgeFalso({**LIMPIO, campo: "Estimado equipo"}, {**LIMPIO, campo: "No solo QA!"})
    post = POST if canal == "email" else {**POST, "canal": canal, "destino_canal": "https://wa.me/12345"}
    with pytest.raises(RuntimeError, match="Revisá el mensaje: ") as exc:
        await composer.compose_message(post, PITCH, PROFILE, bridge)
    assert type(exc.value).__name__ == "MensajeRequiereRevision"
    assert any("exclamación" in m for m in exc.value.motivos)
    assert str(exc.value) == "Revisá el mensaje: " + "; ".join(exc.value.motivos)
    assert len(bridge.calls) == 2
    assert campo in bridge.calls[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("segunda", [TimeoutError("sin respuesta"), {}, {"body": ""}, None,
                                    {"subject": "", "body": "Hola"}])
async def test_reescritura_fallida_requiere_revision(segunda):
    await _invariantes()
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    bridge = BridgeFalso({**LIMPIO, "body": "Estimado equipo"}, segunda)
    with pytest.raises(RuntimeError, match="Revisá el mensaje: ") as exc:
        await composer.compose_email(POST, PITCH, PROFILE, bridge)
    assert type(exc.value).__name__ == "MensajeRequiereRevision"
    assert any("estimado" in m for m in exc.value.motivos)
    assert len(bridge.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("reescribir", [False, True])
async def test_cuota_una_o_dos(reescribir):
    await _invariantes()
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    respuesta = {"subject": "QA – ANA PRUEBA", "body": "Hola — me interesa. Probé integraciones con SQL.\n\n" + FIJAS}
    bridge = BridgeFalso(*([{**LIMPIO, "body": "Estimado equipo"}] if reescribir else []), respuesta)
    r = await composer.compose_email(POST, PITCH, PROFILE, bridge)
    assert len(bridge.calls) == (2 if reescribir else 1)
    assert r["subject"] == "QA - ANA PRUEBA"
    assert r["body"].startswith("Hola - me interesa.")
    assert r["body"].endswith(composer.build_email_signature(PROFILE))
    assert r["to"] == "rrhh@example.com" and not r["is_fallback"]


@pytest.mark.asyncio
@pytest.mark.parametrize("fuente", ["firma", "fija", "fallback"])
async def test_valida_datos_agregados_y_fallback(monkeypatch, fuente):
    await _invariantes()
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    profile = {**PROFILE, "nombre": "ANA PRUEBA!"} if fuente == "firma" else PROFILE
    if fuente == "fija":
        monkeypatch.setattr(composer, "load_voz_email", lambda: {**VOZ_TEST, "ubicacion": "Soy de Villa Test!"})
    bridge = BridgeFalso(TimeoutError("técnico")) if fuente == "fallback" else BridgeFalso(LIMPIO, LIMPIO)
    pitch = {**PITCH, "pitch": "No solo QA!"} if fuente == "fallback" else PITCH
    with pytest.raises(RuntimeError, match="Revisá el mensaje: ") as exc:
        await composer.compose_email(POST, pitch, profile, bridge)
    assert type(exc.value).__name__ == "MensajeRequiereRevision"
    assert any("exclamación" in m for m in exc.value.motivos)
    assert len(bridge.calls) == (2 if fuente == "fija" else 1)


@pytest.mark.asyncio
async def test_tokens_protegidos_y_firma_literales():
    await _invariantes()
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    profile = {**PROFILE, "nombre": "ANA–PRUEBA"}
    subject = "REF–77 https://example.com/qa–ref"
    bridge = BridgeFalso({"subject": subject, "body": LIMPIO["body"] + "\n`QA–77`"})
    r = await composer.compose_email(POST, PITCH, profile, bridge)
    assert r["subject"] == subject
    assert "`QA–77`" in r["body"]
    assert r["body"].endswith(composer.build_email_signature(profile))
    assert len(bridge.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("reescribir", [False, True])
async def test_frases_fijas_normalizadas_no_se_duplican(monkeypatch, reescribir):
    await _invariantes()
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    monkeypatch.setattr(composer, "load_voz_email", lambda: {**VOZ_TEST, "ubicacion": "Soy de Villa – Test."})
    respuesta = {**LIMPIO, "body": LIMPIO["body"].replace(VOZ_TEST["ubicacion"], "Soy de Villa – Test.")}
    bridge = BridgeFalso(*([{**LIMPIO, "body": "Estimado equipo"}] if reescribir else []), respuesta)
    r = await composer.compose_email(POST, PITCH, PROFILE, bridge)
    assert r["body"].count("Soy de Villa - Test.") == 1
    assert "–" not in r["body"]
    assert len(bridge.calls) == (2 if reescribir else 1)
