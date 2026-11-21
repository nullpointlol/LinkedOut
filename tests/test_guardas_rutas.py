"""Superficie del portal (S1, repo público): toda la API queda solo para la PC o un celular emparejado.

- Inventario: recorre `app.routes` y falla si una ruta no-GET, o cualquier GET de `/api`, queda sin guarda.
  Lo que queda abierto está en una lista blanca explícita, cada entrada con su motivo.
- Comportamiento: desde afuera, 403 sin efectos; desde la PC y desde el celular emparejado, igual que antes.
- CORS: ninguna respuesta lo abre (ni `*` ni un portal); la API la usa solo el propio portal.
"""
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
import pytest
from starlette.routing import Mount, Route

import server
from core import acceso_remoto, radar_store
from core.radar_api import ESTADO_HOSTS_DE_TEST, SOLO_LOCAL, _solo_operacion_local, _solo_pc_o_emparejado

# Rutas abiertas a propósito (método, path) y por qué
ABIERTAS = {
    ("GET", "/"): "la página del portal: sin datos, los pide por la API",
    ("GET", "/static"): "JS, CSS e imágenes del portal: sin datos del candidato",
    ("GET", "/sw.js"): "service worker de la app de escritorio: código público, sin datos ni caché de la API",
    ("GET", "/openapi.json"): "esquema de la API: es el código público, sin datos",
    ("GET", "/docs"): "documentación de la API generada del esquema",
    ("GET", "/docs/oauth2-redirect"): "parte de la documentación de FastAPI",
    ("GET", "/redoc"): "documentación de la API generada del esquema",
    ("GET", "/api/radar/emparejar"): "el celular aún no emparejado llega con la clave del QR; la ruta valida la clave",
}
# Rutas con guarda propia (no la general) y por qué
GUARDA_PROPIA: dict = {}
METODOS_LECTURA = {"GET", "HEAD"}


def _llamadas(dependant) -> set:
    """Todas las funciones del árbol de dependencias de una ruta."""
    vistas, pila = set(), [dependant]
    while pila:
        d = pila.pop()
        if d.call is not None:
            vistas.add(d.call)
        pila.extend(d.dependencies)
    return vistas


def _inventario(rutas=None):
    """(método, path, funciones de dependencias) de cada ruta efectiva de la app.

    FastAPI 0.142 incluye los routers en forma diferida (`_IncludedRouter`): se recorren sus rutas efectivas,
    que ya traen el prefijo y las dependencias del router. Un tipo de ruta desconocido hace fallar el test."""
    for ruta in server.app.routes if rutas is None else rutas:
        if hasattr(ruta, "effective_candidates"):
            yield from _inventario(ruta.effective_candidates())
        elif isinstance(ruta, APIRoute) or hasattr(ruta, "dependant"):
            for metodo in sorted(ruta.methods):
                yield metodo, ruta.path, _llamadas(ruta.dependant)
        elif isinstance(ruta, Mount):
            yield "GET", ruta.path, set()
        elif isinstance(ruta, Route):
            for metodo in sorted(ruta.methods or {"GET"}):
                yield metodo, ruta.path, set()
        else:
            raise AssertionError(f"Tipo de ruta desconocido para el inventario: {type(ruta).__name__}")


def test_inventario_toda_ruta_tiene_guarda_o_esta_en_la_lista_blanca():
    sin_guarda = []
    vistas = set()
    for metodo, path, llamadas in _inventario():
        if metodo == "HEAD":
            continue
        vistas.add((metodo, path))
        if (metodo, path) in ABIERTAS:
            continue
        if (metodo, path) in GUARDA_PROPIA:
            nombre = GUARDA_PROPIA[(metodo, path)][0]
            if not any(getattr(c, "__name__", "") == nombre for c in llamadas):
                sin_guarda.append(f"{metodo} {path} (falta {nombre})")
            continue
        if _solo_pc_o_emparejado in llamadas:
            continue
        if metodo not in METODOS_LECTURA and _solo_operacion_local in llamadas:
            continue
        sin_guarda.append(f"{metodo} {path}")
    assert not sin_guarda, "Rutas sin guarda:\n" + "\n".join(sin_guarda)
    # La lista blanca no guarda rutas que ya no existen
    assert set(ABIERTAS) | set(GUARDA_PROPIA) <= vistas


def test_inventario_cubre_las_rutas_que_tenian_el_hueco():
    """Las rutas de la auditoría (S1) existen y entran en el recorrido."""
    vistas = {(m, p) for m, p, _ in _inventario()}
    for esperada in [("POST", "/api/ofertas"), ("POST", "/api/ofertas/url"), ("POST", "/api/ofertas/batch"),
                     ("DELETE", "/api/ofertas/{postulacion_id}"), ("POST", "/api/model-config"),
                     ("POST", "/api/ofertas/{postulacion_id}/regenerate"), ("GET", "/api/stream"),
                     ("GET", "/api/ofertas/{postulacion_id}/export/pdf"), ("GET", "/api/candidato/aprendizajes"),
                     ("POST", "/api/candidato/expand/ask"), ("GET", "/api/tickets")]:
        assert esperada in vistas, esperada


# ----------------------------------------------------------------- comportamiento

@pytest.fixture
def como_en_produccion(monkeypatch):
    """Sin el hook de tests: TestClient no cuenta como local."""
    monkeypatch.setattr(server.app.state, ESTADO_HOSTS_DE_TEST, set(), raising=False)


@pytest.fixture
def clave_celular(tmp_path, monkeypatch):
    monkeypatch.setattr(acceso_remoto, "ARCHIVO_CLAVE", tmp_path / "acceso_celular.key")
    monkeypatch.setattr(acceso_remoto, "ip_lan", lambda: "192.168.1.50")
    return acceso_remoto.clave()


@pytest.fixture
def sin_despertar(monkeypatch):
    from unittest.mock import AsyncMock
    monkeypatch.setattr(server.radar_loop, "wake_processing", AsyncMock())


def _pc():
    return TestClient(server.app, base_url="http://localhost:1111", client=("127.0.0.1", 51000))


def _afuera():
    return TestClient(server.app, base_url="http://192.168.1.50:1111", client=("192.168.1.51", 51000))


def _celular(clave):
    c = _afuera()
    c.cookies.set(acceso_remoto.COOKIE, clave)
    return c


LECTURAS_CON_DATOS = [
    "/api/ofertas", "/api/ofertas/2", "/api/ofertas/2/export/pdf", "/api/ofertas/2/export/docx",
    "/api/candidato/aprendizajes", "/api/candidato/perfil/personales", "/api/candidato/busquedas",
    "/api/candidato/expand", "/api/candidato/puntaje-bajo", "/api/candidato/anios-por-area",
    "/api/radar/posts", "/api/radar/status", "/api/tickets", "/api/model-info", "/api/stream",
]


@pytest.mark.parametrize("path", LECTURAS_CON_DATOS)
def test_lecturas_con_datos_rechazadas_desde_afuera(como_en_produccion, path):
    r = _afuera().get(path)
    assert r.status_code == 403, r.text
    assert r.json()["detail"].startswith(SOLO_LOCAL)


@pytest.mark.parametrize("headers", [{"CF-Connecting-IP": "200.1.2.3"}, {"Sec-Fetch-Site": "cross-site"}])
def test_lecturas_rechazadas_por_tunel_sin_emparejar_o_cross_site(como_en_produccion, headers):
    assert _pc().get("/api/ofertas", headers=headers).status_code == 403


@pytest.mark.parametrize("path", ["/api/ofertas", "/api/candidato/aprendizajes", "/api/tickets", "/api/radar/posts"])
def test_lecturas_siguen_para_la_pc_y_el_celular_emparejado(como_en_produccion, clave_celular, path):
    assert _pc().get(path, headers={"Sec-Fetch-Site": "same-origin"}).status_code == 200
    assert _celular(clave_celular).get(path).status_code == 200


def test_pagina_y_estaticos_siguen_abiertos(como_en_produccion):
    assert _afuera().get("/").status_code == 200
    assert _afuera().get("/static/app.js").status_code == 200


def test_altas_desde_afuera_rechazadas_sin_efecto(como_en_produccion, sin_despertar):
    antes = len(radar_store.list_posts())
    c = _afuera()
    assert c.post("/api/ofertas", json={"contenido": "Oferta de prueba", "url": ""}).status_code == 403
    assert c.post("/api/ofertas/url", json={"url": "https://example.com/oferta/1"}).status_code == 403
    assert c.post("/api/ofertas/batch", json={"urls": ["https://example.com/oferta/2"]}).status_code == 403
    assert c.delete("/api/ofertas/2").status_code == 403
    assert c.post("/api/model-config", json={}).status_code == 403
    assert len(radar_store.list_posts()) == antes


def test_altas_siguen_para_la_pc_y_el_celular_emparejado(como_en_produccion, clave_celular, sin_despertar):
    r = _pc().post("/api/ofertas", json={"contenido": "Oferta de prueba desde la PC", "url": ""},
                   headers={"Origin": "http://localhost:1111"})
    assert r.status_code == 200, r.text
    r = _celular(clave_celular).post("/api/ofertas", json={"contenido": "Oferta desde el celular", "url": ""},
                                     headers={"Origin": "http://192.168.1.50:1111"})
    assert r.status_code == 200, r.text


# ----------------------------------------------------------------- CORS

@pytest.mark.parametrize("metodo, path", [
    ("GET", "/"), ("GET", "/api/ofertas"), ("OPTIONS", "/api/ofertas"), ("POST", "/api/ofertas"),
    ("GET", "/api/radar/status"), ("OPTIONS", "/api/radar/status"),
])
def test_ninguna_respuesta_abre_cors_a_cualquier_origen(como_en_produccion, sin_despertar, metodo, path):
    """Ninguna respuesta lleva `Access-Control-Allow-Origin: *`, ni desde la PC ni desde afuera."""
    for cliente in (_pc, _afuera):
        r = cliente().request(metodo, path, json={"contenido": "x"} if metodo == "POST" else None,
                              headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
        assert r.headers.get("access-control-allow-origin") != "*", (metodo, path, dict(r.headers))


# ----------------------------------------------------------------- herramientas quitadas (S5)

RUTAS_QUITADAS = [
    ("POST", "/api/ofertas/clipper"), ("OPTIONS", "/api/ofertas/clipper"), ("GET", "/api/toolkit/bookmarklet"),
    ("GET", "/api/ofertas/2/toolkit/interview-prep"), ("POST", "/api/ofertas/2/toolkit/interview-prep"),
    ("GET", "/api/ofertas/2/toolkit/networking"), ("POST", "/api/ofertas/2/toolkit/networking"),
]


@pytest.mark.parametrize("metodo, path", RUTAS_QUITADAS)
def test_las_herramientas_quitadas_ya_no_responden(sin_despertar, metodo, path):
    """El menú «Herramientas» se fue con sus rutas: no crean nada ni abren CORS a ningún portal."""
    antes = len(radar_store.list_posts())
    r = _pc().request(metodo, path, json={"contenido": "Detalle"} if metodo == "POST" else None,
                      headers={"Origin": "https://www.linkedin.com", "Access-Control-Request-Method": "POST"})
    assert r.status_code in (404, 405), (metodo, path, r.status_code)
    assert "access-control-allow-origin" not in r.headers
    assert len(radar_store.list_posts()) == antes


# ----------------------------------------------------------------- viewport

def test_viewport_permite_zoom():
    html = (server.STATIC_DIR / "index.html").read_text(encoding="utf-8")
    linea = next(l for l in html.splitlines() if 'name="viewport"' in l)
    assert "user-scalable=no" not in linea and "maximum-scale" not in linea
    assert "width=device-width" in linea


# ----------------------------------------------------------------- backup al borrar un aprendizaje

def test_borrar_aprendizaje_respalda_solo_el_archivo_de_aprendizajes(tmp_path, monkeypatch):
    from core import aprendizajes, paths, perfil_candidato
    datos = tmp_path / "datos"
    datos.mkdir()
    archivo = datos / "aprendizajes_candidato.json"
    archivo.write_text('{"docker": {"estado": "TRANSFERIBLE"}, "sql": {"estado": "MATCH_DIRECTO"}}', encoding="utf-8")
    (datos / "base.db").write_bytes(b"base que no se respalda")
    (datos / ".env").write_text("CLAVE=no-se-copia", encoding="utf-8")
    monkeypatch.setattr(paths, "DATA_DIR", datos)
    monkeypatch.setattr(aprendizajes, "APRENDIZAJES_FILE", archivo)
    original = archivo.read_bytes()

    nombre = perfil_candidato.borrar_aprendizaje("docker")["backup"]

    respaldo = datos / "backups" / nombre
    assert sorted(p.name for p in respaldo.iterdir()) == ["aprendizajes_candidato.json"]
    assert (respaldo / "aprendizajes_candidato.json").read_bytes() == original
    assert "docker" not in aprendizajes.obtener_aprendizajes()
