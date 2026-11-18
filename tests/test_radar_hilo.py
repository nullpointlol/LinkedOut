"""El Radar en su propio hilo y event loop (`core/radar_hilo.py`, 12/11).

- Lo async del Radar corre en el hilo del Radar y devuelve su resultado o su excepción al llamador.
- El loop del portal no se frena mientras el Radar hace trabajo síncrono (SQLite).
- Los eventos que el Radar emite desde su hilo llegan al SSE del portal, en orden.
- Detener o reanudar desde el panel y apagar el portal cierran todo: Edge cerrado, ningún post en
  ANALIZANDO ni GENERANDO a medias, ninguna tarea viva y el hilo terminado al apagar.
Sin red, LinkedIn ni AGY reales.
"""
import asyncio
import threading
import time
from types import SimpleNamespace

import httpx
import pytest
import uvicorn

import server
from core import radar_store
from core.bridge import AntigravityBridge
from core.radar import RadarLoop
from core.radar_hilo import RadarEnHilo
from tests import test_e2e_radar_envio as e2e
from tests.test_ui_navegador import _puerto_libre

pytestmark = pytest.mark.timeout(180)
GUARDA = 60


class _RadarDePrueba:
    """Doble mínimo con la forma del RadarLoop: registra en qué hilo corre cada cosa."""

    def __init__(self):
        self.hilos = []
        self.estado = "listo"
        self.cerrado = False

    async def trabajar(self, valor):
        self.hilos.append(threading.current_thread().name)
        await asyncio.sleep(0)
        return valor * 2

    async def fallar(self):
        raise ValueError("falla del radar")

    async def bloquear(self, segundos):
        time.sleep(segundos)  # SQLite síncrono largo
        return "hecho"

    async def stop_for_shutdown(self):
        self.hilos.append(threading.current_thread().name)
        self.cerrado = True

    def leer(self):
        return self.estado


@pytest.mark.asyncio
async def test_lo_async_corre_en_el_hilo_del_radar_y_devuelve_el_resultado():
    doble = _RadarDePrueba()
    hilo = RadarEnHilo(doble)
    try:
        assert await hilo.trabajar(21) == 42
        assert doble.hilos == ["radar-loop"]
        assert hilo.hilo_vivo and hilo.loop is not asyncio.get_running_loop()
        with pytest.raises(ValueError, match="falla del radar"):
            await hilo.fallar()
        assert hilo.leer() == "listo"  # lo síncrono se lee directo
    finally:
        await hilo.cerrar()
    assert doble.cerrado and doble.hilos[-1] == "radar-loop"
    assert not hilo.hilo_vivo and hilo.loop is None


@pytest.mark.asyncio
async def test_sin_hilo_cerrar_no_arranca_ninguno():
    doble = _RadarDePrueba()
    hilo = RadarEnHilo(doble)
    await hilo.cerrar()
    assert doble.cerrado and doble.hilos == [threading.current_thread().name]
    assert not hilo.hilo_vivo


@pytest.mark.asyncio
async def test_el_loop_del_portal_no_se_frena_con_trabajo_sincrono_del_radar():
    hilo = RadarEnHilo(_RadarDePrueba())
    atrasos = []

    async def latir():
        while True:
            inicio = time.perf_counter()
            await asyncio.sleep(0.05)
            atrasos.append(time.perf_counter() - inicio - 0.05)

    latido = asyncio.create_task(latir())
    try:
        assert await hilo.bloquear(2.0) == "hecho"
    finally:
        latido.cancel()
        await hilo.cerrar()
    assert len(atrasos) >= 20
    assert max(atrasos) < 0.2, max(atrasos)


@pytest.mark.asyncio
async def test_un_evento_emitido_desde_el_hilo_del_radar_llega_al_sse_en_orden():
    portal = threading.current_thread()

    class Cola(asyncio.Queue):
        """Una asyncio.Queue no es segura entre hilos: se exige que se escriba desde el hilo del portal."""
        def put_nowait(self, item):
            assert threading.current_thread() is portal, "la cola del SSE se tocó desde otro hilo"
            super().put_nowait(item)

    cola = Cola()
    server._LOOPS_SSE[cola] = asyncio.get_running_loop()
    server.subscribers.append(cola)

    class Emisor:
        async def emitir(self, n):
            for i in range(n):
                await server.broadcast_event("radar_status", {"orden": i})

        async def stop_for_shutdown(self):
            pass

    hilo = RadarEnHilo(Emisor())
    try:
        await hilo.emitir(5)
        recibidos = [await asyncio.wait_for(cola.get(), GUARDA) for _ in range(5)]
    finally:
        server.subscribers.remove(cola)
        await hilo.cerrar()
    assert [f'"orden": {i}' in m for i, m in enumerate(recibidos)] == [True] * 5
    assert cola.qsize() == 0


@pytest.mark.asyncio
async def test_un_evento_del_portal_descarta_el_borrador_del_loop_del_radar(monkeypatch):
    class Guardar:
        async def guardar(self):
            server._borradores_radar()[(7, 1)] = {"borrador": True}

        async def stop_for_shutdown(self):
            pass

    hilo = RadarEnHilo(Guardar())
    monkeypatch.setattr(server, "radar_loop", hilo)
    try:
        await hilo.guardar()
        assert (7, 1) in hilo.loop._linkedout_borradores
        await server.broadcast_event("radar_post", {"id": 7, "estado": "LISTO"})  # desde el portal
        assert (7, 1) not in hilo.loop._linkedout_borradores
    finally:
        await hilo.cerrar()


class _Harvester(e2e.HarvesterFalso):
    """Navegador falso: cuenta cuántos Edge quedan abiertos (uno por harvester creado, hasta su `close`)."""
    abiertos = 0
    creados = 0

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.cerrado = False
        type(self).abiertos += 1
        type(self).creados += 1

    async def close(self):
        if not self.cerrado:
            self.cerrado = True
            type(self).abiertos -= 1

    async def harvest_feed(self, max_posts=40, max_scrolls=10, conocidos=None):
        return SimpleNamespace(posts=[], needs_login=False, html="", lectura={"motivo_fin": "fin"})


def _contar(estado):
    return len(radar_store.list_posts(estado=estado))


def test_detener_reanudar_y_apagar_cierran_todo(monkeypatch, tmp_path):
    """Con el portal real y el Radar real en su hilo: un análisis en curso se corta al detener desde el
    panel, al reanudar sigue y al apagar el portal no queda Edge abierto, ni posts a medias, ni el hilo."""
    monkeypatch.setenv("LINKEDOUT_SIN_RADAR", "1")
    for nombre in ("SMTP_SSL", "SMTP"):
        monkeypatch.setattr(f"core.mailer.smtplib.{nombre}", e2e._prohibido)
    monkeypatch.setattr(server, "scrape_job_url", e2e._prohibido)
    _Harvester.abiertos = _Harvester.creados = 0

    analizando = threading.Event()
    soltar = threading.Event()
    ia = e2e._ia_por_etapa([])

    async def run_prompt(self, prompt, json_mode=True, **kw):
        if "anios_por_area" in prompt:
            return {"anios_por_area": []}
        if "OFERTA LABORAL A ANALIZAR" in prompt:
            analizando.set()
            while not soltar.is_set():
                await asyncio.sleep(0.05)
        return await ia(self, prompt, json_mode=json_mode, **kw)

    monkeypatch.setattr(AntigravityBridge, "run_prompt", run_prompt)
    pid = radar_store.insert_post_if_new({"urn": "urn:li:activity:9800", "url": "https://example.org/9800",
                                          "autor": "Acme", "texto": e2e.TEXTO_POST,
                                          "emails": ["rrhh@acme.test"]}, "consulta de prueba")
    radar_store.update_post(pid, estado="OFERTA", triage={"oferta": 1, "puesto": e2e.PUESTO, "canal": "email"})
    radar_store.update_radar_state(generacion_pausada=True)

    radar = RadarLoop(
        harvester_factory=_Harvester, triage_fn=server.triage_batch, ingest_fn=server._radar_ingestar_manual,
        analyze_fn=server._radar_analyze, pipeline_fn=server._radar_pipeline, compose_fn=server._radar_compose,
        send_fn=server._radar_send, broadcast=server.broadcast_event, prepare_fn=server._radar_preparar_analisis,
        queries=[], debug_dir=tmp_path / "radar_debug", perfil_secundario=tmp_path / "sin_cuenta_2",
    )
    hilo = RadarEnHilo(radar)
    monkeypatch.setattr(server, "radar_loop", hilo)

    puerto = _puerto_libre()
    servidor = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=puerto, log_level="warning",
                                             lifespan="on", timeout_graceful_shutdown=5))
    corrida = threading.Thread(target=servidor.run, daemon=True)
    corrida.start()
    limite = time.monotonic() + GUARDA
    while not servidor.started:
        assert time.monotonic() < limite and corrida.is_alive(), "el portal de prueba no arrancó"
        time.sleep(0.05)
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{puerto}", timeout=GUARDA) as api:
            # 1) Arranca desde el panel: el Radar corre en su hilo y analiza
            assert api.post("/api/radar/start").status_code == 200
            assert analizando.wait(GUARDA), "el Radar no llegó a analizar"
            assert hilo.hilo_vivo and _contar("ANALIZANDO") == 1
            assert _Harvester.creados >= 1  # el Radar abrió su navegador (falso)

            # 2) Detener desde el panel con el análisis en curso: vuelve a OFERTA, Edge cerrado, sin tareas
            assert api.post("/api/radar/stop").status_code == 200  # responde ya; detiene en segundo plano
            e2e._esperar(lambda: not radar.is_running and radar_store.get_radar_state()["estado"] == "DETENIDO",
                         GUARDA, "DETENIDO")
            assert _contar("ANALIZANDO") == 0 and radar_store.get_post(pid)["estado"] == "OFERTA"
            assert _contar("GENERANDO") == 0
            assert _Harvester.abiertos == 0
            assert not radar.is_running
            assert api.get("/api/radar/status").json()["estado"] == "DETENIDO"

            # 3) Reanudar desde el panel: el análisis sigue y termina
            analizando.clear()
            assert api.post("/api/radar/start").status_code == 200
            assert analizando.wait(GUARDA), "al reanudar no volvió a analizar"
            soltar.set()
            e2e._esperar(lambda: radar_store.get_post(pid)["estado"] == "ANALIZADA", GUARDA, "ANALIZADA")
    finally:
        soltar.set()
        # 4) Apagar el portal con el Radar corriendo
        servidor.should_exit = True
        corrida.join(timeout=GUARDA)
    assert not corrida.is_alive()
    assert not hilo.hilo_vivo, "el hilo del Radar siguió vivo después de apagar el portal"
    assert _Harvester.abiertos == 0, "quedó Edge abierto"
    assert _contar("ANALIZANDO") == 0 and _contar("GENERANDO") == 0
    assert radar_store.get_radar_state()["desired_running"]  # se reanuda en el próximo arranque


def test_detener_con_agy_en_curso_y_edge_colgado_no_bloquea_el_portal(monkeypatch, tmp_path):
    """Producción 12/11: tras un stop el portal no respondió 6 min. Con una llamada a AGY en curso y un Edge
    que no cierra, status responde en menos de 1 s todo el tiempo y el stop termina dentro del plazo."""
    from core import linkedin_harvester
    from core.linkedin_harvester import LinkedInHarvester

    monkeypatch.setenv("LINKEDOUT_SIN_RADAR", "1")
    for nombre in ("SMTP_SSL", "SMTP"):
        monkeypatch.setattr(f"core.mailer.smtplib.{nombre}", e2e._prohibido)
    monkeypatch.setattr(server, "scrape_job_url", e2e._prohibido)
    monkeypatch.setattr(linkedin_harvester, "PLAZO_CIERRE_NAVEGADOR_S", 2.0)
    colgado = 12.0
    liberar = threading.Event()
    abiertos = []

    class EdgeColgado(LinkedInHarvester):
        def __init__(self, *a, **kw):
            super().__init__(profile_dir=tmp_path / "perfil")

        async def harvest_feed(self, max_posts=40, max_scrolls=10, conocidos=None):
            await self._run(lambda: abiertos.append(threading.current_thread().name))  # Edge abierto
            return SimpleNamespace(posts=[], needs_login=False, html="", lectura={"motivo_fin": "fin"})

        async def check_logged_in(self):
            return True

        def _close_sync(self):
            liberar.wait(colgado)  # context.close() que no vuelve

    analizando = threading.Event()
    ia = e2e._ia_por_etapa([])

    async def run_prompt(self, prompt, json_mode=True, **kw):
        if "anios_por_area" in prompt:
            return {"anios_por_area": []}
        if "OFERTA LABORAL A ANALIZAR" in prompt:
            analizando.set()
            await asyncio.sleep(3600)  # AGY que no termina: el stop la cancela
        return await ia(self, prompt, json_mode=json_mode, **kw)

    monkeypatch.setattr(AntigravityBridge, "run_prompt", run_prompt)
    pid = radar_store.insert_post_if_new({"urn": "urn:li:activity:9850", "url": "https://example.org/9850",
                                          "autor": "Acme", "texto": e2e.TEXTO_POST,
                                          "emails": ["rrhh@acme.test"]}, "consulta de prueba")
    radar_store.update_post(pid, estado="OFERTA", triage={"oferta": 1, "puesto": e2e.PUESTO, "canal": "email"})
    radar_store.update_radar_state(generacion_pausada=True)
    radar = RadarLoop(
        harvester_factory=EdgeColgado, triage_fn=server.triage_batch, ingest_fn=server._radar_ingestar_manual,
        analyze_fn=server._radar_analyze, pipeline_fn=server._radar_pipeline, compose_fn=server._radar_compose,
        send_fn=server._radar_send, broadcast=server.broadcast_event, prepare_fn=server._radar_preparar_analisis,
        queries=[], debug_dir=tmp_path / "radar_debug", perfil_secundario=tmp_path / "sin_cuenta_2",
    )
    hilo = RadarEnHilo(radar)
    monkeypatch.setattr(server, "radar_loop", hilo)
    puerto = _puerto_libre()
    servidor = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=puerto, log_level="warning",
                                             lifespan="on", timeout_graceful_shutdown=5))
    corrida = threading.Thread(target=servidor.run, daemon=True)
    corrida.start()
    limite = time.monotonic() + GUARDA
    while not servidor.started:
        assert time.monotonic() < limite and corrida.is_alive(), "el portal de prueba no arrancó"
        time.sleep(0.05)
    latencias = []
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{puerto}", timeout=GUARDA) as api:
            assert api.post("/api/radar/start").status_code == 200
            assert analizando.wait(GUARDA), "el Radar no llegó a llamar a AGY"
            e2e._esperar(lambda: abiertos, GUARDA, "abrir Edge")

            inicio = time.monotonic()
            assert api.post("/api/radar/stop").status_code == 200
            detenido = None
            while time.monotonic() - inicio < GUARDA:
                t = time.monotonic()
                r = api.get("/api/radar/status")
                latencias.append(time.monotonic() - t)
                assert r.status_code == 200
                if r.json()["estado"] == "DETENIDO" and not radar.is_running:
                    detenido = time.monotonic() - inicio
                    break
                time.sleep(0.2)
            assert detenido is not None, "el stop no terminó"
            assert detenido < colgado - 4, f"el stop tardó {detenido:.1f} s: esperó al Edge colgado"
            assert _contar("ANALIZANDO") == 0
    finally:
        liberar.set()
        servidor.should_exit = True
        corrida.join(timeout=GUARDA)
    assert max(latencias) < 1.0, f"status tardó {max(latencias):.2f} s durante el stop"
    assert not hilo.hilo_vivo


@pytest.mark.asyncio
async def test_una_llamada_desde_el_propio_hilo_del_radar_corre_directo():
    """Si el Radar se llama a sí mismo desde su loop, no se reencola: corre ahí mismo."""
    radar = _RadarDePrueba()
    hilo = RadarEnHilo(radar)
    try:
        async def desde_adentro():
            return await hilo.trabajar(3)
        assert await hilo._en_radar(desde_adentro) == 6
        assert radar.hilos == ["radar-loop"]
    finally:
        hilo.cerrar_ya()
