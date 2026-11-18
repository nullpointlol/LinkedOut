"""Las lecturas del portal nunca esperan a una escritura (producción 12/11: «database is locked» en
`GET /api/radar/status` y en `GET /api/radar/posts/{id}`, que no escriben nada).

Causa: cada conexión del Radar corría `init_radar_tables` con un INSERT OR IGNORE y un UPDATE que toman el
lock de escritura aunque no cambien nada, y `get_radar_state` abría siempre BEGIN IMMEDIATE. En WAL una
lectura ve su snapshot sin esperar a nadie; con esas escrituras de más, cada lectura competía como un
escritor y, si el lock seguía tomado más de `busy_timeout` (5 s), daba 500.
"""
import asyncio
import logging
import sqlite3
import threading
import time
from types import SimpleNamespace

import httpx
import pytest
import uvicorn

import server
from core import bridge, database, radar_store, revision_store, tickets
from core.bridge import AntigravityBridge
from core.radar import RadarLoop
from tests import test_e2e_radar_envio as e2e
from tests.test_ui_navegador import _puerto_libre

pytestmark = pytest.mark.timeout(180)

GUARDA = 60          # s: tope de las esperas por condición (con la PC cargada, AGENTS.md)
RETENCION = 6.0      # s: más que busy_timeout (5 s), como una escritura estirada por la carga
LATENCIA_MAX = 1.0   # s: una lectura no espera a la escritura


class _Contadas(sqlite3.Connection):
    """Conexión que cuenta las transacciones que abre (en WAL, una transacción con escritura toma el lock)."""
    abiertas = 0

    def execute(self, *a, **kw):
        antes = self.in_transaction
        cursor = super().execute(*a, **kw)
        if self.in_transaction and not antes:
            type(self).abiertas += 1
        return cursor


@pytest.fixture
def transacciones(monkeypatch):
    original = sqlite3.connect

    def conectar(*a, **kw):
        kw.setdefault("factory", _Contadas)
        return original(*a, **kw)

    monkeypatch.setattr(database.sqlite3, "connect", conectar)
    _Contadas.abiertas = 0
    return _Contadas


def _sembrar_post() -> int:
    pid = radar_store.insert_post_if_new({"urn": "urn:li:activity:9300", "url": "https://example.org/9300",
                                          "autor": "Acme", "texto": e2e.TEXTO_POST,
                                          "emails": ["rrhh@acme.test"]}, "consulta de prueba")
    radar_store.update_post(pid, estado="OFERTA", triage={"oferta": 1, "puesto": e2e.PUESTO, "canal": "email"})
    return pid


@pytest.mark.parametrize("lectura", [
    lambda pid: radar_store.get_post(pid),
    lambda pid: radar_store.get_radar_state(),
    lambda pid: radar_store.resumen_cola(),
    lambda pid: radar_store.list_posts(estado="OFERTA"),
    lambda pid: revision_store.contar_pendientes(),
    lambda pid: bridge.estado_admision(),
], ids=["get_post", "get_radar_state", "resumen_cola", "list_posts", "contar_pendientes", "estado_admision"])
def test_una_lectura_no_abre_transacciones_de_escritura(transacciones, lectura):
    pid = _sembrar_post()
    radar_store.get_radar_state()           # base ya inicializada y contadores de hoy
    revision_store.contar_pendientes()
    bridge.estado_admision()                # historial del modo serie ya al día
    transacciones.abiertas = 0
    lectura(pid)
    assert transacciones.abiertas == 0


def test_el_reinicio_diario_de_contadores_sigue_escribiendo(monkeypatch):
    """Integridad: leer el estado en un día nuevo sigue reiniciando los contadores (una vez)."""
    radar_store.bump_counter("leidos_busquedas", 3)
    assert radar_store.get_radar_state()["contadores_dia"]["leidos_busquedas"] == 3
    monkeypatch.setattr(radar_store, "_today", lambda: "2099-01-01")
    estado = radar_store.get_radar_state()
    assert estado["contadores_dia"]["leidos_busquedas"] == 0 and estado["fecha_contadores"] == "2099-01-01"
    radar_store.bump_counter("leidos_busquedas")
    assert radar_store.get_radar_state()["contadores_dia"]["leidos_busquedas"] == 1


def test_una_base_nueva_sigue_creando_el_estado_del_radar(tmp_path):
    """Integridad: la primera conexión a una base vacía crea las tablas y la fila de `radar_state`."""
    base = tmp_path / "nueva.db"
    estado = radar_store.get_radar_state(db_path=base)
    assert estado["tope_posts_dia"] == 600 and estado["fecha_contadores"]


class _Escritura:
    """Otra conexión que retiene el lock de escritura `segundos` (un worker o un proceso que escribe)."""

    def __init__(self, segundos: float):
        self.segundos = segundos
        self.tomada = threading.Event()
        self.hilo = threading.Thread(target=self._retener, daemon=True)

    def _retener(self):
        conn = sqlite3.connect(str(database.DB_PATH), timeout=GUARDA)
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("UPDATE radar_state SET estado_detalle = estado_detalle WHERE id = 1")
            self.tomada.set()
            time.sleep(self.segundos)
            conn.commit()
        finally:
            conn.close()

    def __enter__(self):
        self.hilo.start()
        assert self.tomada.wait(GUARDA), "no se pudo tomar el lock de escritura"
        return self

    def __exit__(self, *exc):
        self.hilo.join(GUARDA)


def test_status_responde_con_el_radar_analizando_y_una_escritura_abierta(monkeypatch, tmp_path, caplog):
    """Concurrencia: el Radar real analiza un post (la IA tarda) mientras otra conexión retiene una
    escritura más de 5 s, y el portal real atiende status y el detalle del post: todo 200, rápido y sin
    «database is locked» en los logs ni en los tickets."""
    caplog.set_level(logging.WARNING)
    monkeypatch.setenv("LINKEDOUT_SIN_RADAR", "1")
    monkeypatch.setattr("core.mailer.smtplib.SMTP_SSL", e2e._prohibido)
    monkeypatch.setattr("core.mailer.smtplib.SMTP", e2e._prohibido)
    monkeypatch.setattr(server, "scrape_job_url", e2e._prohibido)
    monkeypatch.setattr(server, "LinkedInHarvester", e2e._prohibido)

    etapas = []
    ia = e2e._ia_por_etapa(etapas)
    analizando = threading.Event()   # el Radar está esperando a la IA
    soltar = threading.Event()       # la IA responde

    async def run_prompt(self, prompt, json_mode=True, **kw):
        if "anios_por_area" in prompt:
            return {"anios_por_area": []}
        if "OFERTA LABORAL A ANALIZAR" in prompt:
            analizando.set()
            limite = time.monotonic() + GUARDA
            while not soltar.is_set() and time.monotonic() < limite:
                await asyncio.sleep(0.05)
        return await ia(self, prompt, json_mode=json_mode, **kw)

    monkeypatch.setattr(AntigravityBridge, "run_prompt", run_prompt)

    class SinLecturas(e2e.HarvesterFalso):
        async def harvest_feed(self, max_posts=40, max_scrolls=10, conocidos=None):
            return SimpleNamespace(posts=[], needs_login=False, html="", lectura={"motivo_fin": "fin"})

    pid = _sembrar_post()
    radar_store.update_radar_state(generacion_pausada=True)  # solo análisis
    radar = RadarLoop(
        harvester_factory=SinLecturas, triage_fn=server.triage_batch, ingest_fn=server._radar_ingestar_manual,
        analyze_fn=server._radar_analyze, pipeline_fn=server._radar_pipeline, compose_fn=server._radar_compose,
        send_fn=server._radar_send, broadcast=server.broadcast_event, prepare_fn=server._radar_preparar_analisis,
        queries=[], debug_dir=tmp_path / "radar_debug", perfil_secundario=tmp_path / "sin_cuenta_2",
    )
    monkeypatch.setattr(server, "radar_loop", radar)

    puerto = _puerto_libre()
    servidor = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=puerto, log_level="warning",
                                             lifespan="on", timeout_graceful_shutdown=5))
    hilo = threading.Thread(target=servidor.run, daemon=True)
    hilo.start()
    limite = time.monotonic() + GUARDA
    while not servidor.started:
        assert time.monotonic() < limite and hilo.is_alive(), "el portal de prueba no arrancó"
        time.sleep(0.05)
    respuestas = []
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{puerto}", timeout=GUARDA) as api:
            assert api.post("/api/radar/start").status_code == 200
            assert analizando.wait(GUARDA), "el Radar no llegó a analizar el post"
            assert radar_store.get_post(pid)["estado"] == "ANALIZANDO"

            with _Escritura(RETENCION):
                fin = time.monotonic() + RETENCION - 1
                while time.monotonic() < fin:
                    for ruta in ("/api/radar/status", f"/api/radar/posts/{pid}", "/api/radar/procesos"):
                        inicio = time.monotonic()
                        r = api.get(ruta)
                        respuestas.append((ruta, r.status_code, time.monotonic() - inicio))
                    time.sleep(0.2)
            soltar.set()

            e2e._esperar(lambda: radar_store.get_post(pid)["estado"] == "ANALIZADA", GUARDA, "ANALIZADA")
            assert api.post("/api/radar/stop").status_code == 200
    finally:
        soltar.set()
        servidor.should_exit = True
        hilo.join(timeout=20)

    assert respuestas
    fallidas = [r for r in respuestas if r[1] != 200]
    assert not fallidas, fallidas
    lentas = [r for r in respuestas if r[2] > LATENCIA_MAX]
    assert not lentas, lentas
    bloqueos = [r.getMessage() for r in caplog.records if "database is locked" in r.getMessage()
                or (r.exc_info and "database is locked" in str(r.exc_info[1]))]
    assert bloqueos == []
    assert not [t for t in tickets.listar() if "database is locked" in (t.get("mensaje") or "")]
