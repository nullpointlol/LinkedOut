"""Backlog persistido: pruebas aisladas, sin proveedores ni portal reales."""
import asyncio
import sqlite3
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import server
from core import radar, radar_store as store
from core.radar import RadarLoop
from tests.test_radar_workers import (
    FakePipeline, WORKER_TIMEOUT, crear_post, esperar_workers, finalizar, make_radar,
)
from tests.perfil_ti import roles_ti  # noqa: F401

pytestmark = pytest.mark.usefixtures("roles_ti")  # prioridades de un perfil de TI


def test_backlog_ordena_por_afinidad_y_cerradas_al_final():
    import os
    m50 = crear_post(1, "ANALIZADA", analisis={"match_score": 76})
    m90 = crear_post(2, "ANALIZADA", analisis={"match_score": 90})
    m70 = crear_post(3, "ANALIZADA", analisis={"match_score": 78}, email_body="  ")
    cerrada = crear_post(4, "ANALIZADA", analisis={"match_score": 95, "oferta_cerrada": True})
    crear_post(5, "LISTO", analisis={"match_score": 90})
    crear_post(6, "ANALIZADA", analisis={"match_score": 90}, email_body="Ya tiene mensaje")
    pendientes = [p["id"] for p in store.backlog_pendiente()]
    # invariante: las ofertas abiertas se ordenan por afinidad descendente
    assert pendientes[:3] == [m90, m70, m50]
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    # defecto: con la regla unificada, ofertas cerradas quedan excluidas del backlog
    assert cerrada not in pendientes
    assert pendientes == [m90, m70, m50]


@pytest.mark.asyncio
async def test_worker_prioriza_forzada_sobre_automatica_sobre_backlog(tmp_path):
    auto = crear_post(1, gen_prioridad=1)
    forzada = crear_post(2, gen_prioridad=2)
    backlog = crear_post(3, "ANALIZADA", analisis={"match_score": 78})
    pipeline = FakePipeline()
    loop = make_radar(tmp_path, pipeline=pipeline, umbral=70)
    try:
        await loop.wake_generation()
        await esperar_workers(loop)
        assert pipeline.calls == [forzada, auto, backlog]  # prioridad 2 > 1 > 0 antes del score
    finally:
        await finalizar(loop)


@pytest.mark.asyncio
async def test_backlog_corre_con_el_radar_detenido(tmp_path):
    ids = [crear_post(i, "ANALIZADA", analisis={"match_score": 78}) for i in (1, 2)]
    loop = make_radar(tmp_path, umbral=70)
    store.update_radar_state(backlog_activo=True)
    try:
        assert not loop.is_running
        await loop.wake_generation()
        await esperar_workers(loop)
        assert all(store.get_post(i)["estado"] == "LISTO" for i in ids)
        assert all(store.get_post(i)["aprobado_at"] is None for i in ids)
    finally:
        await finalizar(loop)


@pytest.mark.asyncio
async def test_backlog_pausado_no_toma_nada(tmp_path):
    pid = crear_post(1, "ANALIZADA")
    store.update_radar_state(backlog_activo=False)
    loop = make_radar(tmp_path, umbral=70)
    try:
        await loop.wake_generation()
        await esperar_workers(loop)
        assert store.get_post(pid)["estado"] == "ANALIZADA"
        assert loop._gen_task.done()
        assert store.resumen_cola()["backlog"] == {"activo": False, "quedan": 1}
    finally:
        await finalizar(loop)


def test_backlog_no_retoma_errores_con_dos_intentos():
    crear_post(1, "ERROR", error="Falló la generación: prueba", intentos=2, analisis={"match_score": 78})
    reintentable = crear_post(2, "ERROR", error="Falló la generación: prueba", intentos=1, analisis={"match_score": 78})
    crear_post(3, "ERROR", error="Falló el análisis", intentos=1, analisis={"match_score": 78})
    assert [p["id"] for p in store.backlog_pendiente()] == [reintentable]


@pytest.mark.asyncio
async def test_nueva_analizada_en_el_umbral_no_entra_a_ninguna_cola(tmp_path):
    """score 70 con umbral 70 no supera el umbral: queda ANALIZADA fuera de las colas."""
    import os
    from core import colas
    pid = crear_post(1, "OFERTA")
    store.update_radar_state(backlog_activo=False)
    loop = make_radar(tmp_path, umbral=70)
    loop._analyze_fn = AsyncMock(return_value={"match_score": 70, "puesto": "Analista Funcional"})
    loop.generate_cv = AsyncMock()
    try:
        await loop._analizar(store.get_post(pid))
        await esperar_workers(loop)
        # invariante: análisis completa y post queda guardado
        assert store.get_post(pid)["estado"] in ("ANALIZADA", "GENERANDO", "LISTO")
        if os.environ.get("INVARIANT_CHECK") == "1":
            return
        # defecto: con umbral estricto > 70, score 70 exacto no se encola automáticamente ni entra al backlog
        assert store.get_post(pid)["estado"] == "ANALIZADA"
        assert colas.nivel(store.get_post(pid)) is None
        loop.generate_cv.assert_not_awaited()
    finally:
        await finalizar(loop)


def test_api_backlog_pausa_y_reanuda(monkeypatch):
    stub = AsyncMock(spec=RadarLoop)
    monkeypatch.setattr(server, "radar_loop", stub)
    client = TestClient(server.app)
    r = client.post("/api/radar/backlog", json={"activo": False})
    assert r.status_code == 200
    assert r.json()["cola"]["backlog"]["activo"] is False
    stub.wake_generation.assert_not_awaited()
    r = client.post("/api/radar/backlog", json={"activo": True})
    assert r.status_code == 200
    assert r.json()["cola"]["backlog"]["activo"] is True
    stub.wake_generation.assert_awaited_once()
    assert stub.notify_status.await_count == 2


def test_migracion_radar_state_agrega_backlog_activo(tmp_path):
    db = tmp_path / "vieja.db"
    with sqlite3.connect(db) as conn:
        conn.execute("""CREATE TABLE radar_state (
            id INTEGER PRIMARY KEY, desired_running INTEGER DEFAULT 0,
            estado TEXT DEFAULT 'DETENIDO', estado_detalle TEXT DEFAULT '',
            next_cycle_at TIMESTAMP, umbral_match INTEGER DEFAULT 60,
            tope_posts_dia INTEGER DEFAULT 600, tope_envios_dia INTEGER DEFAULT 40,
            contadores_dia TEXT DEFAULT '{}', fecha_contadores TEXT,
            ciclos_vacios_consecutivos INTEGER DEFAULT 0, envios_pausados INTEGER DEFAULT 0
        )""")
        conn.execute("INSERT INTO radar_state (id) VALUES (1)")
    assert store.get_radar_state(db_path=db)["backlog_activo"] is True
    store.update_radar_state(db_path=db, backlog_activo=False)
    assert store.get_radar_state(db_path=db)["backlog_activo"] is False


def test_tomar_backlog_revalida_y_no_reinicia_intentos():
    pid = crear_post(1, "ERROR", error="Falló la generación: prueba", intentos=1, analisis={"match_score": 80})
    assert store.tomar_backlog(pid) is True
    post = store.get_post(pid)
    assert (post["estado"], post["gen_prioridad"], post["error"], post["intentos"]) == (
        "GENERANDO", 0, None, 1,
    )
    assert store.tomar_backlog(pid) is False
    otro = crear_post(2, "ANALIZADA")
    store.update_post(otro, email_body="Mensaje agregado mientras esperaba")
    assert store.tomar_backlog(otro) is False


@pytest.mark.asyncio
async def test_backlog_falla_dos_veces_y_termina(tmp_path):
    pid = crear_post(1, "ANALIZADA", analisis={"match_score": 78})
    pipeline = FakePipeline(fail_ids={pid})
    loop = make_radar(tmp_path, pipeline=pipeline, umbral=70)
    try:
        await loop.wake_generation()
        await esperar_workers(loop)
        assert pipeline.calls == [pid, pid]
        assert store.get_post(pid)["intentos"] == 2
        assert store.get_post(pid)["estado"] == "ERROR"
    finally:
        await finalizar(loop)


@pytest.mark.asyncio
async def test_resume_despierta_backlog_y_respeta_pausa(tmp_path):
    # De fondo (2 semanas): solo corre con la cola de fondo activa
    pid = crear_post(1, "ANALIZADA", analisis={"match_score": 78}, fecha_rel="16 días")
    loop = make_radar(tmp_path, umbral=70)
    try:
        store.update_radar_state(backlog_activo=False)
        await loop.resume_if_needed()
        await esperar_workers(loop)
        assert store.get_post(pid)["estado"] == "ANALIZADA"
        store.update_radar_state(backlog_activo=True)
        await loop.resume_if_needed()
        await esperar_workers(loop)
        assert store.get_post(pid)["estado"] == "LISTO"
    finally:
        await finalizar(loop)


def test_ordenes_toleran_null_y_analisis_roto():
    posts = [{"id": 2, "analisis": "roto", "gen_prioridad": None},
             {"id": 1, "analisis": {"match_score": None}},
             {"id": 3, "analisis": {"match_score": 0}, "gen_prioridad": 2}]
    assert [p["id"] for p in radar.orden_backlog(posts)] == [3, 1, 2]
    assert [p["id"] for p in radar.orden_generacion(posts)] == [3, 1, 2]


@pytest.mark.asyncio
async def test_prioridades_en_los_pedidos_de_generacion(tmp_path):
    loop = make_radar(tmp_path, umbral=70)
    loop.wake_generation = AsyncMock()
    loop._wake_processing = lambda: None
    manual = crear_post(1, "ANALIZADA")
    retry = crear_post(2, "ERROR", error="Falló la generación: prueba")
    auto = crear_post(3, "ANALIZADA", analisis={"match_score": 80})
    mensaje = crear_post(4, "LISTO", postulacion_id=42)
    await loop.generate_cv(manual)
    await loop.retry(retry)
    assert await loop.generar_automaticos([auto]) == []  # la automática espera en la cola dinámica
    assert store.get_post(auto)["estado"] == "ANALIZADA"
    await loop.reprocesar()
    for pid in (manual, retry, mensaje):
        assert store.get_post(pid)["gen_prioridad"] == radar.PRIORIDAD_FORZADA == 2
    assert store.resumen_cola()["actual"]["post_id"] == manual


@pytest.mark.asyncio
@pytest.mark.parametrize("mensaje", [None, "", "  ", "Mensaje existente"])
async def test_automaticos_solo_toma_analizadas_sin_mensaje(tmp_path, mensaje):
    pid = crear_post(1, "ANALIZADA", analisis={"match_score": 90}, email_body=mensaje)
    loop = make_radar(tmp_path, umbral=70)
    loop.wake_generation = AsyncMock()
    loop._emitir_post = AsyncMock()
    aceptado = mensaje != "Mensaje existente"
    assert await loop.generar_automaticos([pid, pid]) == []
    assert store.get_post(pid)["estado"] == "ANALIZADA"
    elegido = loop._siguiente_generacion()
    if aceptado:
        assert elegido["id"] == pid and elegido["estado"] == "GENERANDO"
        assert elegido["gen_prioridad"] == radar.PRIORIDAD_AUTO
    else:
        assert elegido is None
    assert store.get_post(pid)["email_body"] == mensaje


@pytest.mark.asyncio
async def test_automaticos_respeta_pedido_manual_durante_notificacion(tmp_path):
    manual = crear_post(1, "ANALIZADA", analisis={"match_score": 90})
    primero = crear_post(2, "ANALIZADA", analisis={"match_score": 90})
    loop = make_radar(tmp_path, umbral=70)
    loop.wake_generation = AsyncMock()
    notificados = []

    async def notificar(pid):
        notificados.append((pid, store.get_post(pid)["gen_prioridad"]))
        if pid == primero:
            await loop.generate_cv(manual)

    loop._emitir_post = notificar
    assert await loop.generar_automaticos() == []
    assert notificados == []
    await loop.generate_cv(manual)
    assert store.get_post(manual)["estado"] == "GENERANDO"
    assert store.get_post(manual)["gen_prioridad"] == 2
    # La forzada pasa delante de la automática, que sigue esperando sin tomarse
    assert loop._siguiente_generacion()["id"] == manual
    assert store.get_post(primero)["estado"] == "ANALIZADA"


@pytest.mark.asyncio
async def test_automaticos_respeta_backlog_terminado_durante_notificacion(tmp_path):
    terminado = crear_post(1, "ANALIZADA", analisis={"match_score": 90})
    primero = crear_post(2, "ANALIZADA", analisis={"match_score": 90})
    pipeline = FakePipeline()
    loop = make_radar(tmp_path, umbral=70, pipeline=pipeline)
    emitir = loop._emitir_post
    intercalado = False
    mensaje = None
    notificados = []

    async def notificar(pid):
        nonlocal intercalado, mensaje
        notificados.append((pid, store.get_post(pid)["estado"]))
        await emitir(pid)
        if pid == primero and not intercalado:
            intercalado = True
            await loop.wake_generation()
            await esperar_workers(loop)
            mensaje = store.get_post(terminado)["email_body"]
            assert mensaje
            assert store.get_post(terminado)["estado"] == "LISTO"

    try:
        assert await loop.generar_automaticos() == []
        await esperar_workers(loop)
        # Mismo día, contacto, rol y score: desempata el id
        assert pipeline.calls == [terminado, primero]
        assert store.get_post(terminado)["estado"] == "LISTO"
        assert store.get_post(primero)["estado"] == "LISTO"
    finally:
        await finalizar(loop)


def test_regeneracion_tiene_prioridad_forzada(monkeypatch):
    stub = AsyncMock(spec=RadarLoop)
    monkeypatch.setattr(server, "radar_loop", stub)
    pid = crear_post(1, "LISTO", postulacion_id=42)
    response = TestClient(server.app).post(f"/api/radar/posts/{pid}/regenerate")
    assert response.status_code == 200
    assert store.get_post(pid)["gen_prioridad"] == 2
    stub.wake_generation.assert_awaited_once()


@pytest.mark.asyncio
async def test_pausa_no_interrumpe_el_actual_y_no_toma_el_siguiente(tmp_path):
    p1 = crear_post(1, "ANALIZADA", analisis={"match_score": 90}, fecha_rel="16 días")
    p2 = crear_post(2, "ANALIZADA", analisis={"match_score": 80}, fecha_rel="16 días")
    gate = asyncio.Event()
    pipeline = FakePipeline(gate=gate)
    loop = make_radar(tmp_path, pipeline=pipeline, umbral=70)
    try:
        await loop.wake_generation()
        await asyncio.wait_for(pipeline.started.wait(), WORKER_TIMEOUT)
        assert pipeline.calls == [p1]
        store.update_radar_state(backlog_activo=False)
        gate.set()
        await esperar_workers(loop)
        assert store.get_post(p1)["estado"] == "LISTO"
        assert store.get_post(p2)["estado"] == "ANALIZADA"
        assert pipeline.calls == [p1]
    finally:
        gate.set()
        await finalizar(loop)


def test_siguiente_generacion_salta_toma_perdida(tmp_path, monkeypatch):
    p1 = crear_post(1, "ANALIZADA", analisis={"match_score": 90})
    p2 = crear_post(2, "ANALIZADA", analisis={"match_score": 80})
    tomar = store.tomar_candidata

    def competir(pid, nivel):
        if pid == p1:
            store.update_post(pid, estado="DESCARTADO")
        return tomar(pid, nivel)

    monkeypatch.setattr(store, "tomar_candidata", competir)
    elegido = make_radar(tmp_path, umbral=70)._siguiente_generacion()
    assert elegido["id"] == p2 and elegido["estado"] == "GENERANDO"
    assert elegido["gen_prioridad"] == radar.PRIORIDAD_AUTO == 1


@pytest.mark.asyncio
async def test_resumen_mantiene_actual_si_llega_una_forzada(tmp_path):
    actual = crear_post(1, "ANALIZADA", analisis={"match_score": 78})
    gate = asyncio.Event()
    pipeline = FakePipeline(gate=gate)
    loop = make_radar(tmp_path, pipeline=pipeline, umbral=70)
    try:
        await loop.wake_generation()
        await asyncio.wait_for(pipeline.started.wait(), WORKER_TIMEOUT)
        assert pipeline.calls == [actual]
        forzada = crear_post(2, gen_prioridad=2)
        cola = store.resumen_cola()
        assert cola["actual"]["post_id"] == actual
        assert cola["actual"]["progreso"] is not None
        gate.set()
        await esperar_workers(loop)
        assert pipeline.calls == [actual, forzada]
    finally:
        gate.set()
        await finalizar(loop)
