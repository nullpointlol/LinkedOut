"""/static se sirve con «Cache-Control: no-cache»: el navegador revalida cada archivo con su
ETag, así un index.html nuevo nunca convive con un app.js viejo de la caché, y si el archivo no cambió la respuesta
es un 304 sin cuerpo (no se pierde rendimiento).

Medidas: los JS y CSS del portal llegan con no-cache y ETag; con If-None-Match del mismo ETag, 304 con no-cache.
"""
import pytest
from fastapi.testclient import TestClient

import server


@pytest.fixture
def cliente():
    return TestClient(server.app)


@pytest.mark.parametrize("ruta", ["/static/app.js", "/static/css/tokens.css", "/static/iconos.svg"])
def test_static_con_no_cache_y_etag(cliente, ruta):
    r = cliente.get(ruta)
    assert r.status_code == 200
    assert r.headers.get("cache-control") == "no-cache"
    assert r.headers.get("etag")


def test_static_sin_cambios_responde_304_sin_cuerpo(cliente):
    etag = cliente.get("/static/app.js").headers["etag"]
    r = cliente.get("/static/app.js", headers={"If-None-Match": etag})
    assert r.status_code == 304 and not r.content
    assert r.headers.get("cache-control") == "no-cache"


def test_archivo_inexistente_sigue_en_404(cliente):
    assert cliente.get("/static/no-existe.js").status_code == 404
