"""Pruebas de consistencia de cuota frente a respuestas viejas, agotamientos repetidos y cancelaciones."""
import asyncio
import contextlib
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from core import bridge as modulo_bridge
from core.bridge import AntigravityBridge, _EstadoCuota

ESTADO_LIBRE_EXACTO = {
    "agotada": False,
    "desde": None,
    "reintento": None,
    "renueva": None,
    "mensaje": "",
}


@pytest.fixture(autouse=True)
def aislar_cuota(monkeypatch):
    nueva_cuota = _EstadoCuota()
    monkeypatch.setattr(modulo_bridge, "CUOTA", nueva_cuota)
    return nueva_cuota


@contextlib.asynccontextmanager
async def scope_tareas():
    tareas = []

    def lanzar(coro_o_tarea):
        if isinstance(coro_o_tarea, asyncio.Task):
            t = coro_o_tarea
        else:
            t = asyncio.create_task(coro_o_tarea)
        tareas.append(t)
        return t

    try:
        yield lanzar
    finally:
        for t in tareas:
            if not t.done():
                t.cancel()
        if tareas:
            await asyncio.gather(*tareas, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "caso,resultado_esperado,excepcion",
    [
        ("exito", {"ok": True, "cv": "resultado"}, None),
        ("error", None, RuntimeError("fallo ajeno a cuota")),
    ],
)
async def test_respuesta_anterior_no_restablece(monkeypatch, caso, resultado_esperado, excepcion):
    a_iniciado = asyncio.Event()
    a_puede_terminar = asyncio.Event()

    async def fake_ejecutar(self, prompt, json_mode=True, timeout_seconds=180, effort=None, cwd=None):
        a_iniciado.set()
        await a_puede_terminar.wait()
        if excepcion is not None:
            raise excepcion
        return resultado_esperado

    monkeypatch.setattr(AntigravityBridge, "_ejecutar_prompt", fake_ejecutar)
    bridge = AntigravityBridge()

    async with scope_tareas() as lanzar:
        tarea_a = lanzar(bridge.run_prompt("prompt_a"))
        await asyncio.wait_for(a_iniciado.wait(), timeout=30)

        # Mientras A está en vuelo, B registra agotamiento de cuota
        modulo_bridge.CUOTA.agotada("RESOURCE_EXHAUSTED: cuota agotada")
        estado_esperado = dict(modulo_bridge.CUOTA.estado())
        assert estado_esperado["agotada"] is True
        assert estado_esperado["mensaje"] == "RESOURCE_EXHAUSTED: cuota agotada"

        # A finaliza
        a_puede_terminar.set()
        if excepcion is not None:
            with pytest.raises(RuntimeError, match="fallo ajeno a cuota"):
                await asyncio.wait_for(tarea_a, timeout=30)
        else:
            res = await asyncio.wait_for(tarea_a, timeout=30)
            assert res == resultado_esperado

        # La respuesta vieja (éxito o error ajeno) no debe borrar la pausa vigente
        assert modulo_bridge.CUOTA.estado() == estado_esperado


@pytest.mark.asyncio
async def test_recuperacion_posterior_restablece(monkeypatch):
    nueva_cuota = modulo_bridge.CUOTA
    nueva_cuota.agotada("cuota inicial")
    assert nueva_cuota.estado()["agotada"] is True

    dueno_esperando = asyncio.Event()
    seguidor_esperando = asyncio.Event()
    despertar = asyncio.Event()
    tarea_dueno = None
    tarea_seguidor = None

    orig_esperar_turno = nueva_cuota.esperar_turno

    async def dormir_controlado(segundos):
        actual = asyncio.current_task()
        if tarea_dueno is not None and actual is tarea_dueno:
            dueno_esperando.set()
        elif tarea_seguidor is not None and actual is tarea_seguidor:
            seguidor_esperando.set()
        await despertar.wait()
        await asyncio.sleep(0)

    async def esperar_turno_con_dormir(dormir=None):
        return await orig_esperar_turno(dormir=dormir_controlado if dormir is None else dormir)

    monkeypatch.setattr(nueva_cuota, "esperar_turno", esperar_turno_con_dormir)

    llamadas_fisicas = []

    async def fake_ejecutar(self, prompt, json_mode=True, timeout_seconds=180, effort=None, cwd=None):
        llamadas_fisicas.append({
            "prompt": prompt,
            "json_mode": json_mode,
            "timeout_seconds": timeout_seconds,
            "effort": effort,
            "cwd": cwd,
        })
        return {"prompt": prompt, "status": "recuperado"}

    monkeypatch.setattr(AntigravityBridge, "_ejecutar_prompt", fake_ejecutar)
    bridge = AntigravityBridge()

    cwd1 = Path("/tmp/dir1")
    cwd2 = Path("/tmp/dir2")

    async with scope_tareas() as lanzar:
        tarea_dueno = lanzar(bridge.run_prompt("prompt_uno", json_mode=True, timeout_seconds=60, effort="high", cwd=cwd1))
        await asyncio.wait_for(dueno_esperando.wait(), timeout=30)

        tarea_seguidor = lanzar(bridge.run_prompt("prompt_dos", json_mode=False, timeout_seconds=120, effort="low", cwd=cwd2))
        await asyncio.wait_for(seguidor_esperando.wait(), timeout=30)

        # Ambas llamadas están en espera confirmadas por eventos (cero ejecuciones físicas)
        assert len(llamadas_fisicas) == 0

        # Se adelanta el reintento (recuperación posterior) y se despiertan esperas
        nueva_cuota.reintentar_ya()
        despertar.set()

        res_dueno = await asyncio.wait_for(tarea_dueno, timeout=30)
        res_seguidor = await asyncio.wait_for(tarea_seguidor, timeout=30)

        assert res_dueno == {"prompt": "prompt_uno", "status": "recuperado"}
        assert res_seguidor == {"prompt": "prompt_dos", "status": "recuperado"}

        # Estado libre exacto restaurado
        assert nueva_cuota.estado() == ESTADO_LIBRE_EXACTO

        # Argumentos de llamadas físicas preservados
        assert len(llamadas_fisicas) == 2
        assert llamadas_fisicas[0] == {
            "prompt": "prompt_uno",
            "json_mode": True,
            "timeout_seconds": 60,
            "effort": "high",
            "cwd": cwd1,
        }
        assert llamadas_fisicas[1] == {
            "prompt": "prompt_dos",
            "json_mode": False,
            "timeout_seconds": 120,
            "effort": "low",
            "cwd": cwd2,
        }


@pytest.mark.asyncio
async def test_segundo_agotamiento_invalida_sondeo(monkeypatch):
    # Agotamiento 1
    modulo_bridge.CUOTA.agotada("agotamiento 1")
    modulo_bridge.CUOTA.reintentar_ya()

    sondeo_en_vuelo = asyncio.Event()
    sondeo_puede_terminar = asyncio.Event()

    async def fake_ejecutar(self, prompt, json_mode=True, timeout_seconds=180, effort=None, cwd=None):
        if prompt == "sondeo":
            sondeo_en_vuelo.set()
            await sondeo_puede_terminar.wait()
            return {"ok": True, "sondeo": 1}
        elif prompt == "recuperacion":
            return {"ok": True, "recuperacion": 1}
        return {"ok": True}

    monkeypatch.setattr(AntigravityBridge, "_ejecutar_prompt", fake_ejecutar)
    bridge = AntigravityBridge()

    async with scope_tareas() as lanzar:
        tarea_sondeo = lanzar(bridge.run_prompt("sondeo"))
        await asyncio.wait_for(sondeo_en_vuelo.wait(), timeout=30)

        # Mientras el sondeo está en vuelo, otra llamada choca con un segundo agotamiento
        modulo_bridge.CUOTA.agotada("agotamiento 2")
        estado_agotamiento_2 = dict(modulo_bridge.CUOTA.estado())
        assert estado_agotamiento_2["agotada"] is True
        assert estado_agotamiento_2["mensaje"] == "agotamiento 2"

        # El sondeo viejo completa su respuesta
        sondeo_puede_terminar.set()
        res_sondeo = await asyncio.wait_for(tarea_sondeo, timeout=30)
        assert res_sondeo == {"ok": True, "sondeo": 1}

        # El sondeo viejo NO debe borrar el segundo agotamiento
        assert modulo_bridge.CUOTA.estado() == estado_agotamiento_2

        # Y el sondeo viejo debe haber liberado su turno para una recuperación posterior
        modulo_bridge.CUOTA.reintentar_ya()
        tarea_recup = lanzar(bridge.run_prompt("recuperacion"))
        res_recup = await asyncio.wait_for(tarea_recup, timeout=30)
        assert res_recup == {"ok": True, "recuperacion": 1}
        assert modulo_bridge.CUOTA.estado() == ESTADO_LIBRE_EXACTO


@pytest.mark.asyncio
async def test_cancelar_dueno_en_espera_libera_turno(monkeypatch):
    nueva_cuota = modulo_bridge.CUOTA
    nueva_cuota.agotada("agotamiento dueno")
    estado_pausa = dict(nueva_cuota.estado())
    assert estado_pausa["agotada"] is True

    dueno_esperando = asyncio.Event()
    despertar_dueno = asyncio.Event()
    tarea_dueno = None

    orig_esperar_turno = nueva_cuota.esperar_turno

    async def dormir_controlado(segundos):
        actual = asyncio.current_task()
        if tarea_dueno is not None and actual is tarea_dueno:
            dueno_esperando.set()
        await despertar_dueno.wait()
        await asyncio.sleep(0)

    async def esperar_turno_con_dormir(dormir=None):
        return await orig_esperar_turno(dormir=dormir_controlado if dormir is None else dormir)

    monkeypatch.setattr(nueva_cuota, "esperar_turno", esperar_turno_con_dormir)

    ejecutadas = []

    async def fake_ejecutar(self, prompt, *args, **kwargs):
        ejecutadas.append(prompt)
        return {"ok": prompt}

    monkeypatch.setattr(AntigravityBridge, "_ejecutar_prompt", fake_ejecutar)
    bridge = AntigravityBridge()

    async with scope_tareas() as lanzar:
        tarea_dueno = lanzar(bridge.run_prompt("dueno"))
        await asyncio.wait_for(dueno_esperando.wait(), timeout=30)

        # El dueño está durmiendo esperando reintento; ninguna ejecución física
        assert len(ejecutadas) == 0

        # Cancelar al dueño en espera
        tarea_dueno.cancel()
        with pytest.raises(asyncio.CancelledError):
            await tarea_dueno

        # Cancelar al dueño no borra la pausa
        assert nueva_cuota.estado() == estado_pausa

        # El sucesor puede recuperar (el turno quedó liberado)
        nueva_cuota.reintentar_ya()
        despertar_dueno.set()
        tarea_sucesor = lanzar(bridge.run_prompt("sucesor"))
        res_sucesor = await asyncio.wait_for(tarea_sucesor, timeout=30)
        assert res_sucesor == {"ok": "sucesor"}
        assert ejecutadas == ["sucesor"]
        assert nueva_cuota.estado() == ESTADO_LIBRE_EXACTO


@pytest.mark.asyncio
async def test_cancelar_seguidor_no_libera_turno_ajeno(monkeypatch):
    nueva_cuota = modulo_bridge.CUOTA
    nueva_cuota.agotada("agotamiento previo")
    nueva_cuota.reintentar_ya()

    dueno_bloqueado = asyncio.Event()
    dueno_puede_terminar = asyncio.Event()
    seguidor_esperando = asyncio.Event()
    tercero_bloqueado = asyncio.Event()
    despertar_bloqueados = asyncio.Event()
    tarea_seguidor = None
    tarea_tercero = None

    orig_esperar_turno = nueva_cuota.esperar_turno

    async def dormir_controlado(segundos):
        actual = asyncio.current_task()
        if tarea_seguidor is not None and actual is tarea_seguidor:
            seguidor_esperando.set()
        elif tarea_tercero is not None and actual is tarea_tercero:
            tercero_bloqueado.set()
        await despertar_bloqueados.wait()
        await asyncio.sleep(0)

    async def esperar_turno_con_dormir(dormir=None):
        return await orig_esperar_turno(dormir=dormir_controlado if dormir is None else dormir)

    monkeypatch.setattr(nueva_cuota, "esperar_turno", esperar_turno_con_dormir)

    llamadas = []

    async def fake_ejecutar(self, prompt, *args, **kwargs):
        llamadas.append(prompt)
        if prompt == "dueno":
            dueno_bloqueado.set()
            await dueno_puede_terminar.wait()
            return {"ok": "dueno"}
        return {"ok": prompt}

    monkeypatch.setattr(AntigravityBridge, "_ejecutar_prompt", fake_ejecutar)
    bridge = AntigravityBridge()

    async with scope_tareas() as lanzar:
        tarea_dueno = lanzar(bridge.run_prompt("dueno"))
        await asyncio.wait_for(dueno_bloqueado.wait(), timeout=30)
        assert llamadas == ["dueno"]

        # Seguidor entra en espera confirmada por evento mientras el dueño tiene el turno
        tarea_seguidor = lanzar(bridge.run_prompt("seguidor"))
        await asyncio.wait_for(seguidor_esperando.wait(), timeout=30)

        # Cancelar seguidor
        tarea_seguidor.cancel()
        with pytest.raises(asyncio.CancelledError):
            await tarea_seguidor

        # Tercera llamada intentando entrar: queda bloqueada confirmada por evento
        tarea_tercero = lanzar(bridge.run_prompt("tercero"))
        await asyncio.wait_for(tercero_bloqueado.wait(), timeout=30)
        assert llamadas == ["dueno"]
        assert not tarea_tercero.done()

        # El dueño finaliza y libera su turno
        dueno_puede_terminar.set()
        res_dueno = await asyncio.wait_for(tarea_dueno, timeout=30)
        assert res_dueno == {"ok": "dueno"}

        # Ahora el tercero puede ejecutar
        despertar_bloqueados.set()
        res_tercero = await asyncio.wait_for(tarea_tercero, timeout=30)
        assert res_tercero == {"ok": "tercero"}
        assert llamadas == ["dueno", "tercero"]
        assert nueva_cuota.estado() == ESTADO_LIBRE_EXACTO


@pytest.mark.asyncio
async def test_cancelar_sondeo_en_vuelo_libera_turno(monkeypatch):
    nueva_cuota = modulo_bridge.CUOTA
    nueva_cuota.agotada("cuota para sondeo cancelable")
    nueva_cuota.reintentar_ya()

    sondeo_en_vuelo = asyncio.Event()
    bloqueo_sondeo = asyncio.Event()

    async def fake_ejecutar(self, prompt, *args, **kwargs):
        if prompt == "sondeo":
            sondeo_en_vuelo.set()
            await bloqueo_sondeo.wait()
        return {"ok": prompt}

    monkeypatch.setattr(AntigravityBridge, "_ejecutar_prompt", fake_ejecutar)
    bridge = AntigravityBridge()

    async with scope_tareas() as lanzar:
        tarea_sondeo = lanzar(bridge.run_prompt("sondeo"))
        await asyncio.wait_for(sondeo_en_vuelo.wait(), timeout=30)

        # Capturar estado() antes de cancelar
        estado_antes_cancelar = dict(nueva_cuota.estado())
        assert estado_antes_cancelar["agotada"] is True

        # Cancelar el sondeo en vuelo
        tarea_sondeo.cancel()
        with pytest.raises(asyncio.CancelledError):
            await tarea_sondeo

        # Exigir igualdad inmediatamente después, antes de reintentar_ya()
        assert nueva_cuota.estado() == estado_antes_cancelar

        # Otro intento toma el turno y recupera exitosamente
        nueva_cuota.reintentar_ya()
        tarea_otro = lanzar(bridge.run_prompt("otro"))
        res_otro = await asyncio.wait_for(tarea_otro, timeout=30)
        assert res_otro == {"ok": "otro"}
        assert nueva_cuota.estado() == ESTADO_LIBRE_EXACTO


@pytest.mark.asyncio
async def test_reset_sin_argumentos_sigue_compatible(monkeypatch):
    nueva_cuota = modulo_bridge.CUOTA
    nueva_cuota.agotada("cuota administrativa")
    assert nueva_cuota.estado()["agotada"] is True

    # Reset administrativo sin argumentos
    nueva_cuota.restablecida()

    assert nueva_cuota.estado() == ESTADO_LIBRE_EXACTO

    # Las siguientes llamadas no quedan en espera (acotado por timeout)
    monkeypatch.setattr(AntigravityBridge, "_ejecutar_prompt", AsyncMock(return_value={"ok": "inmediato"}))
    async with scope_tareas() as lanzar:
        tarea_directo = lanzar(AntigravityBridge().run_prompt("directo"))
        res = await asyncio.wait_for(tarea_directo, timeout=30)
        assert res == {"ok": "inmediato"}
