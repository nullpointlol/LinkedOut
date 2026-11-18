"""Portales (14/11): guardado y duplicados, búsquedas por portal, vuelta en el hilo del Radar, API y Mi perfil.

Sin red: las consultas usan las respuestas grabadas de tests/fixtures/portales (con los clientes reales de
core/portales, la red inyectada)."""
import json
import shutil
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient

import core.paths
import server
from core import portales, portales_store, radar_consultas, radar_store, reglas_portales, tickets
from core.portales import buscar_indeed, buscar_jooble
from core.radar import RadarLoop
from core.reglas_portales import ReglaPortal

GRABADAS = Path(__file__).parent / "fixtures" / "portales"
HOY = date(2026, 11, 14)
from tests.portales_prueba import reglas as _reglas_completas

REGLAS = _reglas_completas(
    apagados=("computrabajo", "bumeran", "remoteok", "remotive", "himalayas"),
    jooble={"consultas_por_dia": 3, "minutos_entre_consultas": 15, "ubicacion": "Buenos Aires", "leer_pagina_aviso": True},
    indeed={"consultas_por_dia": 3, "minutos_entre_consultas": 30, "ubicacion": "Buenos Aires, Argentina",
            "resultados_por_consulta": 30})
BUSQUEDAS = {"busquedas": [
    {"nombre": "Análisis", "activa": True, "consulta": "\"cv a\" funcional",
     "portales": ["jooble", "indeed"], "palabras": "analista funcional"},
    {"nombre": "Soporte", "activa": True, "consulta": "\"cv a\" soporte", "portales": ["indeed"],
     "palabras": "mesa de ayuda"},
    {"nombre": "Pausada", "activa": False, "consulta": "\"cv a\" pausada", "portales": ["jooble"],
     "palabras": "pausada"},
    {"nombre": "Solo LinkedIn", "activa": True, "consulta": "\"cv a\" otra"},
]}


def _grabada(nombre):
    return json.loads((GRABADAS / nombre).read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def perfil(tmp_path, monkeypatch):
    candidato = tmp_path / "datos" / "candidato"
    shutil.copytree(core.paths.DATA_DIR / "candidato", candidato)
    monkeypatch.setattr(core.paths, "DATA_DIR", tmp_path / "datos")
    (candidato / "reglas_portales.json").write_text(json.dumps(REGLAS), encoding="utf-8")
    (candidato / "busquedas.json").write_text(json.dumps(BUSQUEDAS, ensure_ascii=False), encoding="utf-8")
    return candidato


def _regla(portal):
    d = REGLAS[portal]
    return ReglaPortal(portal=portal, **{**d, "listados": tuple(d["listados"])})


async def portales_grabados(portal, palabras, regla):
    """La consulta real de core/portales con la red reemplazada por las respuestas grabadas."""
    if portal == "jooble":
        return buscar_jooble(palabras, regla, "clave", post_json=lambda *a: (200, _grabada("jooble_respuesta.json")),
                             hoy=HOY)
    return buscar_indeed(palabras, regla, scrape=lambda **k: _grabada("indeed_filas.json"), hoy=HOY)


class Reloj:
    def __init__(self):
        self.ahora = datetime(2026, 11, 14, 10, 0)

    def __call__(self):
        return self.ahora


def motor(**kwargs):
    return RadarLoop(
        harvester_factory=Mock(side_effect=AssertionError("Portales no usa LinkedIn")),
        triage_fn=kwargs.pop("triage_fn", AsyncMock(return_value={})),
        analyze_fn=AsyncMock(return_value={}), pipeline_fn=AsyncMock(return_value=1),
        compose_fn=AsyncMock(return_value={}), send_fn=AsyncMock(side_effect=AssertionError("sin envíos")),
        broadcast=AsyncMock(), portales_fn=kwargs.pop("portales_fn", portales_grabados), **kwargs,
    )


def _oferta(portal="indeed", id_="1", titulo="Analista Funcional", empresa="Acme", link=None, directo=""):
    return {"portal": portal, "id": id_, "urn": f"{portal}:{id_}", "titulo": titulo, "empresa": empresa,
            "ubicacion": "CABA", "descripcion": "Detalle " * 80, "completa": True,
            "link": link or f"https://{portal}.test/{id_}", "link_directo": directo, "emails": [], "fecha": ""}


# ---------------------------------------------------------------- guardado y duplicados

def test_oferta_nueva_entra_con_su_fuente_y_su_busqueda():
    estado, pid = portales_store.guardar_oferta(_oferta(directo="https://acme.test/jobs/1"), "Análisis")
    assert estado == portales_store.NUEVA
    post = radar_store.get_post(pid)
    assert post["fuente"] == "indeed" and post["query"] == "Análisis" and post["estado"] == "NUEVO"
    assert post["autor"] == "Acme" and post["cargo_autor"] == "Analista Funcional"
    assert post["post_url"] == "https://indeed.test/1"
    assert post["links"] == ["https://acme.test/jobs/1", "https://indeed.test/1"]
    assert post["texto"].startswith("Analista Funcional\n\nAcme · CABA")


def test_la_misma_oferta_no_entra_dos_veces_aunque_se_haya_borrado():
    _, pid = portales_store.guardar_oferta(_oferta(), "Análisis")
    assert portales_store.guardar_oferta(_oferta(), "Soporte") == (portales_store.VISTA, pid)
    radar_store.delete_post(pid)  # no era oferta: se borró
    assert portales_store.guardar_oferta(_oferta(), "Análisis")[0] == portales_store.VISTA
    assert radar_store.list_posts(fuente="indeed") == []


def test_duplicados_por_link_o_por_titulo_y_empresa_se_unen():
    _, pid = portales_store.guardar_oferta(_oferta(directo="https://acme.test/jobs/1"), "Análisis")
    # Otro portal, mismo link directo
    mismo_link = _oferta("jooble", "9", titulo="Otro título", empresa="Otra", directo="https://acme.test/jobs/1/")
    assert portales_store.guardar_oferta(mismo_link, "Análisis") == (portales_store.DUPLICADA, pid)
    # Otro portal, mismo título y empresa con otras tildes y mayúsculas
    mismo_titulo = _oferta("jooble", "10", titulo="ANALISTA  funcional", empresa="acme")
    assert portales_store.guardar_oferta(mismo_titulo, "Análisis") == (portales_store.DUPLICADA, pid)
    assert radar_store.get_post(pid)["links"] == [
        "https://acme.test/jobs/1", "https://indeed.test/1", "https://jooble.test/9", "https://jooble.test/10"]
    # Repetir un duplicado ya unido: vista
    assert portales_store.guardar_oferta(mismo_titulo, "Análisis") == (portales_store.VISTA, pid)
    # Sin empresa no se une por título
    sin_empresa = _oferta("jooble", "11", empresa="")
    assert portales_store.guardar_oferta(sin_empresa, "Análisis")[0] == portales_store.NUEVA


def test_un_urn_que_ya_estaba_en_la_tabla_es_vista():
    radar_store.insert_post_if_new({"urn": "indeed:1", "texto": "x"}, "q", fuente="indeed")
    estado, pid = portales_store.guardar_oferta(_oferta(), "Análisis")
    assert estado == portales_store.VISTA and pid == radar_store.list_posts(fuente="indeed")[0]["id"]


def test_lecturas_cuentan_por_portal_y_desde():
    portales_store.registrar_lectura("jooble", "A", "a", "2026-11-14T10:00:00+00:00", "x", traidas=2)
    portales_store.registrar_lectura("jooble", "B", "b", "2026-11-14T11:00:00+00:00", "x", error="falló")
    portales_store.registrar_lectura("indeed", "A", "a", "2026-11-13T11:00:00+00:00", "x")
    assert portales_store.consultas_desde("jooble", "2026-11-14T00:00:00+00:00") == 2
    assert portales_store.consultas_desde("indeed", "2026-11-14T00:00:00+00:00") == 0
    assert portales_store.ultima_consulta("jooble") == "2026-11-14T11:00:00+00:00"
    assert portales_store.ultimas_por_busqueda("jooble") == {"A": "2026-11-14T10:00:00+00:00",
                                                            "B": "2026-11-14T11:00:00+00:00"}
    assert [l["error"] for l in portales_store.lecturas()] == [None, "falló", None]
    assert portales_store.ahora_iso(datetime(2026, 11, 14, 10, 0, tzinfo=timezone.utc)) == "2026-11-14T10:00:00+00:00"


# ---------------------------------------------------------------- búsquedas

def test_busquedas_de_portal_solo_activas_con_ese_portal():
    assert radar_consultas.busquedas_de_portal("jooble") == [{"nombre": "Análisis", "palabras": "analista funcional"}]
    assert [b["nombre"] for b in radar_consultas.busquedas_de_portal("indeed")] == ["Análisis", "Soporte"]
    # Los campos de portales no cambian la búsqueda de LinkedIn
    assert "\"cv a\" funcional" in radar_consultas.consultas_activas()


@pytest.mark.parametrize("busqueda, error", [
    ({"portales": "jooble", "palabras": "x"}, "«portales» tiene que ser una lista"),
    ({"portales": ["otro"], "palabras": "x"}, "«portales» tiene que ser una lista"),
    ({"portales": ["indeed", "indeed"], "palabras": "x"}, "un portal está repetido"),
    ({"portales": ["indeed"], "palabras": 3}, "«palabras» tiene que ser un texto"),
    ({"portales": ["indeed"], "palabras": "\"analista\""}, "sin comillas"),
    ({"portales": ["indeed"], "palabras": "analista OR funcional"}, "sin AND, OR ni NOT"),
    ({"portales": ["indeed"], "palabras": "x" * 101}, "más de 100"),
    ({"portales": ["indeed"], "palabras": " "}, "faltan las palabras"),
])
def test_palabras_y_portales_se_validan(perfil, busqueda, error):
    datos = {"busquedas": [{"nombre": "A", "activa": True, "consulta": "\"cv a\"", **busqueda}]}
    errores = radar_consultas.validar_busquedas(datos)
    assert any(error in e for e in errores), errores
    # La búsqueda de LinkedIn sigue: un error de portales solo saltea la del portal
    (perfil / "busquedas.json").write_text(json.dumps(datos), encoding="utf-8")
    assert radar_consultas.consultas_activas() == ["\"cv a\""]
    assert radar_consultas.busquedas_de_portal("indeed") == []


def test_sin_archivo_de_busquedas_no_hay_busquedas_de_portal(perfil):
    (perfil / "busquedas.json").unlink()
    assert radar_consultas.busquedas_de_portal("indeed") == []
    assert radar_consultas.portales_de_busquedas() == {}


# ---------------------------------------------------------------- vuelta de Portales

@pytest.mark.asyncio
async def test_vuelta_trae_ofertas_de_los_dos_portales_y_une_duplicados():
    loop = motor(now=Reloj())
    loop._wake_processing = Mock()
    hecho = await loop.vuelta_portales()
    assert hecho["jooble"] == {"busqueda": "Análisis", "traidas": 2, "nuevas": 2, "duplicadas": 0, "error": None}
    # Indeed trae Acme «Analista Funcional | Integraciones», que Jooble ya trajo: se une
    assert hecho["indeed"] == {"busqueda": "Análisis", "traidas": 2, "nuevas": 1, "duplicadas": 1, "error": None}
    assert len(radar_store.list_posts(fuente=("jooble", "indeed"))) == 3
    loop._wake_processing.assert_called()
    lecturas = portales_store.lecturas()
    assert [(l["portal"], l["busqueda"], l["palabras"]) for l in lecturas] == [
        ("indeed", "Análisis", "analista funcional"), ("jooble", "Análisis", "analista funcional")]


@pytest.mark.asyncio
async def test_ritmo_consultas_por_dia_y_rotacion_de_busquedas():
    reloj = Reloj()
    llamadas = []

    async def portales_fn(portal, palabras, regla):
        llamadas.append((portal, palabras))
        return []

    loop = motor(now=reloj, portales_fn=portales_fn)
    await loop.vuelta_portales()
    assert llamadas == [("jooble", "analista funcional"), ("indeed", "analista funcional")]
    reloj.ahora += timedelta(minutes=14)
    await loop.vuelta_portales()
    assert len(llamadas) == 2  # ninguno cumplió su ritmo
    reloj.ahora += timedelta(minutes=1)
    await loop.vuelta_portales()
    assert llamadas[2:] == [("jooble", "analista funcional")]  # Jooble (15 min) sí; Indeed (30 min) no
    reloj.ahora += timedelta(minutes=15)
    await loop.vuelta_portales()
    # Indeed rota a la búsqueda que nunca consultó; Jooble llega a 3 consultas del día
    assert llamadas[3:] == [("jooble", "analista funcional"), ("indeed", "mesa de ayuda")]
    reloj.ahora += timedelta(hours=1)
    await loop.vuelta_portales()
    assert llamadas[5:] == [("indeed", "analista funcional")]  # Jooble agotó su día
    reloj.ahora += timedelta(hours=1)
    await loop.vuelta_portales()
    assert llamadas[6:] == []  # Indeed también


@pytest.mark.asyncio
async def test_portal_con_reglas_invalidas_no_corre_y_abre_ticket(perfil):
    malas = json.loads(json.dumps(REGLAS))
    del malas["indeed"]["consultas_por_dia"]
    malas["jooble"]["activo"] = False
    (perfil / "reglas_portales.json").write_text(json.dumps(malas), encoding="utf-8")
    llamadas = []

    async def portales_fn(portal, palabras, regla):
        llamadas.append(portal)
        return []

    assert await motor(now=Reloj(), portales_fn=portales_fn).vuelta_portales() == {}
    assert llamadas == []  # Indeed sin un valor, Jooble apagado
    abiertos = tickets.listar(componente="portales")
    assert [(t["tipo"], t["mensaje"]) for t in abiertos] == [
        ("reglas_invalidas", "Indeed no corre: reglas_portales.json: Indeed: falta «consultas_por_dia».")]


@pytest.mark.asyncio
async def test_error_del_portal_queda_en_la_lectura_y_en_un_ticket_sin_la_key():
    async def portales_fn(portal, palabras, regla):
        if portal == "jooble":
            return buscar_jooble(palabras, regla, "secreta-123", post_json=lambda *a: (403, None))
        raise RuntimeError("se cortó")

    hecho = await motor(now=Reloj(), portales_fn=portales_fn).vuelta_portales()
    assert "rechazó la key" in hecho["jooble"]["error"]
    assert hecho["indeed"]["error"] == "RuntimeError: se cortó"
    mensajes = [t["mensaje"] for t in tickets.listar(componente="portales")]
    assert any("Jooble: Jooble rechazó la key" in m for m in mensajes)
    assert "Indeed: RuntimeError: se cortó" in mensajes
    assert all("secreta-123" not in json.dumps(t, ensure_ascii=False) for t in tickets.listar())
    assert portales_store.consultas_desde("jooble", "2026-01-01T00:00:00+00:00") == 1


@pytest.mark.asyncio
async def test_sin_busquedas_del_portal_no_consulta(perfil):
    datos = {"busquedas": [{"nombre": "Solo LinkedIn", "activa": True, "consulta": "\"cv a\""}]}
    (perfil / "busquedas.json").write_text(json.dumps(datos), encoding="utf-8")
    portales_fn = AsyncMock()
    assert await motor(now=Reloj(), portales_fn=portales_fn).vuelta_portales() == {}
    portales_fn.assert_not_awaited()


@pytest.mark.asyncio
async def test_la_vuelta_de_portales_corre_aunque_linkedin_no_tenga_reglas(monkeypatch):
    loop = motor(now=Reloj())
    llamadas = []

    async def ciclo():
        llamadas.append("linkedin")
        raise RuntimeError("sin reglas de LinkedIn")

    async def vuelta():
        llamadas.append("portales")
        loop._stop_event.set()
        return {}

    monkeypatch.setattr(loop, "run_cycle", ciclo)
    monkeypatch.setattr(loop, "vuelta_portales", vuelta)
    await loop._harvest_loop()
    assert llamadas == ["linkedin", "portales"]


@pytest.mark.asyncio
async def test_jooble_lee_la_pagina_completa_antes_de_clasificar():
    _, pid = portales_store.guardar_oferta(_oferta("jooble", "5"), "Análisis")
    completo = "Analista funcional para banco. " * 30
    ingest = AsyncMock(return_value={"texto": completo, "emails": ["rrhh@umbrella.test"]})
    triage = AsyncMock(return_value={pid: {"es_busqueda_laboral": True}})
    loop = motor(ingest_fn=ingest, triage_fn=triage)
    await loop._clasificar([radar_store.get_post(pid)])
    post = radar_store.get_post(pid)
    assert post["texto_pagina"] == completo.strip() and post["emails"] == ["rrhh@umbrella.test"]
    assert completo.strip() in triage.await_args.args[0][0]["texto"]
    # Si la página no se puede leer, sigue con el resumen
    _, otro = portales_store.guardar_oferta(_oferta("jooble", "6", titulo="Otro", empresa="Otra"), "Análisis")
    ingest.side_effect = RuntimeError("bloqueada")
    triage.return_value = {otro: {"es_busqueda_laboral": True}}
    await loop._clasificar([radar_store.get_post(otro)])
    assert radar_store.get_post(otro)["estado"] == "OFERTA" and radar_store.get_post(otro)["texto_pagina"] == ""
    # Indeed ya trae el aviso completo: no se lee la página
    _, indeed = portales_store.guardar_oferta(_oferta("indeed", "7", titulo="Tercero", empresa="X"), "Análisis")
    ingest.reset_mock()
    triage.return_value = {indeed: {"es_busqueda_laboral": True}}
    await loop._clasificar([radar_store.get_post(indeed)])
    ingest.assert_not_awaited()


@pytest.mark.asyncio
async def test_sin_lector_de_paginas_jooble_sigue_con_el_resumen():
    _, pid = portales_store.guardar_oferta(_oferta("jooble", "8"), "Análisis")
    loop = motor(triage_fn=AsyncMock(return_value={pid: {"es_busqueda_laboral": True}}))
    await loop._clasificar([radar_store.get_post(pid)])
    assert radar_store.get_post(pid)["estado"] == "OFERTA"


# ---------------------------------------------------------------- API y Mi perfil

@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(server, "radar_loop", Mock(wake_processing=AsyncMock()))
    return TestClient(server.app)


def test_vista_linkedin_no_muestra_portales_y_portales_los_junta(api):
    linkedin = radar_store.insert_post_if_new({"urn": "urn:li:1", "texto": "Buscamos dev"}, "consulta")
    manual = radar_store.insert_post_manual("Buscamos QA")
    _, jooble = portales_store.guardar_oferta(_oferta("jooble", "1"), "Análisis")
    _, indeed = portales_store.guardar_oferta(_oferta("indeed", "2", titulo="Otro", empresa="Otra"), "Análisis")
    ids = lambda url: [p["id"] for p in api.get(url).json()]
    assert ids("/api/radar/posts") == [linkedin]
    assert ids("/api/radar/posts?limite_descartados=5") == [linkedin]
    assert ids("/api/radar/posts?fuente=portales") == [indeed, jooble]
    assert ids("/api/radar/posts?fuente=portales&limite_descartados=5") == [indeed, jooble]
    assert ids("/api/radar/posts?fuente=jooble") == [jooble]
    assert ids("/api/radar/posts?fuente=manual") == [manual]
    assert api.get("/api/radar/posts?fuente=otra").status_code == 422
    assert api.get("/api/radar/posts?fuente=portales").json()[0]["fuente"] == "indeed"


def test_mi_perfil_muestra_y_guarda_portales_de_cada_busqueda(api, perfil):
    dato = api.get("/api/candidato/busquedas").json()
    assert dato["portales"] == list(reglas_portales.PORTALES)
    por_nombre = {b["nombre"]: b for b in dato["busquedas"]}
    assert por_nombre["Análisis"]["portales"] == ["jooble", "indeed"]
    assert por_nombre["Solo LinkedIn"] == {**por_nombre["Solo LinkedIn"], "portales": [], "palabras": ""}
    respuesta = api.put("/api/candidato/busquedas/portales", json={"version": dato["version"], "cambios": [
        {"nombre": "Solo LinkedIn", "portales": ["jooble"], "palabras": "soporte de aplicaciones"},
        {"nombre": "Soporte", "portales": [], "palabras": ""},
    ]})
    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["backup"].startswith("candidato-")
    guardado = json.loads((perfil / "busquedas.json").read_text(encoding="utf-8"))["busquedas"]
    assert guardado[3] == {"nombre": "Solo LinkedIn", "activa": True, "consulta": "\"cv a\" otra",
                           "portales": ["jooble"], "palabras": "soporte de aplicaciones"}
    assert guardado[1] == {"nombre": "Soporte", "activa": True, "consulta": "\"cv a\" soporte"}
    assert guardado[0] == BUSQUEDAS["busquedas"][0]
    # Versión vieja: 409; búsqueda inexistente o inválida: 422
    viejo = api.put("/api/candidato/busquedas/portales", json={"version": dato["version"], "cambios": []})
    assert viejo.status_code == 409
    version = api.get("/api/candidato/busquedas").json()["version"]
    for cambios, texto in [([{"nombre": "No existe", "portales": []}], "No existe la búsqueda"),
                           ([{"nombre": "Soporte", "portales": ["indeed"], "palabras": "a OR b"}], "sin AND, OR"),
                           ([{"portales": []}], "lleva el nombre")]:
        r = api.put("/api/candidato/busquedas/portales", json={"version": version, "cambios": cambios})
        assert r.status_code == 422 and texto in r.json()["detail"]


def test_guardar_portales_sin_archivo_o_con_archivo_roto(api, perfil):
    (perfil / "busquedas.json").unlink()
    version = api.get("/api/candidato/busquedas").json()["version"]
    assert api.put("/api/candidato/busquedas/portales", json={"version": version, "cambios": []}).status_code == 404
    (perfil / "busquedas.json").write_text("{roto", encoding="utf-8")
    version = api.get("/api/candidato/busquedas").json()["version"]
    r = api.put("/api/candidato/busquedas/portales", json={"version": version, "cambios": []})
    assert r.status_code == 422 and "no es un JSON válido" in r.json()["detail"]
    (perfil / "busquedas.json").write_text("{}", encoding="utf-8")
    version = api.get("/api/candidato/busquedas").json()["version"]
    r = api.put("/api/candidato/busquedas/portales", json={"version": version, "cambios": []})
    assert r.status_code == 422 and "«busquedas»" in r.json()["detail"]
    (perfil / "busquedas.json").write_text(json.dumps(BUSQUEDAS), encoding="utf-8")
    version = api.get("/api/candidato/busquedas").json()["version"]
    r = api.put("/api/candidato/busquedas/portales", json={"version": version, "cambios": {"nombre": "x"}})
    assert r.status_code == 422


def test_reglas_de_portales_se_editan_en_mi_perfil(api, perfil):
    leido = api.get("/api/candidato/perfil/reglas_portales").json()
    assert json.loads(leido["contenido"]) == REGLAS
    malas = {**REGLAS, "jooble": {**REGLAS["jooble"], "consultas_por_dia": 0}}
    r = api.put("/api/candidato/perfil/reglas_portales",
                json={"contenido": json.dumps(malas), "version": leido["version"]})
    assert r.status_code == 422 and "«consultas_por_dia» tiene que ser un entero mayor que 0" in r.json()["detail"]
    nuevas = {**REGLAS, "indeed": {**REGLAS["indeed"], "activo": False}}
    r = api.put("/api/candidato/perfil/reglas_portales",
                json={"contenido": json.dumps(nuevas), "version": leido["version"]})
    assert r.status_code == 200
    assert json.loads((perfil / "reglas_portales.json").read_text(encoding="utf-8"))["indeed"]["activo"] is False


def test_estado_de_portales(api, perfil):
    portales_store.registrar_lectura("indeed", "Análisis", "a", portales_store.ahora_iso(), "x", traidas=4)
    malas = {**REGLAS, "jooble": {**REGLAS["jooble"]}}
    del malas["jooble"]["dias"]
    malas["indeed"] = {**REGLAS["indeed"], "activo": False}
    (perfil / "reglas_portales.json").write_text(json.dumps(malas), encoding="utf-8")
    dato = api.get("/api/portales/estado").json()
    jooble, indeed = dato["portales"][:2]
    assert jooble["corre"] is False and "falta «dias»" in jooble["motivo"]
    assert indeed["corre"] is False and indeed["motivo"] == "Apagado en las reglas"
    assert indeed["consultas_hoy"] == 1 and indeed["consultas_por_dia"] == 3
    assert indeed["busquedas"] == ["Análisis", "Soporte"]
    assert dato["lecturas"][0]["traidas"] == 4
    (perfil / "reglas_portales.json").write_text(json.dumps(REGLAS), encoding="utf-8")
    estado = {p["portal"]: p for p in api.get("/api/portales/estado").json()["portales"]}
    assert all(estado[p]["corre"] and estado[p]["motivo"] == "" for p in ("jooble", "indeed"))
    assert estado["computrabajo"]["motivo"] == "Apagado en las reglas"
