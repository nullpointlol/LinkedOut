"""Bordes de `core/portales.py`: red simulada (nunca sale a internet), fechas raras y respuestas incompletas."""
import io
import json
import urllib.error
from datetime import date

import pytest

from core import portales as p
from core.portales import ErrorPortal
from tests.portales_prueba import regla

HOY = date(2026, 11, 17)


class _Respuesta(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _urlopen(cuerpo=b"", error=None):
    def abrir(pedido, timeout):
        if error is not None:
            raise error
        return _Respuesta(cuerpo)
    return abrir


def _http(codigo):
    return urllib.error.HTTPError("https://x.test", codigo, "error", {}, None)


def test_clave_jooble_sin_env_legible_usa_el_entorno(monkeypatch):
    monkeypatch.setattr("dotenv.dotenv_values", lambda ruta: (_ for _ in ()).throw(OSError("bloqueado")))
    monkeypatch.setenv(p.VARIABLE_CLAVE_JOOBLE, " clave-entorno ")
    assert p.clave_jooble() == "clave-entorno"


@pytest.mark.parametrize("valor,esperado", [
    (1763337600000, date.fromtimestamp(1763337600)),   # milisegundos (pandas)
    (10 ** 20, None),                                  # fuera de rango
    ("2026-11-15T10:00:00Z", date(2026, 11, 15)),
    ("2026-11-14 basura", date(2026, 11, 14)),         # solo la fecha
    ("ayer", None),
])
def test_fechas_de_las_apis(valor, esperado):
    assert p._fecha(valor) == esperado


def test_post_json_devuelve_estado_y_datos(monkeypatch):
    monkeypatch.setattr(p.urllib.request, "urlopen", _urlopen(json.dumps({"jobs": []}).encode()))
    assert p._post_json("https://x.test", {"a": 1}, 5) == (200, {"jobs": []})
    monkeypatch.setattr(p.urllib.request, "urlopen", _urlopen(error=_http(403)))
    assert p._post_json("https://x.test", {}, 5) == (403, None)


def test_jooble_ignora_filas_raras_y_tapa_la_clave_en_los_errores():
    r = regla("jooble")
    datos = {"jobs": ["no es un aviso", {"id": 7, "title": "Analista", "link": "https://j.test/7",
                                         "updated": "2026-11-16T09:00:00", "snippet": "<b>Hola</b>", "source": "x"}]}
    ofertas = p.buscar_jooble("analista", r, "secreta", post_json=lambda u, c, t: (200, datos), hoy=HOY)
    assert [o["id"] for o in ofertas] == ["7"] and ofertas[0]["descripcion"] == "Hola"

    def falla(url, cuerpo, plazo):
        raise ConnectionError(f"no llegó a {url}")
    with pytest.raises(ErrorPortal) as e:
        p.buscar_jooble("analista", r, "secreta", post_json=falla)
    assert "secreta" not in str(e.value) and "<clave>" in str(e.value)


def test_scrape_jobspy_convierte_el_dataframe(monkeypatch):
    import sys
    import types

    class _DF:
        def to_json(self, **kw):
            return json.dumps([{"id": "in-1", "title": "Analista"}])
    modulo = types.ModuleType("jobspy")
    modulo.scrape_jobs = lambda **kw: _DF()
    monkeypatch.setitem(sys.modules, "jobspy", modulo)
    assert p._scrape_jobspy(site_name=["indeed"]) == [{"id": "in-1", "title": "Analista"}]


@pytest.mark.parametrize("texto,esperado", [
    ("", None), ("Publicado hoy", HOY), ("Hace 3 horas", HOY), ("Hace 2 semanas", date(2026, 11, 3)),
    ("Hace 1 mes", date(2026, 10, 18)), ("hace mucho", None),
])
def test_fecha_relativa(texto, esperado):
    assert p.fecha_relativa(texto, HOY) == esperado


def test_computrabajo_aviso_sin_link_y_detalle_sin_descripcion():
    pagina = '<article class="box" data-id="ABC123"><p>sin link</p></article>'
    assert p.avisos_computrabajo(pagina, "https://ar.computrabajo.test") == []
    assert p.detalle_computrabajo("<html>otra cosa</html>") == ""


@pytest.mark.parametrize("error,mensaje", [
    (_http(429), "rechazó la consulta (429)"), (_http(500), "respondió 500"),
    (urllib.error.URLError("caído"), "no respondió: URLError"), (TimeoutError(), "no respondió: TimeoutError"),
])
def test_get_html_errores(monkeypatch, error, mensaje):
    monkeypatch.setattr(p.urllib.request, "urlopen", _urlopen(error=error))
    with pytest.raises(ErrorPortal, match=mensaje.replace("(", r"\(").replace(")", r"\)")):
        p._get_html("https://x.test", 5)


def test_get_html_y_get_json_leen_la_respuesta(monkeypatch):
    monkeypatch.setattr(p.urllib.request, "urlopen", _urlopen("<p>ñ</p>".encode()))
    assert p._get_html("https://x.test", 5) == "<p>ñ</p>"
    monkeypatch.setattr(p.urllib.request, "urlopen", _urlopen(b'{"jobs": []}'))
    assert p._get_json("https://x.test", 5) == {"jobs": []}


@pytest.mark.parametrize("error,mensaje", [(_http(404), "respondió 404"), (ValueError("json roto"), "no respondió")])
def test_get_json_errores(monkeypatch, error, mensaje):
    monkeypatch.setattr(p.urllib.request, "urlopen", _urlopen(error=error))
    with pytest.raises(ErrorPortal, match=mensaje):
        p._get_json("https://x.test", 5)


@pytest.mark.parametrize("portal,buscar", [("remotive", p.buscar_remotive), ("himalayas", p.buscar_himalayas)])
def test_apis_sin_lista_de_ofertas(portal, buscar):
    with pytest.raises(ErrorPortal, match="sin la lista"):
        buscar("", regla(portal), get_json=lambda url, plazo: {"otra": 1}, hoy=HOY)


def test_api_con_ubicaciones_en_lista_epoch_en_segundos_y_tope():
    r = regla("himalayas", resultados_por_consulta=1)
    filas = {"jobs": ["rara", {"guid": "h1", "title": "Data Analyst", "companyName": "Acme", "locationRestrictions": [],
                               "description": "<p>Uno</p><p>Dos</p>", "applicationLink": "https://h.test/1",
                               "pubDate": 1763337600},
                      {"guid": "h2", "title": "Otro", "applicationLink": "https://h.test/2", "pubDate": 1763337600}]}
    ofertas = p.buscar_himalayas("data", r, get_json=lambda url, plazo: filas, hoy=date.fromtimestamp(1763337600))
    assert len(ofertas) == 1 and ofertas[0]["ubicacion"] == "Remoto" and ofertas[0]["descripcion"] == "Uno\nDos"
