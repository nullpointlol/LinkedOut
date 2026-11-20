"""T5: ingesta manual, motor compartido, historial y aislamiento de la vista Radar."""
import asyncio
import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient

import server
from core import database, radar_store
from core.radar import RadarLoop, es_solo_url
from test_radar_ui import _node


@pytest.fixture
def api(monkeypatch):
    radar = Mock(wake_processing=AsyncMock())
    monkeypatch.setattr(server, "radar_loop", radar)
    monkeypatch.setattr(server, "run_pipeline_task", AsyncMock())
    monkeypatch.setattr(server, "scrape_job_url", AsyncMock(side_effect=AssertionError("scraping en request")))
    return TestClient(server.app), radar


def motor(**kwargs):
    return RadarLoop(
        harvester_factory=Mock(side_effect=AssertionError("sin captura real")),
        triage_fn=kwargs.pop("triage_fn", AsyncMock()),
        analyze_fn=AsyncMock(return_value={"match_score": 10, "oferta_cerrada": True}),
        pipeline_fn=AsyncMock(return_value=123),
        compose_fn=AsyncMock(return_value={"to": "rrhh@acme.test", "subject": "QA", "body": "Hola"}),
        send_fn=AsyncMock(side_effect=AssertionError("sin aprobación")),
        broadcast=AsyncMock(), **kwargs,
    )


@pytest.mark.parametrize("tab", ["manual", "url", "batch"])
def test_modal_no_selecciona_historial_con_id_de_post(tab):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node no disponible")
    js = Path("static/app.js").read_text(encoding="utf-8")
    enviar = js.split("async function enviarNuevaOferta()", 1)[1].split(
        "const cerrarModalNuevaOferta", 1
    )[0]
    script = """
      let currentModalTab = TAB;
      let seleccion = 77;
      let cerrado = false;
      let vistaCambiada = null;
      let postAbierto = null;
      const document = {getElementById: () => ({value: 'https://empleos.test/1'})};
      const cerrarModalNueva = () => { cerrado = true; };
      const cerrarModalNuevaOferta = cerrarModalNueva;
      const cambiarVista = (v) => { vistaCambiada = v; };
      const window = { Radar: { abrirPost: (id) => { postAbierto = id; } } };
      const cargarOfertas = async (id) => { if (id != null) seleccion = id; };
      const fetch = async () => ({ok: true, json: async () => ({id: 1, ids: [1]})});
      const alert = texto => { throw new Error(texto); };
    """.replace("TAB", json.dumps(tab))
    script += "async function enviarNuevaOferta()" + enviar
    script += """
      enviarNuevaOferta().then(() => console.log(JSON.stringify({seleccion, cerrado, vistaCambiada, postAbierto})));
    """
    result = subprocess.run([node, "-e", script], capture_output=True, text=True, check=True)
    salida = json.loads(result.stdout.strip().splitlines()[-1])
    assert salida["seleccion"] == 77, "El id del post no debe seleccionar una postulación histórica"
    assert salida["cerrado"]
    assert salida["vistaCambiada"] == "manual"
    assert salida["postAbierto"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("entrada", ["resume_if_needed", "wake_processing"])
@pytest.mark.parametrize("estado", ["ANALIZADA", "NUEVO", "OFERTA"])
async def test_manual_pendiente_se_recupera_con_radar_detenido(entrada, estado):
    radar_store.update_radar_state(desired_running=False, umbral_match=100, backlog_activo=False)
    pid = radar_store.insert_post_manual("Buscamos QA")
    radar_store.update_post(
        pid, estado=estado, prioridad=0,
        analisis={"match_score": 10, "oferta_cerrada": True} if estado == "ANALIZADA" else None,
    )
    listo = asyncio.Event()
    loop = motor(triage_fn=AsyncMock(return_value={pid: {"es_busqueda_laboral": False}}))

    async def al_emitir(nombre, payload):
        if nombre == "radar_post" and payload["id"] == pid and payload["estado"] == "LISTO":
            listo.set()

    loop._broadcast.side_effect = al_emitir
    try:
        await getattr(loop, entrada)()

        await asyncio.wait_for(listo.wait(), 30)
        post = radar_store.get_post(pid)
        assert post["gen_prioridad"] == 2
        assert post["analisis"]["match_score"] == 10
        assert post["aprobado_at"] is None
        assert not loop.is_running
        assert not radar_store.get_radar_state()["desired_running"]
        assert radar_store.get_radar_state()["contadores_dia"]["listos"] == 0
        if estado == "NUEVO":
            assert post["triage"] == {"es_busqueda_laboral": False}
    finally:
        await loop.stop_for_shutdown()


def test_insert_post_manual_extrae_canales():
    pid = radar_store.insert_post_manual(
        "Buscamos QA. rrhh@acme.test https://example.com/jobs/1 WhatsApp +54 9 11 1234 5678",
        "https://example.com/jobs/2", titulo="QA", empresa="Acme",
    )
    p = radar_store.get_post(pid)
    assert p["fuente"] == "manual" and p["urn"].startswith("manual:")
    assert p["query"] == "manual" and p["prioridad"] == 1 and p["estado"] == "NUEVO"
    assert p["autor"] == "Acme" and p["cargo_autor"] == "QA"
    assert p["emails"] == ["rrhh@acme.test"] and p["links"] and p["whatsapp"]
    assert p["post_url"] == "https://example.com/jobs/2"
    assert radar_store.existe_texto(p["texto"])


@pytest.mark.asyncio
async def test_triage_manual_nunca_borra_ni_descarta():
    loop = motor()
    for resultado in ({"es_busqueda_laboral": False}, {"es_busqueda_laboral": True, "alcance": "EXTERIOR"}):
        pid = radar_store.insert_post_manual(str(resultado))
        await loop._aplicar_clasificacion(radar_store.get_post(pid), resultado)
        p = radar_store.get_post(pid)
        assert p["estado"] == "OFERTA" and p["triage"] == resultado
    assert radar_store.get_radar_state()["contadores_dia"]["match"] == 0


@pytest.mark.asyncio
async def test_manual_genera_cv_sin_umbral_con_el_radar_detenido():
    radar_store.update_radar_state(umbral_match=100, backlog_activo=False)
    pid = radar_store.insert_post_manual("Buscamos QA")
    listo = asyncio.Event()
    loop = motor(triage_fn=AsyncMock(return_value={pid: {"es_busqueda_laboral": False}}))

    async def al_emitir(nombre, payload):
        if nombre == "radar_post" and payload["id"] == pid and payload["estado"] == "LISTO":
            listo.set()

    loop._broadcast.side_effect = al_emitir
    try:
        await loop.wake_processing()
        await asyncio.wait_for(listo.wait(), 30)
        p = radar_store.get_post(pid)
        assert not loop.is_running and p["gen_prioridad"] == 2
        assert p["aprobado_at"] is None
        assert radar_store.get_radar_state()["contadores_dia"]["listos"] == 0
        loop._send_fn.assert_not_awaited()
        estados = [call.args[1]["estado"] for call in loop._broadcast.await_args_list
                   if call.args[0] == "radar_post"]
        assert all(e in estados for e in ("OFERTA", "ANALIZANDO", "ANALIZADA", "GENERANDO", "LISTO"))
    finally:
        await loop.stop_for_shutdown()


@pytest.mark.asyncio
async def test_manual_solo_url_se_lee_antes_de_clasificar():
    pid = radar_store.insert_post_manual("https://example.com/job", "https://example.com/job")
    texto = "Buscamos QA con experiencia en Python. " * 10
    ingest = AsyncMock(return_value={"texto": texto, "cargo_autor": "QA"})
    triage = AsyncMock(return_value={pid: {"es_busqueda_laboral": True}})
    loop = motor(ingest_fn=ingest, triage_fn=triage)
    await loop._clasificar([radar_store.get_post(pid)])
    assert triage.await_args.args[0][0]["texto"] == texto
    assert radar_store.get_post(pid)["texto"] == texto
    fallido = radar_store.insert_post_manual("https://example.com/falla")
    normal = radar_store.insert_post_manual("Otro texto completo")
    ingest.side_effect = RuntimeError("no disponible")
    triage.return_value = {normal: {"es_busqueda_laboral": True}}
    await loop._clasificar([radar_store.get_post(fallido), radar_store.get_post(normal)])
    assert radar_store.get_post(fallido)["intentos"] == 1
    assert "No se pudo leer la página" in radar_store.get_post(fallido)["error"]
    assert [p["id"] for p in triage.await_args.args[0]] == [normal]


@pytest.mark.parametrize("path,payload", [
    ("", {"contenido": "Buscamos QA"}),
    ("/url", {"url": "https://example.com/job"}),
])
def test_endpoints_crean_posts_manuales(api, path, payload):
    client, radar = api
    r = client.post("/api/ofertas" + path, json=payload)
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "PROCESSING" and d["estado"] == "NUEVO"
    assert d["id"] == d["post_id"] and not d["duplicado"]
    assert d["titulo"] and d["empresa"] and d["puesto"] == d["titulo"]
    assert isinstance(d["url"], str)
    assert radar_store.get_post(d["id"])["fuente"] == "manual"
    radar.wake_processing.assert_awaited_once()
    assert database.list_postulaciones(origen=None) == []
    server.scrape_job_url.assert_not_awaited()
    server.run_pipeline_task.assert_not_awaited()


def test_batch_crea_un_post_por_url(api):
    client, radar = api
    urls = ["https://example.com/1", "https://example.com/2"]
    d = client.post("/api/ofertas/batch", json={"urls": urls + ["invalida"]}).json()
    assert d["created"] == d["total"] == 2 and d["status"] == "PROCESSING"
    assert sorted(radar_store.get_post(i)["post_url"] for i in d["ids"]) == urls
    radar.wake_processing.assert_awaited_once()
    assert database.list_postulaciones(origen=None) == []


@pytest.mark.parametrize("segundo", [{"contenido": "  Buscamos   QA\n"}, {"contenido": "Otra descripción", "url": "https://example.com/1"}])
def test_oferta_manual_duplicada_devuelve_la_existente(api, segundo):
    client, _ = api
    d = client.post("/api/ofertas", json={"contenido": "Buscamos QA", "url": "https://example.com/1"}).json()
    otro = client.post("/api/ofertas", json=segundo).json()
    assert otro["id"] == d["id"] and otro["duplicado"] is True
    assert len(radar_store.list_posts(fuente="manual")) == 1


def test_get_ofertas_solo_historial_manual(api):
    manual = database.create_postulacion("QA", "Acme", "Oferta", "")
    database.create_postulacion("QA", "Acme", "Otra oferta", "", origen="linkedin")
    assert [p["id"] for p in api[0].get("/api/ofertas").json()] == [manual]


def test_radar_posts_excluye_manuales(api):
    manual = radar_store.insert_post_manual("Buscamos QA")
    otro = radar_store.insert_post_if_new({"urn": "urn:1", "texto": "Buscamos dev"}, "consulta")
    client, _ = api
    assert [p["id"] for p in client.get("/api/radar/posts").json()] == [otro]
    assert [p["id"] for p in client.get("/api/radar/posts?fuente=manual").json()] == [manual]
    radar_store.update_post(manual, triage={"alcance": "EXTERIOR"})
    assert client.get("/api/radar/posts?alcance=EXTERIOR").json() == []


def test_radar_ignora_eventos_de_posts_manuales():
    assert _node("""
      Radar.onPost({id: 9, fuente: 'manual', estado: 'NUEVO'});
      console.log(JSON.stringify(Radar._test.S.posts.has(9)));
    """) is False


@pytest.mark.parametrize("texto,esperado", [
    ("https://empleos.test/1", True), ("http://empleos.test/1", True),
    ("https://empleos.test/1\notra línea", False), ("https://empleos.test/1 más texto", False),
    ("ftp://empleos.test/1", False), ("", False),
])
def test_es_solo_url(texto, esperado):
    assert es_solo_url(texto) is esperado


@pytest.mark.parametrize("path,payload", [
    ("/url", {"url": "ftp://empleos.test/1"}),
    ("/batch", {"urls": ["ftp://empleos.test/1", "invalida"]}),
])
def test_ingesta_invalida_no_despierta_motor(api, path, payload):
    client, radar = api
    assert client.post("/api/ofertas" + path, json=payload).status_code == 400
    assert radar_store.list_posts() == []
    radar.wake_processing.assert_not_awaited()


def test_manual_no_deduplica_con_feed_y_batch_no_duplica(api):
    client, _ = api
    radar_store.insert_post_if_new({"urn": "urn:feed", "texto": "Buscamos QA"}, "feed", fuente="feed")
    manual = client.post("/api/ofertas", json={"contenido": "Buscamos QA"}).json()
    assert not manual["duplicado"]
    url = "https://empleos.test/1"
    lote = client.post("/api/ofertas/batch", json={"urls": [url, url]}).json()
    assert lote["created"] == 1 and lote["total"] == 2
    assert lote["ids"][0] == lote["ids"][1]


@pytest.mark.asyncio
async def test_ingestar_manual_lee_pagina_completa_y_extrae_canales(monkeypatch):
    texto = ("Buscamos QA con experiencia. " * 500) + " rrhh@acme.test https://acme.test/jobs WhatsApp +54 9 11 1234 5678"
    scraper = AsyncMock(return_value={"contenido": texto, "titulo": "QA", "empresa": "Acme"})
    monkeypatch.setattr(server, "scrape_job_url", scraper)
    assert server._build_radar_loop()._ingest_fn is server._radar_ingestar_manual
    pid = radar_store.insert_post_manual("https://acme.test/1", "https://acme.test/1")
    post = radar_store.get_post(pid)
    resultado = await server._radar_ingestar_manual(post)
    assert resultado["texto"] == texto and resultado["cargo_autor"] == "QA" and resultado["autor"] == "Acme"
    assert resultado["emails"] == ["rrhh@acme.test"] and resultado["links"] and resultado["whatsapp"]
    scraper.assert_awaited_once_with("https://acme.test/1")
    for pagina in (None, {}, {"contenido": "  "}, {"error": "no disponible", "contenido": "bloqueado"}):
        scraper.return_value = pagina
        with pytest.raises(RuntimeError, match="No se pudo leer la página"):
            await server._radar_ingestar_manual(post)


@pytest.mark.parametrize("estado,extra", [("ANALIZADA", {}), ("ERROR", {"error": "Falló la generación: prueba", "intentos": 1})])
def test_backlog_no_toma_manuales_y_procesos_muestra_trabajos(api, estado, extra):
    client, radar = api
    pid = radar_store.insert_post_manual("Buscamos QA")
    radar_store.update_post(pid, estado=estado, **extra)
    assert radar_store.backlog_pendiente() == []
    assert not radar_store.tomar_backlog(pid)
    radar.generando_ahora = None
    radar.en_vuelo = set()
    procesos = client.get("/api/radar/procesos").json()
    assert procesos["backlog"]["items"] == []
    radar_store.update_post(pid, estado="ANALIZANDO")
    radar.en_vuelo = {pid}
    procesos = client.get("/api/radar/procesos").json()
    assert [p["post_id"] for p in procesos["en_curso"]] == [pid]
    radar_store.update_post(pid, estado="GENERANDO", gen_prioridad=2)
    radar.en_vuelo = set()
    procesos = client.get("/api/radar/procesos").json()
    assert [p["post_id"] for p in procesos["cola_generacion"]] == [pid]


@pytest.mark.asyncio
async def test_motor_manual_conserva_origen_y_referencia(monkeypatch):
    async def pipeline(pid):
        database.update_postulacion(pid, status="READY")
    monkeypatch.setattr(server, "run_pipeline_task", pipeline)
    post_id = radar_store.insert_post_manual("Buscamos QA", titulo="QA", empresa="Acme")
    post = radar_store.get_post(post_id)
    pid = await server._radar_pipeline(post)
    p = database.get_postulacion(pid)
    assert p["origen"] == "linkedin" and p["radar_post_ref"] == post["urn"]
    assert await server._radar_pipeline(post) == pid


@pytest.mark.asyncio
async def test_manual_comparte_tope_de_envios_y_exige_aprobacion(monkeypatch):
    from tests import reglas_prueba
    reglas_prueba.usar(monkeypatch, reglas_prueba.reglas(tope_envios_dia=1))
    loop = motor()
    loop._send_fn = AsyncMock(return_value="message-id")
    ids = [radar_store.insert_post_manual("QA"), radar_store.insert_post_if_new({"urn": "urn:busqueda", "texto": "Dev"}, "consulta")]
    for pid in ids:
        radar_store.update_post(pid, estado="LISTO", email_to="rrhh@acme.test", email_body="Hola", canal="email")
    assert not await loop._procesar_envio(ids[0])
    loop._send_fn.assert_not_awaited()
    for pid in ids:
        radar_store.update_post(pid, aprobado_at="2026-11-05 12:00:00")
    assert await loop._procesar_envio(ids[0])
    assert not await loop._procesar_envio(ids[1])
    assert radar_store.get_radar_state()["contadores_dia"]["enviados"] == 1


def test_manual_suma_postulados(api):
    client, radar = api
    radar.notify_post = AsyncMock()
    pid = radar_store.insert_post_manual("Buscamos QA")
    radar_store.update_post(pid, estado="LISTO", canal="link", destino_canal="https://acme.test/1")
    assert client.post(f"/api/radar/posts/{pid}/marcar-postulado").status_code == 200
    counters = radar_store.get_radar_state()["contadores_dia"]
    assert counters["postulados"] == 1 and counters["enviados"] == 0


@pytest.mark.parametrize("url,esperada", [
    # WorkMundi muestra un resumen de un aviso de LinkedIn: se lee el aviso completo (oferta 7759)
    ("https://www.workmundi.com/vaga/bGlua2VkaW46NDQ4NzA1MDM1MQ", "https://www.linkedin.com/jobs/view/4487050351/"),
    ("https://www.workmundi.com/vaga/xyz", "https://www.workmundi.com/vaga/xyz"),
    ("https://example.com/job", "https://example.com/job"),
])
def test_url_de_lectura_lee_el_aviso_de_linkedin_detras_de_workmundi(url, esperada):
    from core import scraper
    assert scraper.url_de_lectura(url) == esperada


@pytest.mark.asyncio
async def test_scrape_job_url_lee_la_url_de_lectura(monkeypatch):
    from core import scraper
    leidas = []
    monkeypatch.setattr(scraper, "_fetch_url_sync",
                        lambda url, plazo: leidas.append(url) or "<html><title>QA</title><body>Buscamos QA</body></html>")
    await scraper.scrape_job_url("https://www.workmundi.com/vaga/bGlua2VkaW46NDQ4NzA1MDM1MQ")
    assert leidas == ["https://www.linkedin.com/jobs/view/4487050351/"]


@pytest.mark.asyncio
async def test_una_oferta_borrada_avisa_que_ya_no_existe_sin_reintentar(monkeypatch):
    # oferta 7764: el aviso de LinkedIn ya no existe (404); pedir el texto no sirve
    from core.scraper import OfertaInexistente
    monkeypatch.setattr(server, "scrape_job_url",
                        AsyncMock(return_value={"error": "HTTP Error 404: Not Found", "contenido": "Contenido importado"}))
    with pytest.raises(OfertaInexistente, match="ya no existe"):
        await server._radar_ingestar_manual({"texto": "https://acme.test/1", "post_url": "https://acme.test/1"})
    pid = radar_store.insert_post_manual("https://acme.test/2", "https://acme.test/2")
    await motor(ingest_fn=server._radar_ingestar_manual)._clasificar([radar_store.get_post(pid)])
    post = radar_store.get_post(pid)
    assert post["estado"] == "ERROR" and "ya no existe" in post["error"]
