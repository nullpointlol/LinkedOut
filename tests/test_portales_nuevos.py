"""Portales sin búsquedas: Computrabajo (listados públicos), Bumeran (con el navegador, como LinkedIn),
Indeed sin palabras, un radar por portal que se pausa solo si el portal pide una verificación. Sin red: HTML armado con
la estructura de cada sitio y datos inventados."""
import json
import shutil
from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, Mock

import pytest

import core.paths
from core import portales, portales_store, radar_store
from core.radar import RadarLoop
from core.reglas_portales import ReglaPortal

HOY = date(2026, 11, 15)


def _aviso_ct(id_, titulo, cuando, empresa="Acme SA", lugar="Palermo, Capital Federal"):
    return (f"<article class=\"box_offer\" data-id='{id_}' data-lc=\"x\"> <h2 class=\"fs18 fwB prB\"> "
            f"<a class=\"js-o-link fc_base\" href=\"/ofertas-de-trabajo/oferta-{id_}#lc=x\"> {titulo} </a> </h2> "
            f"<p class=\"dFlex vm_fx fs16 fc_base mt5\"> <a class=\"fc_base t_ellipsis\" href=\"https://x\" "
            f"offer-grid-article-company-url> {empresa} </a> </p> <p class=\"fs16 fc_base mt5\"> "
            f"<span class=\"mr10\"> {lugar} </span> </p> <p class=\"fs13 fc_aux mt15\"> {cuando} </p> </article>")


DETALLE_CT = ('<div class="mb40 pb40 bb1" div-link="oferta"> <h2 class="fwB fs18 mb20">Descripción de la oferta</h2> '
              '<p class="mbB">' + "Buscamos analista funcional con SQL. " * 15 + '</p> <ul class="disc mbB"> '
              "<li class='mb10'>2 años de experiencia</li> </ul> <div class=\"pos\">Postularme</div>")


def _regla(portal, **cambios):
    from tests.portales_prueba import regla
    base = {"computrabajo": {"url": "https://ar.computrabajo.com"}, "bumeran": {"url": "https://www.bumeran.com.ar"}}
    return regla(portal, **{"modo": "todas", **base.get(portal, {}), **cambios})


# ---------------------------------------------------------------- fechas relativas

@pytest.mark.parametrize("texto,dias", [("Hace 1 hora", 0), ("Ayer", 1), ("Publicado hace 2 días", 2),
                                        ("Hace 1 mes", 30), ("Publicado hoy", 0)])
def test_fecha_relativa(texto, dias):
    assert portales.fecha_relativa(texto, HOY) == HOY - timedelta(days=dias)


# ---------------------------------------------------------------- Computrabajo

def test_computrabajo_lee_la_lista_y_el_aviso():
    lista = portales.avisos_computrabajo(_aviso_ct("AB12", "Analista Funcional", "Hace 2 horas"), "https://ar.computrabajo.com")
    assert lista == [{"id": "AB12", "titulo": "Analista Funcional",
                      "link": "https://ar.computrabajo.com/ofertas-de-trabajo/oferta-AB12", "empresa": "Acme SA",
                      "ubicacion": "Palermo, Capital Federal", "cuando": "Hace 2 horas"}]
    detalle = portales.detalle_computrabajo(DETALLE_CT)
    assert detalle.startswith("Descripción de la oferta") and "2 años de experiencia" in detalle
    assert "Postularme" not in detalle


def test_computrabajo_trae_las_recientes_corta_en_las_viejas_y_no_relee_las_vistas():
    paginas = {1: _aviso_ct("A1", "Analista", "Hace 1 hora") + _aviso_ct("A2", "Soporte", "Ayer"),
               2: _aviso_ct("A3", "QA", "Hace 2 días") + _aviso_ct("A4", "Viejo", "Hace 10 días")}
    pedidos = []

    def get(url):
        pedidos.append(url)
        if "oferta-" in url:
            return DETALLE_CT
        return paginas.get(int(url.rsplit("p=", 1)[1]), "")

    ofertas = portales.buscar_computrabajo("empleos-en-capital-federal", _regla("computrabajo"), get=get,
                                           ya_vistos=lambda urns: {"computrabajo:A2"}, hoy=HOY)
    assert [o["id"] for o in ofertas] == ["A1", "A3"]  # A2 ya entró; A4 tiene más de 7 días
    assert all(o["completa"] and o["portal"] == "computrabajo" for o in ofertas)
    assert pedidos[0] == "https://ar.computrabajo.com/empleos-en-capital-federal?pubdate=7&p=1"
    assert not any("oferta-A2" in p for p in pedidos)  # la vista no se vuelve a pedir
    assert not any("p=3" in p for p in pedidos)  # cortó al ver una vieja


def test_computrabajo_bloqueado_es_un_error_del_portal():
    def get(url):
        raise portales.ErrorPortal("El portal rechazó la consulta (403)")
    with pytest.raises(portales.ErrorPortal):
        portales.buscar_computrabajo("empleos-en-cordoba", _regla("computrabajo"), get=get)


# ---------------------------------------------------------------- Bumeran

def test_bumeran_url_del_listado_por_dias():
    assert portales.url_listado_bumeran("https://www.bumeran.com.ar", "empleos", 7, 1) == "https://www.bumeran.com.ar/empleos-publicacion-menor-a-7-dias.html"
    assert portales.url_listado_bumeran("https://www.bumeran.com.ar", "empleos", 1, 3) == "https://www.bumeran.com.ar/empleos-publicacion-hoy.html?page=3"


def test_bumeran_arma_las_ofertas_de_las_tarjetas():
    tarjetas = [{"id": "111", "href": "/empleos/analista-111.html", "datos": "Capital Federal, Buenos Aires\nPresencial",
                 "encabezado": "Publicado ayer\nAnalista Funcional\nAcme SA\n\n" + "Relevar requerimientos. " * 30},
                {"id": "222", "href": "/empleos/viejo-222.html", "datos": "",
                 "encabezado": "Publicado hace 20 días\nViejo\nOtra"}]
    ofertas, viejas = portales.ofertas_bumeran(tarjetas, _regla("bumeran"), HOY)
    assert viejas and len(ofertas) == 1
    o = ofertas[0]
    assert (o["titulo"], o["empresa"], o["ubicacion"], o["fecha"]) == ("Analista Funcional", "Acme SA",
                                                                       "Capital Federal, Buenos Aires", "2026-11-14")
    assert o["completa"] and o["link"] == "https://www.bumeran.com.ar/empleos/analista-111.html"


@pytest.mark.asyncio
async def test_bumeran_recorre_paginas_hasta_las_viejas():
    lector = Mock()
    paginas = [[{"id": "1", "href": "/empleos/a-1.html", "datos": "", "encabezado": "Publicado hoy\nA\nX"}],
               [{"id": "2", "href": "/empleos/b-2.html", "datos": "", "encabezado": "Publicado hace 9 días\nB\nY"}],
               [{"id": "3", "href": "/empleos/c-3.html", "datos": "", "encabezado": "Publicado hoy\nC\nZ"}]]
    lector.tarjetas_bumeran = AsyncMock(side_effect=paginas)
    ofertas = await portales.buscar_bumeran("empleos", _regla("bumeran"), lector=lector, hoy=HOY, get=lambda u: "")
    assert [o["id"] for o in ofertas] == ["1"] and lector.tarjetas_bumeran.await_count == 2


# ---------------------------------------------------------------- radar por portal



from tests.portales_prueba import reglas as _reglas_completas

REGLAS = _reglas_completas(
    apagados=("jooble", "remoteok", "remotive", "himalayas"),
    indeed={"resultados_por_consulta": 30, "modo": "todas"},
    computrabajo={"modo": "todas", "listados": ["empleos-en-capital-federal", "empleos-en-cordoba"]},
    bumeran={"modo": "todas", "listados": ["empleos"]})


@pytest.fixture
def perfil(tmp_path, monkeypatch):
    candidato = tmp_path / "datos" / "candidato"
    shutil.copytree(core.paths.DATA_DIR / "candidato", candidato)
    monkeypatch.setattr(core.paths, "DATA_DIR", tmp_path / "datos")
    (candidato / "reglas_portales.json").write_text(json.dumps(REGLAS), encoding="utf-8")
    return candidato


class Reloj:
    def __init__(self):
        self.ahora = datetime(2026, 11, 15, 10, 0)

    def __call__(self):
        return self.ahora


def _motor(portales_fn, reloj):
    return RadarLoop(harvester_factory=Mock(side_effect=AssertionError("sin LinkedIn")), triage_fn=AsyncMock(return_value={}),
                     analyze_fn=AsyncMock(return_value={}), pipeline_fn=AsyncMock(return_value=1),
                     compose_fn=AsyncMock(return_value={}), send_fn=AsyncMock(side_effect=AssertionError("sin envíos")),
                     broadcast=AsyncMock(), portales_fn=portales_fn, now=reloj)


@pytest.mark.asyncio
async def test_modo_todas_rota_los_listados_y_un_portal_pausado_no_corre(perfil):
    reloj, llamadas = Reloj(), []

    async def portales_fn(portal, palabras, regla):
        llamadas.append((portal, palabras))
        return []

    loop = _motor(portales_fn, reloj)
    portales_store.pausar("bumeran")
    await loop.vuelta_portales()
    assert llamadas == [("indeed", ""), ("computrabajo", "empleos-en-capital-federal")]  # Bumeran, en pausa
    reloj.ahora += timedelta(minutes=16)
    portales_store.reanudar("bumeran")
    await loop.vuelta_portales()
    assert llamadas[2:] == [("indeed", ""), ("computrabajo", "empleos-en-cordoba"), ("bumeran", "empleos")]


@pytest.mark.asyncio
async def test_un_captcha_abre_un_ticket_y_el_radar_no_se_pausa(perfil, monkeypatch):
    # con un captcha, el ticket; nada de pausas ni límites que no definió
    from core import radar as radar_mod
    tickets = []
    monkeypatch.setattr(radar_mod, "_ticket", lambda *a, **kw: tickets.append(a))

    async def portales_fn(portal, palabras, regla):
        if portal == "bumeran":
            raise portales.ErrorPortal("El sitio pidió una verificación (captcha o desafío) en https://x: «Just a moment»")
        return []

    await _motor(portales_fn, Reloj()).vuelta_portales()
    assert portales_store.pausados() == {}
    assert any(a[0] == "consulta_fallida" and "verificación" in a[1] for a in tickets)


SITEMAP = ("<urlset><url><loc>https://www.bumeran.com.ar/empleos/a-111.html</loc><lastmod>2026-11-15</lastmod></url>"
           "<url><loc>https://www.bumeran.com.ar/empleos/b-222.html</loc><lastmod>2026-10-01</lastmod></url></urlset>")


def test_el_sitemap_de_bumeran_da_los_avisos_recientes():
    assert portales.avisos_del_sitemap(SITEMAP, 7, HOY) == ["111"]


@pytest.mark.asyncio
async def test_si_el_sitemap_no_trae_nada_nuevo_no_se_abre_el_navegador():
    lector = Mock()
    lector.tarjetas_bumeran = AsyncMock(side_effect=AssertionError("no hacía falta el navegador"))
    ofertas = await portales.buscar_bumeran("empleos", _regla("bumeran"), lector=lector, hoy=HOY,
                                            get=lambda u: SITEMAP, ya_vistos=lambda urns: {"bumeran:111"})
    assert ofertas == []


@pytest.mark.asyncio
async def test_con_avisos_nuevos_lee_hasta_encontrarlos_y_saltea_los_vistos():
    lector = Mock()
    paginas = [[{"id": "999", "href": "/empleos/x-999.html", "datos": "", "encabezado": "Publicado hoy\nVisto\nX"},
                {"id": "111", "href": "/empleos/a-111.html", "datos": "", "encabezado": "Publicado hoy\nNuevo\nY"}],
               [{"id": "333", "href": "/empleos/c-333.html", "datos": "", "encabezado": "Publicado hoy\nC\nZ"}]]
    lector.tarjetas_bumeran = AsyncMock(side_effect=paginas)
    ofertas = await portales.buscar_bumeran("empleos", _regla("bumeran"), lector=lector, hoy=HOY,
                                            get=lambda u: SITEMAP, ya_vistos=lambda urns: {"bumeran:999"})
    assert [o["id"] for o in ofertas] == ["111"] and lector.tarjetas_bumeran.await_count == 1


# ---------------------------------------------------------------- remoto en el exterior (APIs gratis)

REMOTEOK = [{"legal": "link back"}, {"id": "7", "position": "QA Analyst", "company": "Acme", "location": "Worldwide",
                                    "description": "<p>" + "Test cases. " * 40 + "</p>", "url": "https://remoteok.test/7",
                                    "date": "2026-11-14T08:00:00+00:00"},
            {"id": "8", "position": "Viejo", "company": "B", "location": "", "description": "x",
             "url": "https://remoteok.test/8", "date": "2026-10-01T08:00:00+00:00"}]
REMOTIVE = {"jobs": [{"id": 5, "title": "Business Analyst", "company_name": "Beta", "candidate_required_location": "LATAM",
                      "description": "<p>Requirements</p>", "url": "https://remotive.test/5",
                      "publication_date": "2026-11-13T17:29:57"}]}
HIMALAYAS = {"jobs": [{"guid": "h-1", "title": "Support Engineer", "companyName": "Gamma",
                       "locationRestrictions": ["Argentina", "Brazil"], "description": "<p>Linux</p>",
                       "applicationLink": "https://himalayas.test/1", "pubDate": 1794765499}]}


@pytest.mark.parametrize("portal,buscar,datos,esperado", [
    ("remoteok", portales.buscar_remoteok, REMOTEOK, ("7", "QA Analyst", "Acme", "Worldwide")),
    ("remotive", portales.buscar_remotive, REMOTIVE, ("5", "Business Analyst", "Beta", "LATAM")),
    ("himalayas", portales.buscar_himalayas, HIMALAYAS, ("h-1", "Support Engineer", "Gamma", "Argentina, Brazil")),
])
def test_apis_de_remoto_normalizan_y_filtran_por_fecha(portal, buscar, datos, esperado):
    pedidos = []
    ofertas = buscar("", _regla(portal), get_json=lambda url, plazo: pedidos.append(url) or datos, hoy=HOY)
    assert [(o["id"], o["titulo"], o["empresa"], o["ubicacion"]) for o in ofertas] == [esperado]
    assert ofertas[0]["portal"] == portal and pedidos == [_regla(portal).url]


def test_con_busquedas_usa_la_url_de_busqueda_del_perfil():
    pedidos = []
    portales.buscar_remotive("business analyst", _regla("remotive", modo="busquedas"),
                             get_json=lambda url, plazo: pedidos.append(url) or REMOTIVE, hoy=HOY)
    assert pedidos == ["https://remotive.test/api?search=business%20analyst"]


# ---------------------------------------------------------------- duplicados aproximados

def test_la_misma_oferta_con_otras_palabras_es_un_duplicado(perfil):
    def oferta(portal, id_, titulo, empresa):
        return {"portal": portal, "id": id_, "urn": f"{portal}:{id_}", "titulo": titulo, "empresa": empresa,
                "ubicacion": "CABA", "descripcion": "x", "completa": False, "link": f"https://{portal}.test/{id_}",
                "link_directo": "", "emails": [], "fecha": ""}
    _, pid = portales_store.guardar_oferta(oferta("computrabajo", "1", "Analista Funcional Sr", "Acme S.A."), "x")
    assert portales_store.guardar_oferta(oferta("bumeran", "2", "Analista Funcional Sr.", "ACME SA"), "x") ==         (portales_store.DUPLICADA, pid)
    assert portales_store.guardar_oferta(oferta("bumeran", "3", "Contador Sr", "Acme S.A."), "x")[0] == portales_store.NUEVA
