"""Pausa global de CV y mensajes, con SQLite temporal y proveedores simulados."""
import asyncio
import json
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from bs4 import BeautifulSoup

import server
from core import radar_store as store
from core.radar import RadarLoop
from tests.test_radar_workers import (
    FakeCompose, FakePipeline, WORKER_TIMEOUT, crear_post, esperar_workers,
    finalizar, make_radar, wait_until,
)
from tests.test_radar_ui import _run_node


class AvisoEspera(asyncio.Event):
    """Permite observar que el worker llegó a esperar sin depender de una demora."""
    def __init__(self):
        super().__init__()
        self.esperando = False

    async def wait(self):
        self.esperando = True
        try:
            return await super().wait()
        finally:
            self.esperando = False


@pytest.mark.asyncio
@pytest.mark.parametrize("prioridad", [0, 1, 2])
async def test_pausa_conserva_generando_y_reanuda_sin_iniciar_radar(tmp_path, prioridad):
    pid = crear_post(1, gen_prioridad=prioridad)
    pipeline, compose = FakePipeline(), FakeCompose()
    loop = make_radar(tmp_path, pipeline=pipeline, compose=compose)
    loop._generation_event = aviso = AvisoEspera()
    store.update_radar_state(generacion_pausada=True)
    try:
        assert not loop.is_running
        await loop.wake_generation()
        await wait_until(lambda: aviso.esperando or pipeline.calls or loop._gen_task.done())
        assert pipeline.calls == []
        assert compose.calls == []
        assert aviso.esperando
        post = store.get_post(pid)
        assert post["estado"] == "GENERANDO" and post["error"] is None
        assert post["intentos"] == 0

        store.update_radar_state(generacion_pausada=False)
        await loop.wake_generation()
        await esperar_workers(loop)
        assert pipeline.calls == [pid]
        assert compose.calls == [(pid, 42)]
        assert store.get_post(pid)["estado"] == "LISTO"
        assert not loop.is_running and loop._test_created == []
    finally:
        await finalizar(loop)


@pytest.mark.asyncio
async def test_pausa_deja_terminar_actual_pero_no_toma_otro_ni_backlog(tmp_path):
    p1 = crear_post(1, gen_prioridad=2)
    p2 = crear_post(2, gen_prioridad=1)
    backlog = crear_post(3, "ANALIZADA", analisis={"match_score": 78})
    gate = asyncio.Event()
    pipeline = FakePipeline(gate=gate)
    loop = make_radar(tmp_path, pipeline=pipeline)
    loop._generation_event = aviso = AvisoEspera()
    try:
        await loop.wake_generation()
        await asyncio.wait_for(pipeline.started.wait(), WORKER_TIMEOUT)
        store.update_radar_state(generacion_pausada=True)
        gate.set()
        await wait_until(lambda: aviso.esperando or len(pipeline.calls) > 1 or loop._gen_task.done())
        assert pipeline.calls == [p1]
        assert store.get_post(p1)["estado"] == "LISTO"
        assert store.get_post(p2)["estado"] == "GENERANDO"
        assert store.get_post(backlog)["estado"] == "ANALIZADA"
        assert store.get_radar_state()["backlog_activo"] is True
        store.update_radar_state(generacion_pausada=False)
        await loop.wake_generation()
        await esperar_workers(loop)
        assert pipeline.calls == [p1, p2, backlog]
    finally:
        gate.set()
        await finalizar(loop)


@pytest.mark.asyncio
async def test_nuevo_se_clasifica_analiza_y_reserva_automatico_durante_pausa(tmp_path):
    pid = crear_post(1, "NUEVO", prioridad=1)
    pipeline = FakePipeline()
    loop = make_radar(tmp_path, pipeline=pipeline, umbral=60)
    store.update_radar_state(generacion_pausada=True)
    try:
        loop._wake_processing()
        await asyncio.wait_for(asyncio.shield(loop._proc_task), WORKER_TIMEOUT)
        post = store.get_post(pid)
        assert post["analisis"]["match_score"] == 80.0
        # Colas dinámicas (08/11): queda ANALIZADA hasta que el generador la tome; con pausa no la toma
        assert post["estado"] == "ANALIZADA"
        assert loop._siguiente_generacion() is None
        assert pipeline.calls == []
        assert not loop.is_running and loop._test_created == []
    finally:
        await finalizar(loop)


def test_endpoint_pausa_persiste_devuelve_status_y_despierta_al_reanudar(monkeypatch):
    stub = AsyncMock(spec=RadarLoop)
    monkeypatch.setattr(server, "radar_loop", stub)
    client = TestClient(server.app)
    for pausada in (True, False):
        response = client.post("/api/radar/generacion", json={"pausada": pausada})
        assert response.status_code == 200
        status = response.json()
        assert status["generacion_pausada"] is pausada
        assert "cola" in status and "gmail_configurado" in status
        assert store.get_radar_state()["generacion_pausada"] is pausada
        assert client.get("/api/radar/status").json()["generacion_pausada"] is pausada
        if pausada:
            stub.wake_generation.assert_not_awaited()
    stub.wake_generation.assert_awaited_once()
    assert stub.notify_status.await_count == 2


def test_migracion_aditiva_de_pausa_conserva_estado_anterior(tmp_path):
    db = tmp_path / "anterior.db"
    store.update_radar_state(db_path=db, backlog_activo=False, umbral_match=73)
    with sqlite3.connect(db) as conn:
        # Simula la tabla previa a esta feature, conservando todas sus otras columnas.
        columnas = {r[1] for r in conn.execute("PRAGMA table_info(radar_state)")}
        if "generacion_pausada" in columnas:
            conn.execute("ALTER TABLE radar_state DROP COLUMN generacion_pausada")
    state = store.get_radar_state(db_path=db)
    assert state["generacion_pausada"] is False
    assert state["backlog_activo"] is False and state["umbral_match"] == 73
    store.update_radar_state(db_path=db, generacion_pausada=True)
    assert store.get_radar_state(db_path=db)["generacion_pausada"] is True


def test_control_de_generacion_en_historial_con_texto_legible_y_fila_adaptable():
    soup = BeautifulSoup(Path("static/index.html").read_text(encoding="utf-8"), "html.parser")
    boton = soup.find(id="radarGeneracionBtn")
    assert boton is not None and boton.find_parent(id="radarProcesosPanel") is not None
    assert boton.get_text(strip=True) == "Pausar"
    # Rediseño E2: botón de componentes.css (btn-sm, 13 px) en una fila .proc-control que envuelve (radar.css)
    assert boton["type"] == "button" and {"btn", "btn-sm"} <= set(boton["class"])
    assert "proc-control" in boton.parent["class"]
    css = Path("static/css/radar.css").read_text(encoding="utf-8")
    regla = css[css.index(".proc-control {"):]
    assert "flex-wrap: wrap" in regla[:regla.index("}")]
    assert not {"text-zinc-500", "text-zinc-600"}.intersection(boton["class"])


def test_status_actualiza_boton_y_franja_de_pausa():
    js = json.dumps(str(Path("static/radar.js").resolve()))
    resultado = _run_node(r"""
global.window = global;
const nodos = {radarCola: {}, radarGeneracionBtn: {}};
global.document = {addEventListener() {}, getElementById(id) {return nodos[id] || null;}};
global.setTimeout = () => 0;
require(RUTA_JS);
const vistas = [];
for (const pausada of [true, false, true]) {
  window.Radar.onStatus({generacion_pausada: pausada});
  vistas.push([nodos.radarGeneracionBtn.textContent, nodos.radarCola.textContent]);
}
console.log(JSON.stringify(vistas));
""".replace("RUTA_JS", js))
    assert resultado == [
        ["Reanudar", "Generación de CV en pausa"],
        ["Pausar", "Nada en curso · Ver procesos"],  # §8
        ["Reanudar", "Generación de CV en pausa"],
    ]
