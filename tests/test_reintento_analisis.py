"""Un análisis que falló por la IA (timeout, 503, respuesta inválida) no deja el post en ERROR para siempre
(producción 12/11: el post 6892 quedó en ERROR tras dos timeouts de AGY de 420 s).

Regla, con constantes a la vista en `core/radar.py`:
- `ESPERA_REINTENTO_AUTOMATICO_MIN` (30): minutos en ERROR antes de volver solo a OFERTA;
- `MAX_REINTENTOS_AUTOMATICOS` (2): veces que vuelve solo; después queda en ERROR y lo reintenta el
  botón Analizar.
No vuelve con el análisis en pausa, sin cuota de AGY ni con AGY saturado. Sin red ni AGY reales.
"""
import time
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, Mock

import pytest

import server
from core import bridge, radar, radar_store
from core.radar import ESPERA_REINTENTO_AUTOMATICO_MIN, MAX_REINTENTOS_AUTOMATICOS, RadarLoop
from core.strategist import StrategistAgent

ERROR_TIMEOUT = ("Falló el análisis (3/3): el modelo no respondió un análisis válido "
                 "(La llamada a Antigravity CLI excedió el tiempo límite de 420s.)")


@pytest.fixture(autouse=True)
def cuota_sana():
    bridge.CUOTA.restablecida()
    yield
    bridge.CUOTA.restablecida()


def _motor(monkeypatch, minutos_despues: float) -> RadarLoop:
    """Radar con el reloj corrido `minutos_despues` respecto de ahora (updated_at lo pone la base)."""
    ahora = datetime.now() + timedelta(minutes=minutos_despues)
    loop = RadarLoop(harvester_factory=Mock(), triage_fn=AsyncMock(), analyze_fn=AsyncMock(),
                     pipeline_fn=AsyncMock(), compose_fn=AsyncMock(), send_fn=AsyncMock(), broadcast=AsyncMock(),
                     now=lambda: ahora)
    monkeypatch.setattr(loop, "_wake_processing", Mock())
    return loop


def _post_en_error(error: str = ERROR_TIMEOUT, n: int = 1, **campos) -> int:
    pid = radar_store.insert_post_if_new({"urn": f"urn:li:activity:95{n:04d}", "url": f"https://example.org/{n}",
                                          "autor": "Acme", "texto": f"Buscamos analista {n} en Acme.",
                                          "emails": ["rrhh@acme.test"]}, "consulta de prueba")
    radar_store.update_post(pid, estado="ERROR", intentos=3, error=error, **campos)
    return pid


def test_las_reglas_son_constantes_con_nombre():
    assert ESPERA_REINTENTO_AUTOMATICO_MIN == 30
    assert MAX_REINTENTOS_AUTOMATICOS == 2


@pytest.mark.asyncio
async def test_un_post_en_error_por_la_ia_vuelve_a_oferta_a_los_30_min(monkeypatch):
    pid = _post_en_error()
    assert await _motor(monkeypatch, ESPERA_REINTENTO_AUTOMATICO_MIN - 1).reintentar_analisis_fallidos() == 0
    assert radar_store.get_post(pid)["estado"] == "ERROR"

    loop = _motor(monkeypatch, ESPERA_REINTENTO_AUTOMATICO_MIN + 1)
    assert await loop.reintentar_analisis_fallidos() == 1
    post = radar_store.get_post(pid)
    assert post["estado"] == "OFERTA" and post["intentos"] == 0 and post["reintentos_auto"] == 1
    assert "Reintento automático 1/2" in post["error"] and "420s" in post["error"]
    loop._wake_processing.assert_called_once()


@pytest.mark.asyncio
async def test_no_vuelve_con_el_analisis_en_pausa(monkeypatch):
    pid = _post_en_error()
    radar_store.update_radar_state(analisis_pausado=True)
    assert await _motor(monkeypatch, 60).reintentar_analisis_fallidos() == 0
    assert radar_store.get_post(pid)["estado"] == "ERROR"


@pytest.mark.asyncio
async def test_no_vuelve_sin_cuota_de_agy(monkeypatch):
    pid = _post_en_error()
    bridge.CUOTA.agotada("Individual quota reached. Resets in 2h0m0s.")
    assert await _motor(monkeypatch, 60).reintentar_analisis_fallidos() == 0
    assert radar_store.get_post(pid)["estado"] == "ERROR"


@pytest.mark.asyncio
async def test_no_vuelve_con_agy_saturado(monkeypatch):
    pid = _post_en_error()
    radar_store.update_radar_state(agy_serie_hasta=time.time() + 600)  # modo serie por 503
    assert await _motor(monkeypatch, 60).reintentar_analisis_fallidos() == 0
    assert radar_store.get_post(pid)["estado"] == "ERROR"


@pytest.mark.asyncio
async def test_no_vuelve_una_tercera_vez(monkeypatch):
    pid = _post_en_error()
    for vuelta in range(1, MAX_REINTENTOS_AUTOMATICOS + 1):
        assert await _motor(monkeypatch, 31 * vuelta).reintentar_analisis_fallidos() == 1
        assert radar_store.get_post(pid)["reintentos_auto"] == vuelta
        radar_store.update_post(pid, estado="ERROR", intentos=3, error=ERROR_TIMEOUT)  # vuelve a fallar
    assert await _motor(monkeypatch, 31 * (MAX_REINTENTOS_AUTOMATICOS + 1)).reintentar_analisis_fallidos() == 0
    post = radar_store.get_post(pid)
    assert post["estado"] == "ERROR" and post["reintentos_auto"] == MAX_REINTENTOS_AUTOMATICOS


@pytest.mark.asyncio
async def test_solo_toca_los_errores_del_analisis(monkeypatch):
    otros = [_post_en_error("Captura incompleta: falta texto suficiente o un puesto detectado", n=2),
             _post_en_error("Falló la generación: El Redactor no generó el CV", n=3)]
    assert await _motor(monkeypatch, 60).reintentar_analisis_fallidos() == 0
    assert [radar_store.get_post(p)["estado"] for p in otros] == ["ERROR", "ERROR"]


@pytest.mark.asyncio
async def test_el_ciclo_del_radar_hace_el_barrido(monkeypatch):
    loop = _motor(monkeypatch, 0)
    monkeypatch.setattr(loop, "run_cycle", AsyncMock())
    monkeypatch.setattr(loop, "_rescate_si_toca", AsyncMock())
    monkeypatch.setattr(loop, "_entrar_en_pausa", AsyncMock())
    monkeypatch.setattr(loop, "_wait", AsyncMock(return_value=False))
    monkeypatch.setattr(loop, "reintentar_analisis_fallidos", AsyncMock(return_value=0))
    await loop._harvest_loop()
    loop.reintentar_analisis_fallidos.assert_awaited_once()


@pytest.mark.asyncio
async def test_el_error_del_analisis_conserva_la_causa(monkeypatch):
    """La causa (timeout de AGY) queda en el error del post en vez de perderse en el fallback."""
    async def timeout(self, prompt, **kw):
        raise TimeoutError("La llamada a Antigravity CLI excedió el tiempo límite de 420s.")

    monkeypatch.setattr(radar, "_ticket", Mock())
    monkeypatch.setattr(server.AntigravityBridge, "run_prompt", timeout)
    with pytest.raises(RuntimeError, match=r"no respondió un análisis válido \(La llamada a Antigravity CLI "
                                           r"excedió el tiempo límite de 420s\.\)"):
        await server._radar_analyze({"texto": "Buscamos analista en Acme", "links_leidos": []})


@pytest.mark.asyncio
async def test_la_falla_del_estratega_lleva_la_causa(monkeypatch, tmp_path):
    import core.aprendizajes as ap
    monkeypatch.setattr(ap, "APRENDIZAJES_FILE", tmp_path / "aprendizajes.json")
    agente = StrategistAgent(bridge=Mock(effort="low", run_prompt=AsyncMock(side_effect=TimeoutError("420s"))),
                             expander=Mock(get_expanded_competencies=Mock(return_value=[])))
    with pytest.raises(RuntimeError, match=r"no respondió un análisis válido \(420s\)"):
        await agente.analyze_offer("Analista\nAcme\n• Python")
