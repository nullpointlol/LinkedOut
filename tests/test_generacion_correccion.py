"""Generación del Radar sin ERROR evitable (14/11: 25 generaciones fallaron entre el 13 y el 14/11).

1. Rechazo de la revisión de calidad (clichés, impacto vago, ATS bajo el piso, mail largo, keywords faltantes): antes
   de pasar a ERROR, una corrección automática con lo que ya hace «remediar» (`remediate_cv_task`: el auditor reescribe
   con sus directivas). Si la corrección aprueba, sigue; si no, ERROR con el motivo. Las reglas de calidad no cambian.
2. Fallas de AGY (timeout del Redactor, «Fallo al ejecutar agy CLI», 503): el post vuelve solo a la cola de generación
   pasados `ESPERA_REINTENTO_GENERACION_MIN`, hasta `MAX_REINTENTOS_GENERACION_AUTO` veces; nunca con la generación en
   pausa, sin cuota ni con AGY saturado.
Sin AGY ni mails reales."""
import json
import time
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, Mock

import pytest

import server
from core import bridge, database, radar_store
from core.radar import (CORRECCIONES_DE_CALIDAD, ESPERA_REINTENTO_GENERACION_MIN, MAX_REINTENTOS_GENERACION_AUTO,
                        RadarLoop)

RECHAZO = {"approved": False, "score_ats": 75, "hallazgos_unificados": {
    "lista_observaciones": ["Remover clichés y muletillas de IA detectadas: impacto vago"], "requiere_accion": True}}
APROBADO = {"approved": True, "score_ats": 92}


def _post_generando(n=1):
    pid = radar_store.insert_post_if_new({"urn": f"urn:li:activity:96{n:03d}", "url": f"https://example.org/{n}",
                                          "autor": "Acme", "texto": f"Buscamos QA {n} en Acme.",
                                          "emails": ["rrhh@acme.test"]}, "consulta de prueba")
    radar_store.update_post(pid, estado="GENERANDO", analisis={
        "puesto": "QA", "empresa": "Acme", "requisitos": [{"nombre": "Python", "estado": "MATCH_DIRECTO"}]})
    return pid


def _terminar(postulacion_id, status, reporte):
    database.update_postulacion(postulacion_id, status=status, cv_markdown="# ALEX RIVERA\nQA\n",
                                reporte_auditoria=reporte, telemetry={"status": status})


# ---------------------------------------------------------------- 1. corrección ante la revisión de calidad

@pytest.mark.asyncio
async def test_un_rechazo_de_calidad_se_corrige_una_vez_y_sigue(monkeypatch):
    pid = _post_generando()

    async def recalculo(postulacion_id):
        _terminar(postulacion_id, "ERROR", RECHAZO)

    correcciones = []

    async def remediar(postulacion_id):
        correcciones.append(postulacion_id)
        _terminar(postulacion_id, "READY", APROBADO)

    monkeypatch.setattr(server, "recalculate_cv_task", recalculo)
    monkeypatch.setattr(server, "remediate_cv_task", remediar)
    postulacion_id = await server._radar_pipeline(radar_store.get_post(pid))
    assert correcciones == [postulacion_id]
    assert database.get_postulacion(postulacion_id)["status"] == "READY"
    assert radar_store.get_post(pid)["postulacion_id"] == postulacion_id


@pytest.mark.asyncio
async def test_si_la_correccion_no_aprueba_va_a_error_con_el_motivo(monkeypatch):
    pid = _post_generando()

    async def recalculo(postulacion_id):
        _terminar(postulacion_id, "ERROR", RECHAZO)

    correcciones = []

    async def remediar(postulacion_id):
        correcciones.append(postulacion_id)
        _terminar(postulacion_id, "ERROR", RECHAZO)

    monkeypatch.setattr(server, "recalculate_cv_task", recalculo)
    monkeypatch.setattr(server, "remediate_cv_task", remediar)
    with pytest.raises(RuntimeError, match="Revisá el CV.*corrección automática.*impacto vago"):
        await server._radar_pipeline(radar_store.get_post(pid))
    assert len(correcciones) == CORRECCIONES_DE_CALIDAD == 1  # una sola pasada, las reglas no cambian
    assert radar_store.get_post(pid)["postulacion_id"] is None


@pytest.mark.asyncio
async def test_una_falla_que_no_es_de_calidad_no_se_corrige(monkeypatch):
    pid = _post_generando()

    async def recalculo(postulacion_id):
        database.update_postulacion(postulacion_id, status="ERROR", telemetry={"status": "ERROR"})

    remediar = AsyncMock()
    monkeypatch.setattr(server, "recalculate_cv_task", recalculo)
    monkeypatch.setattr(server, "remediate_cv_task", remediar)
    with pytest.raises(RuntimeError, match="terminó en estado ERROR"):
        await server._radar_pipeline(radar_store.get_post(pid))
    remediar.assert_not_awaited()


# ---------------------------------------------------------------- 2. reintento por fallas de AGY

ERRORES_AGY = [
    "Falló la generación: El Redactor no generó el CV (La llamada a Antigravity CLI excedió el tiempo límite de 600s.): reintentá",
    "Falló la generación: El Redactor no generó el CV (Fallo al ejecutar agy CLI (código 1): ): reintentá",
    "Generación detenida por saturación AGY (503): 503 Service Unavailable",
    "Falló la generación: superó 20 min de trabajo (sin contar la espera de cuota) dos veces",
]


@pytest.fixture(autouse=True)
def cuota_sana():
    bridge.CUOTA.restablecida()
    yield
    bridge.CUOTA.restablecida()


def _motor(monkeypatch, minutos_despues):
    ahora = datetime.now() + timedelta(minutes=minutos_despues)
    loop = RadarLoop(harvester_factory=Mock(), triage_fn=AsyncMock(), analyze_fn=AsyncMock(), pipeline_fn=AsyncMock(),
                     compose_fn=AsyncMock(), send_fn=AsyncMock(), broadcast=AsyncMock(), now=lambda: ahora)
    monkeypatch.setattr(loop, "wake_generation", AsyncMock())
    return loop


def _post_en_error(error, n=1):
    pid = _post_generando(n)
    radar_store.update_post(pid, estado="ERROR", intentos=2, error=error)
    return pid


def test_las_reglas_son_constantes_con_nombre():
    assert ESPERA_REINTENTO_GENERACION_MIN == 30 and MAX_REINTENTOS_GENERACION_AUTO == 2
    assert CORRECCIONES_DE_CALIDAD == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("error", ERRORES_AGY, ids=["timeout", "codigo-1", "503", "plazo"])
async def test_una_falla_de_agy_vuelve_a_la_cola_a_los_30_min(monkeypatch, error):
    pid = _post_en_error(error)
    radar_store.update_radar_state(generacion_pausada=False)
    assert await _motor(monkeypatch, ESPERA_REINTENTO_GENERACION_MIN - 1).reintentar_generacion_fallida() == 0
    assert radar_store.get_post(pid)["estado"] == "ERROR"
    loop = _motor(monkeypatch, ESPERA_REINTENTO_GENERACION_MIN + 1)
    assert await loop.reintentar_generacion_fallida() == 1
    post = radar_store.get_post(pid)
    assert post["estado"] == "GENERANDO" and post["intentos"] == 0 and post["reintentos_gen_auto"] == 1
    assert "Reintento automático 1/2" in post["error"]
    loop.wake_generation.assert_awaited()


@pytest.mark.asyncio
async def test_un_rechazo_de_calidad_no_se_reintenta_solo(monkeypatch):
    pid = _post_en_error("Falló la generación: Revisá el CV (tras una corrección automática): impacto vago")
    radar_store.update_radar_state(generacion_pausada=False)
    assert await _motor(monkeypatch, 60).reintentar_generacion_fallida() == 0
    assert radar_store.get_post(pid)["estado"] == "ERROR"


@pytest.mark.asyncio
async def test_no_reintenta_con_la_generacion_en_pausa(monkeypatch):
    pid = _post_en_error(ERRORES_AGY[0])
    radar_store.update_radar_state(generacion_pausada=True)
    assert await _motor(monkeypatch, 60).reintentar_generacion_fallida() == 0
    assert radar_store.get_post(pid)["estado"] == "ERROR"


@pytest.mark.asyncio
async def test_no_reintenta_sin_cuota_ni_con_agy_saturado(monkeypatch):
    pid = _post_en_error(ERRORES_AGY[0])
    radar_store.update_radar_state(generacion_pausada=False)
    bridge.CUOTA.agotada("Individual quota reached. Resets in 2h0m0s.")
    assert await _motor(monkeypatch, 60).reintentar_generacion_fallida() == 0
    bridge.CUOTA.restablecida()
    radar_store.update_radar_state(agy_serie_hasta=time.time() + 600)
    assert await _motor(monkeypatch, 60).reintentar_generacion_fallida() == 0
    assert radar_store.get_post(pid)["estado"] == "ERROR"


@pytest.mark.asyncio
async def test_no_vuelve_una_tercera_vez(monkeypatch):
    pid = _post_en_error(ERRORES_AGY[0])
    radar_store.update_radar_state(generacion_pausada=False)
    for vuelta in range(1, MAX_REINTENTOS_GENERACION_AUTO + 1):
        assert await _motor(monkeypatch, 31 * vuelta).reintentar_generacion_fallida() == 1
        radar_store.update_post(pid, estado="ERROR", intentos=2, error=ERRORES_AGY[0])  # vuelve a fallar
    assert await _motor(monkeypatch, 31 * (MAX_REINTENTOS_GENERACION_AUTO + 1)).reintentar_generacion_fallida() == 0
    post = radar_store.get_post(pid)
    assert post["estado"] == "ERROR" and post["reintentos_gen_auto"] == MAX_REINTENTOS_GENERACION_AUTO


@pytest.mark.asyncio
async def test_el_ciclo_del_radar_hace_el_barrido(monkeypatch):
    loop = _motor(monkeypatch, 0)
    for nombre in ("run_cycle", "_rescate_si_toca", "_entrar_en_pausa", "reintentar_analisis_fallidos"):
        monkeypatch.setattr(loop, nombre, AsyncMock(return_value=0))
    monkeypatch.setattr(loop, "_wait", AsyncMock(return_value=False))
    monkeypatch.setattr(loop, "reintentar_generacion_fallida", AsyncMock(return_value=0))
    await loop._harvest_loop()
    loop.reintentar_generacion_fallida.assert_awaited_once()
