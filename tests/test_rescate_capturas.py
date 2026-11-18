"""Rescate de fondo de las ofertas en «Captura incompleta» (creador, 09/11): regla «aviso útil»,
barrido (publicación, imagen, links) y endpoint. Sin red, LinkedIn ni AGY reales."""
import itertools
from copy import deepcopy
from datetime import datetime
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient

import server
from core import radar_store
from core.linkedin_harvester import LinkedInHarvester
from core.prioridades import aviso_util
from core.radar import RESCATE_CADA_S, RadarLoop

IMAGEN = "https://media.licdn.com/dms/image/v2/feedshare/oferta.png"
POST_URL = "https://www.linkedin.com/feed/update/urn:li:share:1/"
CORTO_EMAIL = ("Buscamos un profesional Jr. con ganas de aprender. Si te interesa el puesto, "
               "mandá tu CV al correo: seleccion@empresa-ficticia.test\n#Hiring")
CORTO_SIN_CONTACTO = ("We're hiring a Senior IT Specialist to join our onsite team. In this role you will work "
                      "with complex IT environments and troubleshoot advanced technical issues.")
LARGO = "Vacante de analista. Requisitos: experiencia en Python y SQL. " * 10
ANALISIS = {"match_score": 10, "oferta_cerrada": True, "puesto": "Profesional Jr."}
NUEVO_TEXTO = "¡Te estamos buscando! Ejecutivo comercial con experiencia en ciberseguridad y soporte técnico"
ERROR_INCOMPLETA = "Captura incompleta: falta texto suficiente o un puesto detectado"
_URN = itertools.count(1)


@pytest.fixture(autouse=True)
def sin_proveedores(monkeypatch):
    from core import image_reader
    from core.bridge import AntigravityBridge

    monkeypatch.setattr(image_reader, "_descargar_imagen", Mock(side_effect=AssertionError("sin red")))
    monkeypatch.setattr(AntigravityBridge, "run_prompt", AsyncMock(side_effect=AssertionError("sin AGY")))
    radar_store.update_radar_state(generacion_pausada=True, backlog_activo=False)


class HarvesterFalso:
    def __init__(self, recaptura=None):
        self.recapturar_post = AsyncMock(return_value=recaptura)


def motor(monkeypatch, harvester=None, **kwargs):
    loop = RadarLoop(
        harvester_factory=Mock(return_value=harvester or HarvesterFalso()),
        triage_fn=AsyncMock(return_value={}),
        analyze_fn=kwargs.pop("analyze_fn", AsyncMock(return_value=deepcopy(ANALISIS))),
        pipeline_fn=AsyncMock(return_value=1),
        compose_fn=AsyncMock(return_value={"to": "a@b.test", "subject": "S", "body": "B"}),
        send_fn=AsyncMock(side_effect=AssertionError("sin aprobación")),
        broadcast=AsyncMock(),
        image_reader_fn=kwargs.pop("image_reader_fn", AsyncMock(return_value="")),
        now=lambda: datetime(2026, 11, 9, 10, 30),
        **kwargs,
    )
    monkeypatch.setattr(loop, "_wake_processing", Mock())
    monkeypatch.setattr(loop, "wake_generation", AsyncMock())
    return loop


def incompleta(texto=CORTO_SIN_CONTACTO, *, puesto="Profesional Jr.", imagenes=None, emails=None,
               links=None, post_url=POST_URL, fuente="feed", estado="ERROR", **campos):
    n = next(_URN)
    pid = radar_store.insert_post_if_new({
        "urn": f"urn:li:card:rescate-{n}", "texto": texto, "imagenes": imagenes or [],
        "emails": emails or [], "links": links or [], "post_url": post_url,
    }, "q", fuente=fuente)
    radar_store.update_post(pid, estado=estado, error=ERROR_INCOMPLETA if estado == "ERROR" else None,
                            triage={"es_busqueda_laboral": True, "puesto": puesto}, **campos)
    return pid


# ----------------------------------------------------------------- regla «aviso útil»

@pytest.mark.parametrize("post, esperado", [
    ({"texto": CORTO_EMAIL, "triage": {"puesto": "Profesional Jr."}}, True),  # email en el texto
    ({"texto": "Corto", "emails": ["rrhh@x.test"], "triage": {"puesto": "Analista"}}, True),
    ({"texto": "Corto", "texto_pagina": "Página de la oferta", "triage": {"puesto": "Analista"}}, True),
    ({"texto": CORTO_SIN_CONTACTO, "triage": {"puesto": "Senior IT Specialist"}}, False),
    ({"texto": CORTO_EMAIL, "triage": {"puesto": "Posición no especificada"}}, False),
    ({"texto": CORTO_EMAIL, "triage": {}}, False),
    ({"texto": LARGO, "triage": {"puesto": "Analista"}}, True),  # igual que captura_suficiente
    ({"texto": LARGO, "triage": {}}, False),
])
def test_aviso_util(post, esperado):
    assert aviso_util(post) is esperado


CORTO_DM = ("En Hitss buscamos Soporte de Aplicaciones. Modalidad híbrida. Experiencia en PL/SQL y Oracle. "
            "Podés enviar tu CV por privado.")
CORTO_PORTAL = "Auxiliar de servicio\n\nCortinart · Morón\n\nReparación de cortinas. Secundario completo."


@pytest.mark.parametrize("post, esperado", [
    # pide el CV por privado: el destino es el autor (posts 9145, 4676, 4797 y 3502, 16/11)
    ({"texto": CORTO_DM, "canal": "dm", "destino_canal": "https://www.linkedin.com/in/autora/",
      "triage": {"puesto": "Soporte de Aplicaciones"}}, True),
    ({"texto": CORTO_DM, "canal": "dm", "destino_canal": "", "triage": {"puesto": "Soporte"}}, False),
    ({"texto": CORTO_DM, "canal": "dm", "destino_canal": "https://www.linkedin.com/in/a/", "triage": {}}, False),
    # Indeed trae el aviso completo aunque sea corto (10943, 11156, 11167); Jooble trae un resumen
    ({"texto": CORTO_PORTAL, "fuente": "indeed", "triage": {"puesto": "Auxiliar de servicio"}}, True),
    ({"texto": CORTO_PORTAL, "fuente": "jooble", "triage": {"puesto": "Business Analyst"}}, False),
    ({"texto": CORTO_SIN_CONTACTO, "canal": "otro", "triage": {"puesto": "Senior IT Specialist"}}, False),
])
def test_aviso_corto_con_destino_por_privado_o_completo_del_portal(post, esperado, monkeypatch):
    from core import prioridades
    reglas = {"indeed": Mock(leer_pagina_aviso=False), "jooble": Mock(leer_pagina_aviso=True)}
    monkeypatch.setattr(prioridades, "leer_reglas_portales", lambda: (reglas, {}))
    assert aviso_util(post) is esperado


@pytest.mark.asyncio
async def test_analizar_corto_con_email_y_puesto_se_analiza(monkeypatch):
    pid = incompleta(CORTO_EMAIL, estado="OFERTA")
    analizar = AsyncMock(return_value=deepcopy(ANALISIS))
    loop = motor(monkeypatch, analyze_fn=analizar)
    await loop._analizar(radar_store.get_post(pid))
    analizar.assert_awaited_once()
    assert radar_store.get_post(pid)["estado"] == "ANALIZADA"


@pytest.mark.asyncio
async def test_analizar_corto_sin_contacto_sigue_incompleto(monkeypatch):
    pid = incompleta(CORTO_SIN_CONTACTO, estado="OFERTA")
    analizar = AsyncMock(return_value=deepcopy(ANALISIS))
    loop = motor(monkeypatch, analyze_fn=analizar)
    await loop._analizar(radar_store.get_post(pid))
    analizar.assert_not_awaited()
    post = radar_store.get_post(pid)
    assert post["estado"] == "ERROR" and post["error"] == ERROR_INCOMPLETA


# ----------------------------------------------------------------- barrido

@pytest.mark.asyncio
async def test_barrido_toma_tres_de_los_mas_nuevos_y_respeta_exclusiones(monkeypatch):
    viejos = [incompleta() for _ in range(2)]
    nuevos = [incompleta() for _ in range(3)]
    agotado = incompleta(rescate_intentos=2)
    descartado = incompleta(estado="DESCARTADO")
    manual = incompleta(fuente="manual")
    loop = motor(monkeypatch)
    res = await loop.rescatar_capturas()
    assert res == {"revisados": 3, "rescatados": 0, "siguen_incompletos": 3}
    for pid in nuevos:
        assert radar_store.get_post(pid)["rescate_intentos"] == 1
    for pid in [*viejos, agotado, descartado, manual]:
        intentos = radar_store.get_post(pid)["rescate_intentos"]
        assert intentos == (2 if pid == agotado else 0)
    assert radar_store.get_post(descartado)["estado"] == "DESCARTADO"
    assert radar_store.get_post(manual)["error"] == ERROR_INCOMPLETA


@pytest.mark.asyncio
async def test_rescata_corto_con_email_sin_gastar_lecturas(monkeypatch):
    pid = incompleta(CORTO_EMAIL, post_url="")
    lector = AsyncMock(return_value="")
    loop = motor(monkeypatch, image_reader_fn=lector)
    res = await loop.rescatar_capturas()
    assert res == {"revisados": 1, "rescatados": 1, "siguen_incompletos": 0}
    post = radar_store.get_post(pid)
    assert (post["estado"], post["error"], post["intentos"]) == ("OFERTA", None, 0)
    assert post["rescate_intentos"] == 1 and post["rescate_at"]
    lector.assert_not_awaited()
    loop._wake_processing.assert_called_once()


@pytest.mark.asyncio
async def test_lee_la_imagen_aunque_la_lectura_automatica_haya_fallado(monkeypatch):
    pid = incompleta("Mirá la imagen", imagenes=[IMAGEN], puesto="Analista QA")
    lector = AsyncMock(return_value="Analista QA. Enviá tu CV a rrhh@acme.test")
    loop = motor(monkeypatch, image_reader_fn=lector)
    loop._lecturas_fallidas.add(pid)
    await loop.rescatar_capturas()
    lector.assert_awaited_once_with([IMAGEN])
    post = radar_store.get_post(pid)
    assert post["texto_imagen"].startswith("Analista QA")
    assert post["estado"] == "OFERTA"


@pytest.mark.asyncio
async def test_relee_la_publicacion_sin_imagen_y_decide_con_lo_nuevo(monkeypatch):
    pid = incompleta("¡Te estamos buscando! Activar para ver una imagen más grande.", puesto="Ejecutivo comercial")
    harvester = HarvesterFalso({"texto": NUEVO_TEXTO, "imagenes": [IMAGEN],
                                "links": [], "emails": [], "whatsapp": []})
    lector = AsyncMock(return_value="Ejecutivo comercial. CV a seleccion@megatech.test")
    loop = motor(monkeypatch, harvester=harvester, image_reader_fn=lector)
    res = await loop.rescatar_capturas()
    harvester.recapturar_post.assert_awaited_once_with(POST_URL)
    lector.assert_awaited_once_with([IMAGEN])
    post = radar_store.get_post(pid)
    assert post["imagenes"] == [IMAGEN]
    assert post["texto"] == NUEVO_TEXTO
    assert post["estado"] == "OFERTA" and res["rescatados"] == 1


@pytest.mark.asyncio
async def test_relee_links_aunque_el_analisis_ya_lo_haya_intentado(monkeypatch):
    pid = incompleta(CORTO_SIN_CONTACTO, links=["https://lnkd.in/abc"], post_url="", intentos=1)
    vistos = []

    async def preparar(post):
        vistos.append(post["intentos"])
        radar_store.update_post(post["id"], texto_pagina="Senior IT Specialist. Postulate en la web.")
        return dict(post, texto_pagina="Senior IT Specialist. Postulate en la web.")

    loop = motor(monkeypatch, prepare_fn=AsyncMock(side_effect=preparar))
    await loop.rescatar_capturas()
    assert vistos == [0]
    assert radar_store.get_post(pid)["estado"] == "OFERTA"


@pytest.mark.asyncio
async def test_sin_nada_rescatable_queda_con_lo_intentado_y_frena_a_los_dos_intentos(monkeypatch):
    pid = incompleta(CORTO_SIN_CONTACTO, puesto="Senior IT Specialist")
    harvester = HarvesterFalso(None)
    loop = motor(monkeypatch, harvester=harvester)
    await loop.rescatar_capturas()
    post = radar_store.get_post(pid)
    assert post["estado"] == "ERROR"
    assert post["error"] == ("Captura incompleta (revisada 09/11 10:30): no se pudo releer la publicación, "
                             "sin imagen, sin links, sin email")
    assert post["rescate_intentos"] == 1
    await loop.rescatar_capturas()
    assert radar_store.get_post(pid)["rescate_intentos"] == 2
    assert await loop.rescatar_capturas() == {"revisados": 0, "rescatados": 0, "siguen_incompletos": 0}
    assert harvester.recapturar_post.await_count == 2
    assert radar_store.candidatos_rescate(2, 10) == []


@pytest.mark.asyncio
async def test_un_segundo_barrido_pasa_a_los_que_faltan_antes_de_repetir(monkeypatch):
    ids = [incompleta(CORTO_SIN_CONTACTO, post_url="") for _ in range(5)]
    loop = motor(monkeypatch)
    await loop.rescatar_capturas()
    await loop.rescatar_capturas()
    intentos = {pid: radar_store.get_post(pid)["rescate_intentos"] for pid in ids}
    # 3 + 3: los 5 una vez y recién después el más nuevo por segunda vez
    assert sorted(intentos.values()) == [1, 1, 1, 1, 2]
    assert intentos[ids[-1]] == 2


@pytest.mark.asyncio
async def test_no_toca_un_post_que_cambio_durante_el_rescate(monkeypatch):
    pid = incompleta(CORTO_SIN_CONTACTO)
    harvester = HarvesterFalso(None)

    async def descartar_en_el_medio(_url):
        radar_store.update_post(pid, estado="DESCARTADO")
        return None

    harvester.recapturar_post.side_effect = descartar_en_el_medio
    loop = motor(monkeypatch, harvester=harvester)
    assert await loop.rescatar_capturas() == {"revisados": 0, "rescatados": 0, "siguen_incompletos": 0}
    post = radar_store.get_post(pid)
    assert post["estado"] == "DESCARTADO" and post["rescate_intentos"] == 0


@pytest.mark.asyncio
async def test_analisis_pausado_no_rescata(monkeypatch):
    pid = incompleta(CORTO_EMAIL)
    radar_store.update_radar_state(analisis_pausado=True)
    loop = motor(monkeypatch)
    assert await loop.rescatar_capturas() == {"revisados": 0, "rescatados": 0, "siguen_incompletos": 0}
    assert radar_store.get_post(pid)["estado"] == "ERROR"


@pytest.mark.asyncio
async def test_barrido_cada_media_hora_y_solo_con_el_radar_corriendo(monkeypatch):
    reloj = [1000.0]
    loop = motor(monkeypatch, reloj=lambda: reloj[0])
    barrer = AsyncMock(return_value={})
    monkeypatch.setattr(loop, "rescatar_capturas", barrer)
    monkeypatch.setattr(RadarLoop, "is_running", property(lambda self: False))
    await loop._rescate_si_toca()
    barrer.assert_not_awaited()
    monkeypatch.setattr(RadarLoop, "is_running", property(lambda self: True))
    await loop._rescate_si_toca()
    reloj[0] += RESCATE_CADA_S - 1
    await loop._rescate_si_toca()
    assert barrer.await_count == 1
    reloj[0] += 1
    await loop._rescate_si_toca()
    assert barrer.await_count == 2


# ----------------------------------------------------------------- API y harvester

def test_endpoint_rescate(monkeypatch):
    stub = AsyncMock(spec=RadarLoop)
    stub.rescatar_capturas.return_value = {"revisados": 2, "rescatados": 1, "siguen_incompletos": 1}
    monkeypatch.setattr(server, "radar_loop", stub)
    r = TestClient(server.app).post("/api/radar/rescate")
    assert r.status_code == 200
    assert r.json() == {"revisados": 2, "rescatados": 1, "siguen_incompletos": 1}
    stub.rescatar_capturas.assert_awaited_once_with()


class PaginaFalsa:
    def __init__(self, html, url_final):
        self._html, self.url, self.visitadas = html, url_final, []

    def goto(self, url, wait_until=None):
        self.visitadas.append(url)

    def content(self):
        return self._html

    def is_closed(self):
        return False


def _harvester_con(pagina):
    h = LinkedInHarvester(sleep_fn=lambda _s: None)
    h._page = pagina
    return h


def test_recapturar_sync_devuelve_el_post_principal(monkeypatch):
    principal = {"urn": "a", "texto": "Texto largo de la oferta completa", "imagenes": [IMAGEN]}
    otro = {"urn": "b", "texto": "Otro", "imagenes": []}
    monkeypatch.setattr("core.linkedin_harvester.parse_posts_html", lambda _html: [otro, principal])
    pagina = PaginaFalsa("<html></html>", POST_URL)
    assert _harvester_con(pagina)._recapturar_sync(POST_URL) == principal
    assert pagina.visitadas == [POST_URL]


def test_recapturar_sync_con_login_o_sin_posts_devuelve_none(monkeypatch):
    monkeypatch.setattr("core.linkedin_harvester.parse_posts_html", lambda _html: [])
    assert _harvester_con(PaginaFalsa("", POST_URL))._recapturar_sync(POST_URL) is None
    login = PaginaFalsa("", "https://www.linkedin.com/login")
    assert _harvester_con(login)._recapturar_sync(POST_URL) is None


@pytest.mark.asyncio
async def test_recapturar_post_solo_abre_links_de_linkedin():
    h = LinkedInHarvester()
    assert await h.recapturar_post("https://evil.test/post") is None
    assert h._executor is None  # ni siquiera abrió el navegador


def test_aviso_util_no_relee_las_reglas_por_cada_oferta(monkeypatch):
    # 16/11: releer reglas_portales.json por cada oferta corta dejó el dev al 100 % de CPU sin responder
    from core import prioridades, reglas_portales
    lecturas = []
    monkeypatch.setattr(reglas_portales, "leer_reglas_portales",
                        lambda: lecturas.append(1) or ({"indeed": Mock(leer_pagina_aviso=False)}, {}))
    monkeypatch.setattr(prioridades, "_REGLAS_PORTALES_CACHE", None)
    for _ in range(500):
        assert aviso_util({"texto": CORTO_PORTAL, "fuente": "indeed", "triage": {"puesto": "Auxiliar"}})
    assert len(lecturas) == 1
