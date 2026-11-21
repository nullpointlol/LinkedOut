"""M4 de docs/seguridad/auditoria-20261117.md: ninguna respuesta del portal se puede mostrar en un iframe."""
import pytest
from fastapi import FastAPI, WebSocket
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient

import server
from core.cabeceras_seguridad import CABECERAS, CabecerasSeguridad

ESPERADAS = {"x-frame-options": "DENY", "content-security-policy": "frame-ancestors 'none'"}


@pytest.fixture
def cliente():
    return TestClient(server.app)


@pytest.mark.parametrize("ruta", ["/", "/static/app.js", "/static/vendor/marked-18.2.1/marked.umd.js",
                                  "/api/ofertas", "/api/radar/status", "/no-existe"])
def test_toda_respuesta_prohibe_el_iframe(cliente, ruta):
    # Vale para 200, 403 (guarda) y 404 por igual: el iframe se bloquea antes de ver el contenido
    r = cliente.get(ruta)
    for nombre, valor in ESPERADAS.items():
        assert r.headers.get(nombre) == valor, (ruta, r.status_code, nombre)


def test_solo_frame_ancestors_sin_csp_de_scripts():
    # La CSP de scripts espera al rediseño (los onclick inline la romperían)
    assert CABECERAS["Content-Security-Policy"] == "frame-ancestors 'none'"


def test_no_pisa_una_cabecera_que_la_ruta_ya_puso():
    app = FastAPI()
    app.add_middleware(CabecerasSeguridad)

    @app.get("/propia")
    def propia():
        return PlainTextResponse("x", headers={"X-Frame-Options": "SAMEORIGIN"})

    r = TestClient(app).get("/propia")
    assert r.headers["x-frame-options"] == "SAMEORIGIN"
    assert r.headers["content-security-policy"] == "frame-ancestors 'none'"


def test_websocket_y_lifespan_pasan_sin_tocar():
    app = FastAPI()
    app.add_middleware(CabecerasSeguridad)

    @app.websocket("/ws")
    async def ws(socket: WebSocket):
        await socket.accept()
        await socket.send_text("hola")
        await socket.close()

    with TestClient(app) as c, c.websocket_connect("/ws") as s:
        assert s.receive_text() == "hola"
