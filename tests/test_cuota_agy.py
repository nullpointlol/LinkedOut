"""Sin cuota de AGY nada falla ni se cancela: las llamadas esperan y una reintenta cada tanto."""
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from core import bridge as modulo_bridge
from core.bridge import CUOTA, AntigravityBridge, CuotaAgotada, texto_de_error


@pytest.fixture
def cuota_rapida(monkeypatch):
    ahora = [1_800_000_000.0]
    monkeypatch.setattr(modulo_bridge, "time", SimpleNamespace(time=lambda: ahora[0]))
    original = CUOTA.esperar_turno

    async def dormir(segundos):
        ahora[0] += segundos
        await asyncio.sleep(0)  # Ceder el control; no sincroniza por tiempo real.

    async def esperar_turno():
        return await original(dormir=dormir)

    monkeypatch.setattr(CUOTA, "esperar_turno", esperar_turno)


@pytest.fixture
def cuota_controlada(monkeypatch):
    """Cada espera anuncia su entrada y solo avanza cuando el test la libera."""
    ahora = [1_800_000_000.0]
    monkeypatch.setattr(modulo_bridge, "time", SimpleNamespace(time=lambda: ahora[0]))
    entradas = asyncio.Queue()
    pendientes = []
    original = CUOTA.esperar_turno

    async def dormir(segundos):
        futuro = asyncio.get_running_loop().create_future()
        pendientes.append(futuro)
        entradas.put_nowait(futuro)
        await futuro

    async def esperar_turno():
        return await original(dormir=dormir)

    async def esperar(cantidad=1):
        for _ in range(cantidad):
            await asyncio.wait_for(entradas.get(), 30)

    def despertar():
        for futuro in pendientes:
            if not futuro.done():
                futuro.set_result(None)
        pendientes.clear()

    def vencer_intervalo():
        ahora[0] = CUOTA.reanuda_en
        despertar()

    monkeypatch.setattr(CUOTA, "esperar_turno", esperar_turno)
    return SimpleNamespace(esperar=esperar, despertar=despertar, vencer=vencer_intervalo)


@pytest.mark.parametrize("stdout, stderr, codigo, cuota", [
    ('{"status": "ERROR", "error": "RESOURCE_EXHAUSTED: quota exceeded"}', "", 0, True),
    ("", "Error 429: Too Many Requests", 1, True),
    ("You have reached your usage limit. Try again later.", "", 0, True),
    ('{"status": "ERROR", "error": "internal"}', "", 0, False),
    ('{"status": "SUCCESS", "response": "{\\"cuota\\": \\"rate limit de la API\\"}"}', "", 0, None),
    ('{"response": "CV con quota"}', "warning", 1, False),
])
def test_texto_de_error_and_quota_detection(stdout, stderr, codigo, cuota):
    texto = texto_de_error(stdout, stderr, codigo)
    if cuota is None:
        assert texto == ""  # una respuesta exitosa nunca cuenta, aunque mencione límites
    else:
        assert bool(modulo_bridge._RE_CUOTA.search(texto)) is cuota


def _proceso(stdout: bytes, codigo: int = 0):
    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(stdout, b""))
    proc.returncode = codigo
    return proc


@pytest.mark.asyncio
async def test_run_prompt_waits_without_quota_and_repeats_the_same_call(monkeypatch, cuota_rapida):
    salidas = [b'{"status": "ERROR", "error": "RESOURCE_EXHAUSTED: quota exceeded"}',
               b'{"status": "SUCCESS", "response": "{\\"ok\\": 1}"}']
    prompts = []

    async def fake_exec(*cmd, **kw):
        prompts.append(cmd)
        return _proceso(salidas.pop(0))

    monkeypatch.setattr("core.bridge._crear_proceso", fake_exec)
    bridge = AntigravityBridge()
    monkeypatch.setattr(bridge, "agy_path", Path(__file__))
    assert await bridge.run_prompt("hola") == {"ok": 1}
    assert len(prompts) == 2 and prompts[0] == prompts[1]
    assert CUOTA.estado()["agotada"] is False


@pytest.mark.asyncio
async def test_only_one_call_retries_while_the_others_wait(monkeypatch, cuota_controlada):
    llamadas = []
    estado = {"cuota": False}
    recuperada = asyncio.Event()

    async def ejecutar(self, prompt, *a):
        llamadas.append(prompt)
        await asyncio.sleep(0)  # Mantener las llamadas iniciales concurrentes.
        if not estado["cuota"]:
            raise CuotaAgotada("quota exceeded")
        recuperada.set()
        return {"prompt": prompt}

    monkeypatch.setattr(AntigravityBridge, "_ejecutar_prompt", ejecutar)
    b = AntigravityBridge()
    tareas = [asyncio.create_task(b.run_prompt(f"p{i}")) for i in range(3)]
    try:
        await cuota_controlada.esperar(3)
        assert CUOTA.estado()["agotada"] is True and CUOTA.estado()["reintento"]
        antes = len(llamadas)
        cuota_controlada.vencer()
        await cuota_controlada.esperar(3)
        # Todos volvieron a esperar: hubo exactamente un reintento en el intervalo.
        assert len(llamadas) - antes == 1
        estado["cuota"] = True
        cuota_controlada.vencer()
        await asyncio.wait_for(recuperada.wait(), 30)
        cuota_controlada.despertar()  # Los seguidores observan la recuperación del dueño.
        resultados = await asyncio.wait_for(asyncio.gather(*tareas), 30)
        assert sorted(r["prompt"] for r in resultados) == ["p0", "p1", "p2"]
        assert CUOTA.estado() == {"agotada": False, "desde": None, "reintento": None, "renueva": None, "mensaje": ""}
    finally:
        for tarea in tareas:
            tarea.cancel()
        await asyncio.gather(*tareas, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancelled_waiting_call_releases_the_turn(monkeypatch, cuota_controlada):
    CUOTA.agotada("quota exceeded")
    b = AntigravityBridge()
    monkeypatch.setattr(AntigravityBridge, "_ejecutar_prompt", AsyncMock(return_value={"ok": 1}))
    tarea = asyncio.create_task(b.run_prompt("x"))
    try:
        await cuota_controlada.esperar()
        tarea.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(tarea, 30)
        # Otra llamada toma el turno y, con cuota, sigue.
        CUOTA.reintentar_ya()
        assert await asyncio.wait_for(b.run_prompt("y"), 30) == {"ok": 1}
    finally:
        tarea.cancel()
        await asyncio.gather(tarea, return_exceptions=True)


@pytest.mark.asyncio
async def test_other_errors_still_fail_as_before(monkeypatch):
    monkeypatch.setattr(AntigravityBridge, "_ejecutar_prompt", AsyncMock(side_effect=TimeoutError("600s")))
    with pytest.raises(TimeoutError):
        await AntigravityBridge().run_prompt("x")
    assert CUOTA.estado()["agotada"] is False


def test_status_reports_quota_state():
    from fastapi.testclient import TestClient
    import server
    CUOTA.agotada("quota exceeded")
    r = TestClient(server.app).get("/api/radar/status")
    assert r.status_code == 200 and r.json()["agy_cuota"]["agotada"] is True
    CUOTA.restablecida()


def test_reset_time_is_read_from_the_agy_message(monkeypatch):
    ahora = 1_800_000_000.0
    monkeypatch.setattr(modulo_bridge, "time", SimpleNamespace(time=lambda: ahora))
    CUOTA.agotada("Individual quota reached. Please upgrade your subscription. Resets in 100h18m14s.")
    estado = CUOTA.estado()
    assert estado["agotada"] and estado["renueva"]
    assert abs(CUOTA.renueva_en - ahora - 361094) < 5
    CUOTA.restablecida()
    assert CUOTA.estado()["renueva"] is None


@pytest.mark.asyncio
async def test_retry_now_wakes_the_waiting_call_and_clears_the_banner(monkeypatch, cuota_controlada):
    CUOTA.agotada("quota exceeded")  # próximo intento en 15 minutos
    llamadas = []

    async def ejecutar(self, prompt, *a):
        llamadas.append(prompt)
        return {"ok": True}

    monkeypatch.setattr(AntigravityBridge, "_ejecutar_prompt", ejecutar)
    en_espera = asyncio.create_task(AntigravityBridge().run_prompt("CV"))
    sondeo = None
    try:
        await cuota_controlada.esperar()
        assert llamadas == []
        sondeo = asyncio.create_task(modulo_bridge.sondear_cuota())
        await cuota_controlada.esperar()
        cuota_controlada.despertar()
        estado = await asyncio.wait_for(sondeo, 30)
        assert estado["agotada"] is False
        assert await asyncio.wait_for(en_espera, 30) == {"ok": True}
        assert sorted(llamadas) == sorted(["CV", modulo_bridge.PROMPT_SONDEO])
    finally:
        tareas = [t for t in (en_espera, sondeo) if t is not None]
        for tarea in tareas:
            tarea.cancel()
        await asyncio.gather(*tareas, return_exceptions=True)


def test_retry_now_endpoint(monkeypatch):
    from fastapi.testclient import TestClient
    import server

    async def sondeo():
        return {"agotada": False}

    monkeypatch.setattr("core.radar_api.sondear_cuota", sondeo)
    r = TestClient(server.app).post("/api/radar/agy/reintentar")
    assert r.status_code == 200 and r.json() == {"agotada": False}


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    '{"status": "ERROR", "error": "authentication failed or timed out"}',
    '{"status": "ERROR", "error": "dial tcp: lookup api.example: no such host"}',
    '{"status": "ERROR", "error": "invalid model selection (--model \\"m\\" --effort \\"low\\")"}',
])
async def test_sin_servicio_espera_como_sin_cuota(monkeypatch, cuota_rapida, error):
    salidas = [error.encode(), b'{"status": "SUCCESS", "response": "{\\"ok\\": 1}"}']

    async def fake_exec(*cmd, **kw):
        return _proceso(salidas.pop(0))

    monkeypatch.setattr("core.bridge._crear_proceso", fake_exec)
    bridge = AntigravityBridge()
    monkeypatch.setattr(bridge, "agy_path", Path(__file__))
    assert await bridge.run_prompt("hola") == {"ok": 1}
    assert salidas == [] and CUOTA.estado()["agotada"] is False
