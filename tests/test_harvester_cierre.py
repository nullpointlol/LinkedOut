"""Cerrar el navegador nunca frena el event loop y tiene un plazo (producción 12/11, 14:54–15:04: el portal
dejó de responder 6 min con la CPU en 0; `close()` esperaba al hilo de Playwright con
`executor.shutdown(wait=True)` adentro del loop). Sin Playwright real: el cierre lento se simula en el hilo
del harvester."""
import asyncio
import threading
import time

import pytest

from core import linkedin_harvester
from core.linkedin_harvester import LinkedInHarvester

CIERRE_COLGADO_S = 6.0


class _EdgeQueNoCierra(LinkedInHarvester):
    """`context.close()` de un Edge colgado: el hilo de Playwright tarda CIERRE_COLGADO_S en volver."""

    def __init__(self, tmp_path):
        super().__init__(profile_dir=tmp_path / "perfil")
        self.cierres = 0
        self.liberar = threading.Event()

    def _close_sync(self):
        self.cierres += 1
        self.liberar.wait(CIERRE_COLGADO_S)


async def _latidos(atrasos):
    while True:
        inicio = time.perf_counter()
        await asyncio.sleep(0.05)
        atrasos.append(time.perf_counter() - inicio - 0.05)


@pytest.mark.asyncio
async def test_cerrar_un_edge_colgado_termina_en_el_plazo_sin_frenar_el_loop(monkeypatch, tmp_path):
    monkeypatch.setattr(linkedin_harvester, "PLAZO_CIERRE_NAVEGADOR_S", 1.0)
    h = _EdgeQueNoCierra(tmp_path)
    h._get_executor()  # navegador «abierto»
    atrasos = []
    latido = asyncio.create_task(_latidos(atrasos))
    inicio = time.monotonic()
    try:
        await h.close()
    finally:
        duracion = time.monotonic() - inicio
        latido.cancel()
        h.liberar.set()
    assert duracion < 3.0, f"el cierre esperó {duracion:.1f} s (plazo 1 s)"
    assert max(atrasos) < 0.2, max(atrasos)
    assert h.cierres == 1 and h._executor is None


@pytest.mark.asyncio
async def test_cancelar_el_cierre_no_frena_el_loop(tmp_path):
    h = _EdgeQueNoCierra(tmp_path)
    h._get_executor()
    atrasos = []
    latido = asyncio.create_task(_latidos(atrasos))
    cierre = asyncio.create_task(h.close())
    try:
        await asyncio.sleep(0.3)
        cierre.cancel()  # como un stop que cancela la vuelta del Radar mientras cambia de cuenta
        with pytest.raises(asyncio.CancelledError):
            await cierre
        await asyncio.sleep(0.5)
    finally:
        latido.cancel()
        h.liberar.set()
    assert max(atrasos) < 0.2, f"el loop quedó frenado {max(atrasos):.1f} s esperando a Playwright"


@pytest.mark.asyncio
async def test_un_cierre_normal_espera_a_que_edge_cierre(tmp_path):
    h = _EdgeQueNoCierra(tmp_path)
    h.liberar.set()  # Edge sano: cierra enseguida
    h._get_executor()
    await h.close()
    assert h.cierres == 1 and h._executor is None
    await h.close()  # idempotente
    assert h.cierres == 1


def test_el_plazo_es_una_constante_con_nombre():
    assert linkedin_harvester.PLAZO_CIERRE_NAVEGADOR_S == 20
