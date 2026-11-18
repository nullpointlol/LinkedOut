"""El límite de llamadas a AGY a la vez es de todo el proceso, no de cada event loop (12/11).

Con el Radar en su propio hilo y su propio event loop, el portal y el Radar piden AGY desde dos loops.
El tope sigue siendo 2 entre los dos, y 1 con el modo serie (manual o por saturación). Sin AGY real.
"""
import asyncio
import threading
import time

import pytest

from core import bridge, radar_store
from core.bridge import AntigravityBridge

GUARDA = 60
POR_LOOP = 3


class _Medidor:
    """Cuenta las llamadas que están ejecutando a la vez, sumando todos los hilos."""

    def __init__(self):
        self.lock = threading.Lock()
        self.activas = 0
        self.pico = 0
        self.hechas = 0
        self.soltar = threading.Event()

    async def ejecutar(self, bridge_self, prompt, *a, **kw):
        with self.lock:
            self.activas += 1
            self.pico = max(self.pico, self.activas)
        try:
            limite = time.monotonic() + GUARDA
            while not self.soltar.is_set() and time.monotonic() < limite:
                await asyncio.sleep(0.01)
            return {"ok": prompt}
        finally:
            with self.lock:
                self.activas -= 1
                self.hechas += 1


def _en_dos_loops(medidor: _Medidor, esperar_pico: int) -> None:
    """Dos hilos, cada uno con su event loop, piden POR_LOOP llamadas a la vez; se suelta cuando el pico
    llega a `esperar_pico` (o pasa un rato sin subir) y se espera a que terminen todas."""
    errores = []

    def hilo(nombre):
        loop = asyncio.new_event_loop()
        try:
            async def pedir():
                b = AntigravityBridge()
                await asyncio.wait_for(asyncio.gather(*(b.run_prompt(f"{nombre}-{i}") for i in range(POR_LOOP))),
                                       timeout=GUARDA)
            loop.run_until_complete(pedir())
        except BaseException as e:  # noqa: BLE001 - se reporta en el hilo principal
            errores.append(e)
        finally:
            loop.close()

    hilos = [threading.Thread(target=hilo, args=(n,), daemon=True) for n in ("portal", "radar")]
    for h in hilos:
        h.start()
    limite = time.monotonic() + GUARDA
    while medidor.pico < esperar_pico and time.monotonic() < limite:
        time.sleep(0.01)
    time.sleep(0.5)  # si el tope estuviera roto, en este rato entrarían más
    medidor.soltar.set()
    for h in hilos:
        h.join(GUARDA)
    assert not errores, errores
    assert medidor.hechas == 2 * POR_LOOP


@pytest.fixture
def medidor(monkeypatch):
    m = _Medidor()
    monkeypatch.setattr(AntigravityBridge, "_ejecutar_prompt", m.ejecutar)
    monkeypatch.setattr(bridge, "_capacidad_agy", lambda: 2)
    radar_store.update_radar_state(modo_serie=False, agy_serie_hasta=None)
    return m


def test_dos_loops_nunca_pasan_de_dos_llamadas_a_la_vez(medidor):
    _en_dos_loops(medidor, esperar_pico=2)
    assert medidor.pico == 2


def test_modo_serie_deja_una_sola_llamada_entre_los_dos_loops(medidor):
    radar_store.update_radar_state(modo_serie=True)
    _en_dos_loops(medidor, esperar_pico=1)
    assert medidor.pico == 1


def test_saturacion_deja_una_sola_llamada_entre_los_dos_loops(medidor):
    radar_store.update_radar_state(agy_serie_hasta=time.time() + 600)  # modo serie automático por 503
    _en_dos_loops(medidor, esperar_pico=1)
    assert medidor.pico == 1
