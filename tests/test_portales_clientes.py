"""Portales: clientes de Jooble e Indeed con respuestas grabadas (sin red) y reglas por portal."""
import json
from datetime import date
from pathlib import Path

import pytest

import core.paths
from core import portales, reglas_portales
from core.portales import ErrorPortal, buscar_indeed, buscar_jooble, texto_de_oferta
from core.reglas_portales import ReglaPortal, leer_reglas_portales, validar_reglas_portales

GRABADAS = Path(__file__).parent / "fixtures" / "portales"
HOY = date(2026, 11, 14)
CLAVE = "clave-de-prueba-0000"


def _grabada(nombre):
    return json.loads((GRABADAS / nombre).read_text(encoding="utf-8"))


def _regla(portal="jooble", **cambios):
    from tests.portales_prueba import regla
    return regla(portal, **{"ubicacion": "Buenos Aires", **cambios})


from tests.portales_prueba import reglas as _reglas_completas

REGLAS_OK = _reglas_completas(
    jooble={"consultas_por_dia": 100, "ubicacion": "Buenos Aires"},
    indeed={"consultas_por_dia": 20, "minutos_entre_consultas": 30, "ubicacion": "Buenos Aires, Argentina",
            "resultados_por_consulta": 30})


# ---------------------------------------------------------------- Jooble

def test_jooble_normaliza_la_respuesta_grabada_y_filtra_las_viejas():
    pedidos = []

    def post_json(url, cuerpo, plazo):
        pedidos.append((url, cuerpo))
        return 200, _grabada("jooble_respuesta.json")

    ofertas = buscar_jooble("analista funcional", _regla(), CLAVE, post_json=post_json, hoy=HOY)
    assert pedidos == [(f"https://jooble.test/api/{CLAVE}", {
        "keywords": "analista funcional", "location": "Buenos Aires", "page": 1, "ResultOnPage": 20})]
    assert [o["urn"] for o in ofertas] == ["jooble:-5915371398447495", "jooble:7712093844456"]
    primera = ofertas[0]
    assert primera["titulo"] == "Analista Funcional SSr"
    assert primera["empresa"] == "Umbrella"
    assert primera["descripcion"] == "...relevamiento de requerimientos y armado de casos de uso..."
    assert primera["completa"] is False
    assert primera["link"] == "https://jooble.org/jdp/-5915371398447495"
    assert primera["origen"] == "computrabajo.com.ar"
    assert primera["fecha"] == "2026-11-12"


@pytest.mark.parametrize("estado, texto", [(403, "rechazó la key"), (429, "límite"), (500, "respondió 500")])
def test_jooble_errores_con_motivo_y_sin_la_key(estado, texto):
    with pytest.raises(ErrorPortal) as e:
        buscar_jooble("x", _regla(), CLAVE, post_json=lambda *a: (estado, None), hoy=HOY)
    assert texto in str(e.value) and CLAVE not in str(e.value)


def test_jooble_sin_key_no_consulta():
    def post_json(*a):
        raise AssertionError("no debe consultar")

    with pytest.raises(ErrorPortal, match="JOOBLE_API_KEY"):
        buscar_jooble("x", _regla(), "", post_json=post_json)


def test_jooble_error_de_red_tapa_la_key():
    def post_json(url, cuerpo, plazo):
        raise OSError(f"no se pudo abrir {url}")

    with pytest.raises(ErrorPortal) as e:
        buscar_jooble("x", _regla(), CLAVE, post_json=post_json, hoy=HOY)
    assert CLAVE not in str(e.value) and "<clave>" in str(e.value)


def test_clave_jooble_sale_del_env_del_portal(tmp_path, monkeypatch):
    env = tmp_path / "portal.env"
    env.write_text("JOOBLE_API_KEY=desde-el-env\n", encoding="utf-8")
    monkeypatch.setenv("LINKEDOUT_ENV_FILE", str(env))
    monkeypatch.delenv("JOOBLE_API_KEY", raising=False)
    assert portales.clave_jooble() == "desde-el-env"
    env.write_text("", encoding="utf-8")
    assert portales.clave_jooble() == ""


# ---------------------------------------------------------------- Indeed

def test_indeed_normaliza_las_filas_grabadas_de_jobspy():
    llamadas = []

    def scrape(**kwargs):
        llamadas.append(kwargs)
        return _grabada("indeed_filas.json")

    ofertas = buscar_indeed("mesa de ayuda", _regla("indeed", ubicacion="Buenos Aires, Argentina", dias=7,
                                                    resultados_por_consulta=30), scrape=scrape, hoy=HOY)
    assert llamadas == [{"site_name": ["indeed"], "search_term": "mesa de ayuda",
                         "location": "Buenos Aires, Argentina", "country_indeed": "argentina", "hours_old": 168,
                         "results_wanted": 30, "description_format": "markdown", "verbose": 0}]
    assert [o["urn"] for o in ofertas] == ["indeed:in-0a1b2c3d4e5f6071", "indeed:in-1122334455667788"]
    acme, globex = ofertas
    assert acme["completa"] is True and acme["emails"] == []
    assert acme["link"] == "https://ar.indeed.com/viewjob?jk=0a1b2c3d4e5f6071"
    assert acme["link_directo"] == "https://acme.teamtailor.test/jobs/1001-analista-funcional"
    assert globex["emails"] == ["rrhh@globex.test"]


def test_indeed_error_de_jobspy_es_error_del_portal():
    def scrape(**kwargs):
        raise RuntimeError("bloqueado")

    with pytest.raises(ErrorPortal, match="Indeed no respondió: RuntimeError: bloqueado"):
        buscar_indeed("x", _regla("indeed"), scrape=scrape)


def test_oferta_sin_link_o_sin_id_no_entra():
    filas = [{"id": "", "title": "A", "job_url": "https://x.test"}, {"id": "in-1", "title": "B", "job_url": ""},
             "basura", {"id": "in-2", "title": "C", "job_url": "https://x.test/2", "date_posted": None}]
    ofertas = buscar_indeed("x", _regla("indeed"), scrape=lambda **k: filas, hoy=HOY)
    assert [o["urn"] for o in ofertas] == ["indeed:in-2"]


def test_texto_de_oferta_junta_titulo_empresa_ubicacion_y_descripcion():
    texto = texto_de_oferta({"titulo": "Analista", "empresa": "Acme", "ubicacion": "CABA", "descripcion": "Detalle"})
    assert texto == "Analista\n\nAcme · CABA\n\nDetalle"


@pytest.mark.asyncio
async def test_buscar_despacha_por_portal_fuera_del_loop(monkeypatch):
    monkeypatch.setattr(portales, "buscar_indeed", lambda p, r: [{"urn": "indeed:1"}])
    monkeypatch.setattr(portales, "buscar_jooble", lambda p, r, c: [{"urn": "jooble:1", "clave": c}])
    monkeypatch.setattr(portales, "clave_jooble", lambda: "k")
    assert await portales.buscar("indeed", "x", _regla("indeed")) == [{"urn": "indeed:1"}]
    assert await portales.buscar("jooble", "x", _regla()) == [{"urn": "jooble:1", "clave": "k"}]
    with pytest.raises(ErrorPortal):
        await portales.buscar("otro", "x", _regla())


# ---------------------------------------------------------------- reglas

def test_reglas_validas_y_errores_por_campo():
    assert validar_reglas_portales(REGLAS_OK) == []
    malas = json.loads(json.dumps(REGLAS_OK))
    malas["jooble"]["consultas_por_dia"] = 0
    del malas["indeed"]["ubicacion"]
    malas["indeed"]["extra"] = 1
    malas["otro"] = {}
    errores = validar_reglas_portales(malas)
    assert "Portal desconocido: «otro»." in errores
    assert "Jooble: «consultas_por_dia» tiene que ser un entero mayor que 0." in errores
    assert "Indeed: falta «ubicacion»." in errores
    assert "Indeed: campo desconocido «extra»." in errores
    assert validar_reglas_portales([]) == ["Las reglas de Portales tienen que ser un objeto JSON."]
    assert validar_reglas_portales({}) == [f"Falta «{p}»." for p in reglas_portales.PORTALES] + ["Falta «duplicados»."]


@pytest.mark.parametrize("cambio, error", [
    ({"activo": "si"}, "«activo» tiene que ser true o false"),
    ({"ubicacion": " "}, "«ubicacion» tiene que ser un texto"),
    ({"dias": 0}, "«dias» tiene que ser un entero mayor que 0"),
    ({"plazo_consulta_s": 0}, "«plazo_consulta_s» tiene que ser un entero mayor que 0"),
])
def test_cada_campo_se_valida(cambio, error):
    regla = {**REGLAS_OK["indeed"], **cambio}
    assert any(error in e for e in reglas_portales.errores_de_portal("indeed", regla))
    assert reglas_portales.errores_de_portal("indeed", "x") == [
        "Indeed: tiene que ser un objeto con activo, ubicacion, modo, listados, leer_pagina_aviso, consultas_por_dia, "
        "minutos_entre_consultas, resultados_por_consulta, dias, plazo_consulta_s, min_descripcion_completa, pais."]


@pytest.fixture(autouse=True)
def perfil_aislado(tmp_path, monkeypatch):
    """Cada test escribe su propio perfil: nunca el compartido de la sesión."""
    import shutil
    candidato = tmp_path / "datos" / "candidato"
    shutil.copytree(core.paths.DATA_DIR / "candidato", candidato)
    monkeypatch.setattr(core.paths, "DATA_DIR", tmp_path / "datos")


def _escribir_reglas(contenido):
    ruta = core.paths.DATA_DIR / "candidato" / reglas_portales.ARCHIVO
    ruta.write_text(contenido if isinstance(contenido, str) else json.dumps(contenido), encoding="utf-8")
    return ruta


def test_leer_reglas_un_portal_mal_escrito_no_frena_al_otro():
    malas = json.loads(json.dumps(REGLAS_OK))
    del malas["jooble"]["dias"]
    _escribir_reglas(malas)
    validas, invalidas = leer_reglas_portales()
    assert list(validas) == [p for p in reglas_portales.PORTALES if p != "jooble"]
    assert validas["indeed"].ubicacion == "Buenos Aires, Argentina"
    assert invalidas == {"jooble": "reglas_portales.json: Jooble: falta «dias»."}


def test_leer_reglas_sin_archivo_roto_o_sin_portal():
    ruta = core.paths.DATA_DIR / "candidato" / reglas_portales.ARCHIVO
    ruta.unlink(missing_ok=True)
    validas, invalidas = leer_reglas_portales()
    assert validas == {} and set(invalidas) == set(reglas_portales.PORTALES) and "No existe" in invalidas["jooble"]
    _escribir_reglas("{roto")
    assert "no es un JSON válido" in leer_reglas_portales()[1]["indeed"]
    _escribir_reglas([])
    assert "objeto JSON" in leer_reglas_portales()[1]["indeed"]
    _escribir_reglas({"indeed": REGLAS_OK["indeed"]})
    validas, invalidas = leer_reglas_portales()
    assert list(validas) == ["indeed"] and invalidas == {p: f"reglas_portales.json: falta «{p}»."
                                                         for p in reglas_portales.PORTALES if p != "indeed"}



@pytest.mark.parametrize("portal,cambio,error", [
    ("indeed", {"modo": "otro"}, "«modo» tiene que ser todas o busquedas"),
    ("computrabajo", {"listados": "x"}, "«listados» tiene que ser una lista"),
    ("remotive", {"url_busqueda": ""}, "con «busquedas», falta «url_busqueda»"),
    ("remotive", {"url_busqueda": "https://x.test/api"}, "tiene que llevar «{palabras}»"),
    ("jooble", {"url": "https://jooble.test/api"}, "tiene que llevar «{clave}»"),
])
def test_modo_y_listados(portal, cambio, error):
    regla = {**REGLAS_OK[portal], **cambio}
    assert any(error in e for e in reglas_portales.errores_de_portal(portal, regla))
