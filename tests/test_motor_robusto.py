"""CV robusto (T1): progreso persistido por trabajo, plazo con reintento, espera de cuota, cancelación,
arranque, supervisor del worker, reintento del mensaje y del Redactor, timeouts del Auditor y ETA.

Todo con dobles: pipeline/composer/cuota/reloj falsos y guardas de 30 segundos para condiciones asíncronas.
"""
import asyncio
import itertools
import logging
import sqlite3
from datetime import datetime, timedelta

import pytest

from core import database, progreso, radar_store

_URN = itertools.count(1)
T0 = datetime(2026, 11, 4, 10, 0, 0)


def _post(estado, **extra):
    n = next(_URN)
    pid = radar_store.insert_post_if_new(
        {"urn": f"urn:li:activity:robusto{n}", "url": f"https://www.linkedin.com/in/r{n}", "autor": f"Autor {n}",
         "cargo_autor": "", "texto": f"Oferta robusta {n}", "fecha_rel": "1 h", "emails": ["rrhh@empresa.test"]},
        query="q",
    )
    radar_store.update_post(pid, estado=estado, triage={"puesto": "Analista", "es_busqueda_laboral": True},
                            **extra)
    return pid


def _sql(consulta, params=()):
    conn = database.get_db_connection()
    try:
        with conn:
            conn.execute(consulta, params)
    finally:
        conn.close()


# ------------------------------------------------------------------ progreso y store

def test_progreso_completar_sin_jerga_en_etapa_e_historial():
    prog = progreso.avanzar(progreso.nuevo(T0), "completar", T0)
    assert prog["etiqueta"] == "Completando puesto, CV, presentación y canales"
    prog = progreso.terminar(prog, "LISTO", T0 + timedelta(seconds=2))
    assert prog["pasos"][0]["etiqueta"] == "Completando puesto, CV, presentación y canales"

def test_progreso_avanzar_cierra_el_paso_anterior():
    prog = progreso.avanzar(progreso.nuevo(T0), "preparar", T0)
    prog = progreso.avanzar(prog, "redactar", T0 + timedelta(seconds=5))
    assert prog["pasos"][0]["paso"] == "preparar"
    assert prog["pasos"][0]["duracion_s"] == 5.0
    assert prog["pasos"][0]["fin"] == (T0 + timedelta(seconds=5)).isoformat(timespec="seconds")
    assert prog["paso"] == "redactar" and prog["pct"] == 15
    assert prog["etiqueta"] == "Redactando el CV"
    assert prog["inicio"] == T0.isoformat(timespec="seconds")
    assert prog["pasos"][-1]["fin"] is None and prog["fin"] is None and prog["resultado"] is None


def test_progreso_paso_repetido_solo_actualiza_sub_y_terminar_cierra():
    prog = progreso.avanzar(progreso.nuevo(T0), "auditar", T0)
    prog = progreso.avanzar(prog, "auditar", T0 + timedelta(seconds=2), sub="Intento 2")
    assert len(prog["pasos"]) == 1 and prog["sub"] == "Intento 2"
    prog = progreso.marcar_cuota(prog, True, "2026-11-04T18:00:00+00:00")
    assert prog["esperando_cuota"] is True and prog["cuota_renueva"] == "2026-11-04T18:00:00+00:00"
    prog = progreso.terminar(prog, "LISTO", T0 + timedelta(seconds=10))
    assert prog["resultado"] == "LISTO" and prog["fin"] == (T0 + timedelta(seconds=10)).isoformat(timespec="seconds")
    assert prog["pasos"][0]["duracion_s"] == 10.0
    assert prog["esperando_cuota"] is False


def test_guardar_progreso_no_toca_updated_at():
    pid = _post("LISTO")
    # Una versión histórica hace observable una escritura accidental aun en el mismo ms.
    _sql("UPDATE linkedin_posts SET updated_at = ? WHERE id = ?", (T0.isoformat(), pid))
    antes = radar_store.get_post(pid)["updated_at"]
    radar_store.guardar_progreso(pid, progreso.avanzar(progreso.nuevo(T0), "redactar", T0))
    post = radar_store.get_post(pid)
    assert post["updated_at"] == antes
    assert post["progreso"]["paso"] == "redactar"
    radar_store.guardar_progreso(pid, None)
    assert radar_store.get_post(pid)["progreso"] is None


def test_reconcile_desvincula_generando_sin_cv_listo():
    procesando = database.create_postulacion("Puesto", "Acme", "oferta", origen="linkedin")
    database.update_postulacion(procesando, status="PROCESSING")
    lista = database.create_postulacion("Puesto", "Acme", "oferta", origen="linkedin")
    database.update_postulacion(lista, status="READY")
    manual = database.create_postulacion("Manual", "Acme", "oferta")
    database.update_postulacion(manual, status="PROCESSING")
    prog = progreso.avanzar(progreso.nuevo(T0), "redactar", T0)
    colgado = _post("GENERANDO", postulacion_id=procesando)
    radar_store.guardar_progreso(colgado, prog)
    con_cv = _post("GENERANDO", postulacion_id=lista)

    radar_store.reconcile_on_startup()

    post = radar_store.get_post(colgado)
    assert post["estado"] == "GENERANDO"
    assert post["postulacion_id"] is None and post["progreso"] is None
    assert database.get_postulacion(procesando)["status"] == "ERROR"
    assert database.get_postulacion(manual)["status"] == "PROCESSING"  # las manuales no se tocan
    assert radar_store.get_post(con_cv)["postulacion_id"] == lista


def _postulacion_con_total(total_s):
    pid = database.create_postulacion("Puesto", "Acme", "oferta")
    database.update_postulacion(pid, telemetry={"etapas": [], "total_s": total_s})


def test_eta_usa_la_mediana():
    _post("GENERANDO")
    _post("GENERANDO")
    for total in (100.0, 100.0, 10000.0):
        _postulacion_con_total(total)
    assert radar_store.resumen_cola()["eta_s"] == 200


def test_progreso_ilegible_no_rompe_resumen_cola():
    pid = _post("GENERANDO")
    _sql("UPDATE linkedin_posts SET progreso = ? WHERE id = ?", ("{roto", pid))
    actual = radar_store.resumen_cola()["actual"]
    assert actual["post_id"] == pid
    assert actual["progreso"] is None and actual["etapa"] is None
    assert radar_store.get_post(pid)["progreso"] is None


def test_resumen_cola_expone_el_progreso_del_actual():
    pid = _post("GENERANDO")
    radar_store.guardar_progreso(pid, progreso.avanzar(progreso.nuevo(T0), "auditar", T0))
    actual = radar_store.resumen_cola()["actual"]
    assert actual["progreso"]["paso"] == "auditar"
    assert actual["etapa"] == "Revisando y corrigiendo el CV"


def test_resumen_cola_ignora_progreso_de_un_trabajo_ya_terminado():
    pid = _post("GENERANDO")
    viejo = progreso.terminar(progreso.avanzar(progreso.nuevo(T0), "mensaje", T0), "LISTO", T0)
    radar_store.guardar_progreso(pid, viejo)  # p. ej. «Regenerar»: vuelve a la cola con el progreso anterior
    actual = radar_store.resumen_cola()["actual"]
    assert actual["progreso"] is None and actual["etapa"] is None


# ------------------------------------------------------------------ worker de generación

import core.radar  # noqa: E402
from tests.test_radar_workers import FakePipeline, Recorder, crear_post, finalizar, make_radar  # noqa: E402


async def esperar(pred, segundos=30):
    """Sondea una condición sin limitar vueltas; el plazo solo guarda contra bloqueos."""
    async def sondear():
        while not pred():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(sondear(), segundos)


def _radar(tmp_path, *, pipeline=None, compose=None, broadcast=None, plazo=None, tick=None, cuota=None,
           reloj=None):
    radar = make_radar(tmp_path, pipeline=pipeline, compose=compose, broadcast=broadcast)
    if plazo is not None:
        radar._plazo_trabajo_s = plazo
    if tick is not None:
        radar._tick_plazo_s = tick
    if cuota is not None:
        radar._cuota = cuota
    if reloj is not None:
        radar._reloj = reloj
    return radar


def test_radar_loop_acepta_plazo_tick_reloj_y_cuota():
    reloj = lambda: 1.0  # noqa: E731
    cuota = lambda: {"agotada": False}  # noqa: E731
    radar = core.radar.RadarLoop(
        harvester_factory=lambda: None, triage_fn=None, analyze_fn=None, pipeline_fn=None, compose_fn=None,
        send_fn=None, broadcast=None, plazo_trabajo_s=1.5, tick_plazo_s=0.5, reloj=reloj, cuota=cuota,
    )
    assert (radar._plazo_trabajo_s, radar._tick_plazo_s, radar._reloj, radar._cuota) == (1.5, 0.5, reloj, cuota)
    assert core.radar.PLAZO_TRABAJO_S == 2700 and core.radar.TICK_PLAZO_S == 5.0
    assert core.radar.REINICIO_WORKER_S == 2.0
    assert radar.generando_ahora is None


@pytest.mark.asyncio
@pytest.mark.parametrize("agotada,esperado", [(False, 4.0), (True, 0.0)])
async def test_trabajo_corto_persiste_intervalo_final(tmp_path, agotada, esperado):
    pid = crear_post(1)
    tiempos = iter([0.0, 4.0])
    radar = _radar(tmp_path, reloj=lambda: next(tiempos), tick=5,
                   cuota=lambda: {"agotada": agotada})
    try:
        await radar._vigilar_generacion(radar_store.get_post(pid))
        prog = radar_store.get_post(pid)["progreso"]
        assert prog["resultado"] == "LISTO"
        assert prog["activo_s"] == esperado
    finally:
        await finalizar(radar)


@pytest.mark.asyncio
async def test_progreso_se_persiste_y_se_emite(tmp_path):
    gate = asyncio.Event()
    pid = crear_post(1)

    class Pipeline(FakePipeline):
        async def __call__(self, post):
            await progreso.reportar("redactar")
            return await super().__call__(post)

    rec = Recorder()
    radar = _radar(tmp_path, pipeline=Pipeline(gate=gate), broadcast=rec)
    try:
        await radar.wake_generation()
        await esperar(lambda: (radar_store.get_post(pid)["progreso"] or {}).get("paso") == "redactar")
        assert radar.generando_ahora == pid
        eventos = [p for n, p in rec.events if n == "radar_progreso" and p["id"] == pid]
        assert any(p["progreso"]["paso"] == "redactar" and p["percent"] == 15
                   and p["step"] == "Redactando el CV" for p in eventos)
        assert [p["paso"] for p in radar_store.get_post(pid)["progreso"]["pasos"]] == ["preparar", "redactar"]
        gate.set()
        await esperar(lambda: (radar_store.get_post(pid)["progreso"] or {}).get("resultado") == "LISTO")
        post = radar_store.get_post(pid)
        assert post["estado"] == "LISTO" and post["progreso"]["fin"] is not None
        assert [p["paso"] for p in post["progreso"]["pasos"]] == ["preparar", "redactar", "mensaje"]
        await esperar(lambda: radar.generando_ahora is None)
    finally:
        await finalizar(radar)


@pytest.mark.asyncio
async def test_plazo_vencido_reintenta_una_vez_y_despues_error(tmp_path):
    p1 = crear_post(1)
    p2 = crear_post(2)
    # Reloj falso: el tiempo de trabajo pasa solo mientras el pipeline está colgado (sin carreras
    # con lo que tarda la DB antes de llamarlo)
    ahora = [0.0]

    class Colgado(FakePipeline):
        async def __call__(self, post):
            if post["id"] == p1:
                self.calls.append(post["id"])
                ahora[0] += 3600.0  # una hora de trabajo
                await asyncio.Event().wait()  # nunca termina
            return await super().__call__(post)

    pipeline = Colgado()
    radar = _radar(tmp_path, pipeline=pipeline, plazo=60.0, tick=0.01, reloj=lambda: ahora[0])
    try:
        await radar.wake_generation()
        await esperar(lambda: radar_store.get_post(p2)["estado"] == "LISTO")
        post = radar_store.get_post(p1)
        assert pipeline.calls == [p1, p1, p2]
        assert post["estado"] == "ERROR"
        assert post["error"].startswith("Falló la generación") and "dos veces" in post["error"]
        assert post["intentos"] == 2
        assert post["progreso"]["resultado"] == "vencido"
    finally:
        await finalizar(radar)


@pytest.mark.asyncio
async def test_espera_de_cuota_no_cuenta_para_el_plazo(tmp_path):
    pid = crear_post(1)
    estado_cuota = {"agotada": True, "renueva": "2026-11-04T18:00:00+00:00", "reintento": None}
    pipeline = FakePipeline(gate=asyncio.Event())  # nunca termina
    lecturas = []

    def reloj():
        # Cada tick avanza una hora virtual, incluso mientras no hay cuota.
        lecturas.append(len(lecturas) * 3600.0)
        return lecturas[-1]

    radar = _radar(tmp_path, pipeline=pipeline, plazo=2700, tick=0.01,
                   cuota=lambda: dict(estado_cuota), reloj=reloj)
    try:
        await radar.wake_generation()
        await esperar(lambda: len(lecturas) >= 4 and pipeline.calls == [pid])
        post = radar_store.get_post(pid)
        assert post["estado"] == "GENERANDO" and pipeline.calls == [pid]
        assert post["progreso"]["esperando_cuota"] is True
        assert post["progreso"]["cuota_renueva"] == "2026-11-04T18:00:00+00:00"
        estado_cuota.clear()
        estado_cuota["agotada"] = False
        await esperar(lambda: len(pipeline.calls) >= 2 or radar_store.get_post(pid)["estado"] == "ERROR")
    finally:
        await finalizar(radar)


@pytest.mark.asyncio
async def test_tiempo_activo_se_guarda_durante_un_paso_largo(tmp_path):
    pid = crear_post(1)
    ahora = [0.0]
    pipeline = FakePipeline(gate=asyncio.Event())
    radar = _radar(tmp_path, pipeline=pipeline, tick=0.01, reloj=lambda: ahora[0])
    try:
        await radar.wake_generation()
        await esperar(lambda: pipeline.calls == [pid])
        ahora[0] = 30.0
        await esperar(lambda: radar._progresos[pid]["activo_s"] == 30.0)
        assert radar_store.get_post(pid)["progreso"]["activo_s"] == 30.0
    finally:
        await finalizar(radar)


@pytest.mark.asyncio
async def test_cuota_actualiza_renovacion_sin_reanudarse(tmp_path):
    pid = crear_post(1)
    cuota = {"agotada": True, "renueva": "2026-11-04T18:00:00+00:00"}
    radar = _radar(tmp_path, pipeline=FakePipeline(gate=asyncio.Event()), tick=0.01,
                   cuota=lambda: dict(cuota))
    try:
        await radar.wake_generation()
        await esperar(lambda: (radar_store.get_post(pid)["progreso"] or {}).get("esperando_cuota"))
        cuota["renueva"] = "2026-11-05T18:00:00+00:00"
        await esperar(lambda: radar_store.get_post(pid)["progreso"]["cuota_renueva"] == cuota["renueva"])
        assert radar_store.get_post(pid)["progreso"]["activo_s"] == 0.0
    finally:
        await finalizar(radar)


@pytest.mark.asyncio
async def test_cancelar_generacion_libera_la_cola(tmp_path):
    p1, p2 = crear_post(1), crear_post(2)

    class Bloqueado(FakePipeline):
        async def __call__(self, post):
            if post["id"] == p1:
                self.calls.append(post["id"])
                await asyncio.Event().wait()
            return await super().__call__(post)

    pipeline = Bloqueado()
    radar = _radar(tmp_path, pipeline=pipeline)
    try:
        await radar.wake_generation()
        await esperar(lambda: pipeline.calls == [p1])
        radar_store.update_post(p1, estado="DESCARTADO")
        assert await radar.cancelar_generacion(p1) is True
        post = radar_store.get_post(p1)
        assert post["estado"] == "DESCARTADO"
        assert post["progreso"]["resultado"] == "cancelado"
        await esperar(lambda: radar_store.get_post(p2)["estado"] == "LISTO")
        assert radar_store.get_post(p1)["estado"] == "DESCARTADO"
    finally:
        await finalizar(radar)


@pytest.mark.asyncio
async def test_cancelar_generacion_de_post_que_no_se_esta_generando_devuelve_false(tmp_path):
    pid = crear_post(1)
    otro = crear_post(2, estado="LISTO")
    radar = _radar(tmp_path)
    try:
        assert await radar.cancelar_generacion(pid) is False  # en cola, sin trabajo en curso
        await radar.wake_generation()
        await esperar(lambda: (radar_store.get_post(pid)["progreso"] or {}).get("resultado") == "LISTO")
        assert await radar.cancelar_generacion(pid) is False  # ya terminó
        assert await radar.cancelar_generacion(otro) is False
        assert radar_store.get_post(pid)["estado"] == "LISTO"
        assert radar_store.get_post(pid)["progreso"]["resultado"] == "LISTO"
    finally:
        await finalizar(radar)


@pytest.mark.asyncio
async def test_supervisor_relanza_el_worker_si_muere(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(core.radar, "REINICIO_WORKER_S", 0.01)
    pid = crear_post(1)
    original = radar_store.list_posts
    fallas = []

    def list_posts_que_falla_una_vez(*args, **kwargs):
        if not fallas:
            fallas.append(1)
            raise sqlite3.OperationalError("database is locked")
        return original(*args, **kwargs)

    monkeypatch.setattr(radar_store, "list_posts", list_posts_que_falla_una_vez)
    radar = _radar(tmp_path)
    try:
        with caplog.at_level(logging.ERROR, logger="core.radar"):
            await radar.wake_generation()
            await esperar(lambda: radar_store.get_post(pid)["estado"] == "LISTO")
        assert any(r.levelno == logging.ERROR and "worker de generación" in r.getMessage() for r in caplog.records)
    finally:
        await finalizar(radar)


@pytest.mark.asyncio
async def test_falla_de_generacion_suma_intentos(tmp_path):
    # Verifica una falla aislada; los reintentos del backlog se cubren en test_backlog.py.
    radar_store.update_radar_state(backlog_activo=False)
    pid = crear_post(1)
    radar = _radar(tmp_path, pipeline=FakePipeline(fail_ids={pid}))
    try:
        await radar.wake_generation()
        await esperar(lambda: (radar_store.get_post(pid)["progreso"] or {}).get("resultado") == "ERROR")
        post = radar_store.get_post(pid)
        assert post["estado"] == "ERROR" and post["intentos"] == 1
    finally:
        await finalizar(radar)


@pytest.mark.asyncio
async def test_stop_corta_la_tarea_hija_y_deja_el_post_en_cola(tmp_path):
    pid = crear_post(1)
    pipeline = FakePipeline(gate=asyncio.Event())
    radar = _radar(tmp_path, pipeline=pipeline)
    try:
        await radar.wake_generation()
        await esperar(lambda: pipeline.calls == [pid])
        hija = radar._trabajos_gen[pid]
        await finalizar(radar)
        assert hija is not None and hija.done()
        post = radar_store.get_post(pid)
        assert post["estado"] == "GENERANDO" and post["progreso"] is None
        assert radar.generando_ahora is None
    finally:
        await finalizar(radar)


@pytest.mark.asyncio
async def test_stop_conserva_el_progreso_si_el_cv_ya_termino(tmp_path):
    pid = crear_post(1)
    emitiendo_final = asyncio.Event()

    async def broadcast(nombre, payload):
        if (nombre == "radar_status" and not emitiendo_final.is_set()
                and radar_store.get_post(pid)["estado"] == "LISTO"):
            emitiendo_final.set()
            await asyncio.Event().wait()

    radar = _radar(tmp_path, broadcast=broadcast)
    try:
        await radar.wake_generation()
        await asyncio.wait_for(emitiendo_final.wait(), 30)
        await finalizar(radar)
        post = radar_store.get_post(pid)
        assert post["estado"] == "LISTO"
        assert post["progreso"]["resultado"] == "LISTO"
        assert post["progreso"]["fin"] is not None
    finally:
        await finalizar(radar)


# ------------------------------------------------------------------ API: descartar

from unittest.mock import AsyncMock  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402
from tests.cv_final_prueba import simular_cv_maestro  # noqa: E402
from core.radar import RadarLoop  # noqa: E402


@pytest.fixture
def radar_stub(monkeypatch):
    stub = AsyncMock(spec=RadarLoop)
    monkeypatch.setattr(server, "radar_loop", stub)
    return stub


def test_descartar_generando_cancela_la_tarea(radar_stub):
    pid = _post("GENERANDO")
    r = TestClient(server.app).post(f"/api/radar/posts/{pid}/discard", headers={"X-LinkedOut-Origen": "portal"})
    assert r.status_code == 200
    assert radar_store.get_post(pid)["estado"] == "DESCARTADO"
    radar_stub.cancelar_generacion.assert_awaited_once_with(pid)


def test_descartar_listo_no_cancela_nada(radar_stub):
    pid = _post("LISTO")
    r = TestClient(server.app).post(f"/api/radar/posts/{pid}/discard", headers={"X-LinkedOut-Origen": "portal"})
    assert r.status_code == 200
    radar_stub.cancelar_generacion.assert_not_awaited()


# ------------------------------------------------------------------ server: pasos, pipeline y mensaje

from tests.test_radar_pipeline_events import DummyAuditor, DummyStrategist, DummyWriter  # noqa: E402
from tests.test_cv_formatting_and_a4_e2e import SAMPLE_E2E_CV  # noqa: E402

CV_OK = "# CV\n## PERFIL\nAnalista con experiencia en procesos."


@pytest.fixture
def agentes_falsos(monkeypatch):
    monkeypatch.setattr(server, "StrategistAgent", DummyStrategist)
    monkeypatch.setattr(server, "WriterAgent", DummyWriter)
    monkeypatch.setattr(server, "AuditorAgent", DummyAuditor)
    monkeypatch.setattr(server, "subscribers", [])
    simular_cv_maestro(monkeypatch, server)


def _grabador():
    pasos = []

    async def reportero(paso, sub=None):
        pasos.append(paso)

    return pasos, reportero


@pytest.mark.asyncio
async def test_pipeline_completo_reporta_sus_pasos(agentes_falsos):
    pid = database.create_postulacion(titulo="QA", empresa="Acme", contenido="Buscamos QA", url="", origen="linkedin")
    pasos, reportero = _grabador()
    with progreso.usar_reportero(reportero):
        await server.run_pipeline_task(pid)
    assert database.get_postulacion(pid)["status"] == "READY"
    assert pasos == ["analizar", "redactar", "auditar"]  # «completar» era del pipeline viejo


@pytest.mark.asyncio
async def test_recalculo_del_cv_reporta_sus_pasos(agentes_falsos):
    pid = database.create_postulacion(titulo="QA", empresa="Acme", contenido="Buscamos QA", url="", origen="linkedin")
    database.update_postulacion(pid, requisitos=[
        {"nombre": "Python", "estado": "MATCH_DIRECTO", "approved": True}])
    pasos, reportero = _grabador()
    with progreso.usar_reportero(reportero):
        await server.recalculate_cv_task(pid)
    assert database.get_postulacion(pid)["status"] == "READY"
    assert pasos == ["redactar", "auditar"]


@pytest.mark.asyncio
async def test_pipeline_cancelado_deja_la_postulacion_en_error(monkeypatch):
    pid = _post("GENERANDO", analisis={"puesto": "QA", "requisitos": [{"nombre": "Python", "estado": "MATCH_DIRECTO"}]})
    empezo = asyncio.Event()

    async def recalculo_colgado(postulacion_id):
        database.update_postulacion(postulacion_id, status="PROCESSING")
        empezo.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(server, "recalculate_cv_task", recalculo_colgado)
    tarea = None
    try:
        tarea = asyncio.create_task(server._radar_pipeline(radar_store.get_post(pid)))
        await asyncio.wait_for(empezo.wait(), 30)
        postulacion_id = radar_store.get_post(pid)["postulacion_id"]
        tarea.cancel()
        with pytest.raises(asyncio.CancelledError):
            await tarea
        assert database.get_postulacion(postulacion_id)["status"] == "ERROR"
        assert radar_store.get_post(pid)["postulacion_id"] is None
    finally:
        if tarea is not None:
            tarea.cancel()
            await asyncio.gather(tarea, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("caso", ["stop", "pipeline"])
async def test_limpieza_incluso_si_falla_la_espera(tmp_path, monkeypatch, caso):
    class EsperaFallida(AssertionError):
        pass

    radares, tareas = [], []
    original_radar, original_create = _radar, asyncio.create_task
    original_wait = asyncio.wait_for

    def registrar_radar(*args, **kwargs):
        radar = original_radar(*args, **kwargs)
        radares.append(radar)
        return radar

    def registrar_tarea(coro, **kwargs):
        tarea = original_create(coro, **kwargs)
        tareas.append(tarea)
        return tarea

    async def esperar_y_fallar(pred, **kwargs):
        await esperar_original(pred, **kwargs)
        raise EsperaFallida()

    async def wait_y_fallar(coro, timeout):
        resultado = await original_wait(coro, timeout)
        if getattr(coro, "__qualname__", "") == "Event.wait":
            raise EsperaFallida()
        return resultado

    esperar_original = esperar
    monkeypatch.setitem(globals(), "_radar", registrar_radar)
    monkeypatch.setattr(asyncio, "create_task", registrar_tarea)
    try:
        with pytest.raises(EsperaFallida):
            if caso == "stop":
                monkeypatch.setitem(globals(), "esperar", esperar_y_fallar)
                await test_stop_corta_la_tarea_hija_y_deja_el_post_en_cola(tmp_path)
            else:
                monkeypatch.setattr(asyncio, "wait_for", wait_y_fallar)
                await test_pipeline_cancelado_deja_la_postulacion_en_error(monkeypatch)
        assert tareas and all(t.done() for t in tareas), "quedaron tareas vivas al fallar la espera"
    finally:
        for radar in radares:
            await finalizar(radar)
        for tarea in tareas:
            tarea.cancel()
        await asyncio.gather(*tareas, return_exceptions=True)


def _postulacion_lista():
    pid = database.create_postulacion("QA", "Acme", "Buscamos QA", origen="linkedin")
    database.update_postulacion(pid, status="READY", cv_markdown=SAMPLE_E2E_CV)
    return pid


class ComposeFalso:
    def __init__(self, *fallbacks):
        self.fallbacks = list(fallbacks)
        self.llamadas = 0

    async def __call__(self, post, postulacion):
        self.llamadas += 1
        fallback = self.fallbacks.pop(0) if self.fallbacks else False
        return {"to": "rrhh@empresa.test", "subject": f"Asunto {self.llamadas}", "body": f"Cuerpo {self.llamadas}",
                "is_fallback": fallback}


@pytest.fixture
def mensaje_sin_modelo(monkeypatch):
    async def _identidad_cv(postulacion):
        return postulacion

    async def _identidad_mensaje(post, postulacion, mensaje):
        return mensaje

    monkeypatch.setattr(server, "_cv_al_dia", _identidad_cv)
    monkeypatch.setattr(server, "_completar_mensaje", _identidad_mensaje)


@pytest.mark.asyncio
async def test_compose_fallback_reintenta_y_si_vuelve_falla(monkeypatch, mensaje_sin_modelo):
    compose = ComposeFalso(True, True)
    monkeypatch.setattr(server, "compose_message", compose)
    post = radar_store.get_post(_post("GENERANDO"))
    with pytest.raises(RuntimeError, match="dos veces"):
        await server._radar_compose(post, _postulacion_lista())
    assert compose.llamadas == 2


@pytest.mark.asyncio
async def test_compose_fallback_una_vez_y_despues_ok(monkeypatch, mensaje_sin_modelo):
    compose = ComposeFalso(True, False)
    monkeypatch.setattr(server, "compose_message", compose)
    post = radar_store.get_post(_post("GENERANDO"))
    pasos, reportero = _grabador()
    with progreso.usar_reportero(reportero):
        mensaje = await server._radar_compose(post, _postulacion_lista())
    assert compose.llamadas == 2
    assert mensaje["subject"] == "Asunto 2" and mensaje["is_fallback"] is False
    assert pasos == ["completar_mensaje"]


# ------------------------------------------------------------------ Auditor y Redactor

from core.auditor import AuditorAgent  # noqa: E402
from core.writer import WriterAgent  # noqa: E402


class BridgeRespuestas:
    def __init__(self, *respuestas):
        self.respuestas = iter(respuestas)
        self.llamadas = []

    async def run_prompt(self, prompt, **kwargs):
        self.llamadas.append(kwargs)
        respuesta = next(self.respuestas)
        if isinstance(respuesta, Exception):
            raise respuesta
        return respuesta


@pytest.mark.asyncio
async def test_remediacion_usa_timeout_acorde_al_effort():
    bridge = BridgeRespuestas({"lineas": [], "pitch": ""}, {"lineas": [], "pitch": ""})
    cv = CV_OK.replace("Analista con experiencia en procesos.", "Cuento con experiencia en procesos.")
    cv += "\n## HABILIDADES TÉCNICAS\n• Python"
    await AuditorAgent(bridge=bridge).audit_and_remediate(cv, "Hola", ["SQL"])
    assert len(bridge.llamadas) == 1
    assert [c["timeout_seconds"] for c in bridge.llamadas] == [420]
    assert [c["effort"] for c in bridge.llamadas] == ["medium"]


@pytest.mark.asyncio
@pytest.mark.parametrize("destino_seguro", [False, True])
async def test_remediacion_fallida_se_registra(caplog, destino_seguro):
    bridge = BridgeRespuestas(RuntimeError("modelo caído"))
    original = (CV_OK.replace("Analista con experiencia en procesos.", "Cuento con experiencia en procesos.")
                if destino_seguro else CV_OK)
    with caplog.at_level(logging.WARNING, logger="core.auditor"):
        cv, pitch, report = await AuditorAgent(bridge=bridge).audit_and_remediate(original, "Hola", ["SQL"])
    assert cv == original
    assert pitch == "Hola" and report["approved"] is False
    assert len(bridge.llamadas) == int(destino_seguro)
    if destino_seguro:
        assert "Falló la remediación de PERFIL (intento 1): modelo caído" in caplog.text
    else:
        assert "No se remedia: no hay destino seguro para los hallazgos" in caplog.text


PERFIL_FALSO = {"personales": "Ana | ana@example.com", "experiencia": "Analista en Acme, 2020-2026"}


@pytest.mark.asyncio
async def test_redactor_reintenta_con_medium_tras_timeout():
    bridge = BridgeRespuestas(TimeoutError("agotado"), {"cv_markdown": CV_OK, "pitch": "Hola"})
    resultado = await WriterAgent(bridge=bridge).generate_cv_and_pitch({"puesto": "QA"}, PERFIL_FALSO)
    assert resultado["is_fallback"] is False
    assert "Analista con experiencia" in resultado["cv_markdown"]
    assert len(bridge.llamadas) == 2
    assert bridge.llamadas[1]["effort"] == "medium"


@pytest.mark.asyncio
async def test_redactor_dos_timeouts_cae_al_fallback():
    bridge = BridgeRespuestas(TimeoutError("primero"), TimeoutError("segundo"))
    resultado = await WriterAgent(bridge=bridge).generate_cv_and_pitch({"puesto": "QA"}, PERFIL_FALSO)
    assert resultado["is_fallback"] is True
    assert len(bridge.llamadas) == 2
    assert "segundo" in resultado["fallback_reason"]


@pytest.mark.asyncio
async def test_redactor_otro_error_no_reintenta():
    bridge = BridgeRespuestas(RuntimeError("modelo caído"))
    resultado = await WriterAgent(bridge=bridge).generate_cv_and_pitch({"puesto": "QA"}, PERFIL_FALSO)
    assert resultado["is_fallback"] is True
    assert len(bridge.llamadas) == 1
