"""Unitarios de funciones de apoyo de `core/radar.py` (no del ciclo del Radar): pausa por bloqueo
con datos dañados o con zona horaria, cancelación de tareas, validación del email y progreso."""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

import pytest

from core import radar
from core.radar import RadarLoop


def test_pausa_con_ahora_con_zona_horaria(monkeypatch):
    """Un `ahora` con zona horaria se pasa a hora local; con un bloqueo reciente hay pausa."""
    ultimo = datetime.now().replace(microsecond=0) - timedelta(minutes=10)
    monkeypatch.setattr(radar.radar_store, "resumen_lecturas",
                        lambda **k: {"analista": {"ultimo_inicio": ultimo, "motivos": ["bloqueo"]}})
    monkeypatch.setattr(radar.radar_store, "inicios_bloqueo", lambda desde, **k: [desde])
    pausa = radar.pausa_busquedas(datetime.now(timezone.utc))
    assert pausa is not None
    assert pausa["bloqueos_24h"] == 1
    assert pausa["desde"] == ultimo.isoformat(timespec="seconds")


def test_pausa_con_error_en_la_base_no_pausa(monkeypatch, caplog):
    """Si leer las lecturas falla, se registra el error y no se pausa."""
    def roto(**k):
        raise RuntimeError("base bloqueada")

    monkeypatch.setattr(radar.radar_store, "resumen_lecturas", roto)
    assert radar.pausa_busquedas(datetime.now()) is None
    assert "Error calculando pausa" in caplog.text


@pytest.mark.asyncio
async def test_cancelar_tarea_que_falla_al_detenerse(caplog):
    """Cancelar una tarea que termina con error lo registra sin propagarlo."""
    async def tarea():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            raise RuntimeError("cierre sucio")

    t = asyncio.create_task(tarea())
    await asyncio.sleep(0)
    with caplog.at_level(logging.ERROR, logger="core.radar"):
        await RadarLoop._cancelar(t, "Worker de prueba")
    assert "Worker de prueba terminó con error al detenerlo" in caplog.text


@pytest.mark.asyncio
async def test_cancelar_tarea_cancelada_o_inexistente():
    """Sin tarea, o con una que se cancela normalmente, no hay error."""
    await RadarLoop._cancelar(None, "nada")
    t = asyncio.create_task(asyncio.sleep(10))
    await asyncio.sleep(0)
    await RadarLoop._cancelar(t, "sleep")
    assert t.cancelled()


def test_validar_email_rechaza_incompletos():
    """Un email que no es dict, sin asunto (canal email) o sin cuerpo lanza ValueError."""
    for email, requiere, error in [
        ("texto", True, "no devolvió un email"),
        ({"subject": "  ", "body": "x"}, True, "sin asunto"),
        ({"subject": "Hola", "body": " "}, True, "sin cuerpo"),
    ]:
        with pytest.raises(ValueError, match=error):
            RadarLoop._validar_email(email, requiere_asunto=requiere)


def test_validar_email_sin_asunto_fuera_del_canal_email():
    """Fuera del canal email, el asunto ausente se toma como vacío y se limpian espacios."""
    assert RadarLoop._validar_email({"to": " rrhh@acme.example ", "body": " Hola "}, requiere_asunto=False) == (
        "rrhh@acme.example", "", "Hola")


def test_guardar_progreso_que_falla_solo_se_registra(monkeypatch, caplog):
    """Si guardar el progreso falla, se registra y no corta la generación."""
    def roto(post_id, prog):
        raise RuntimeError("disco")

    monkeypatch.setattr(radar.radar_store, "guardar_progreso", roto)
    RadarLoop._guardar_progreso(5, {"pct": 10})
    assert "No se pudo guardar el progreso del post 5" in caplog.text


def test_sigue_generando_segun_el_estado(monkeypatch):
    """Solo un post en estado GENERANDO sigue generando; uno borrado no."""
    estados = {1: {"estado": "GENERANDO"}, 2: {"estado": "LISTO"}}
    monkeypatch.setattr(radar.radar_store, "get_post", lambda pid: estados.get(pid))
    assert [RadarLoop._sigue_generando(i) for i in (1, 2, 3)] == [True, False, False]
