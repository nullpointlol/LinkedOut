"""Tickets de errores: módulo `core/tickets.py` (dedup, secretos, archivos, contexto) y su API."""
import base64
import logging

import pytest
from fastapi.testclient import TestClient

import server
from core import database, tickets

PNG_1x1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


@pytest.fixture
def cliente():
    return TestClient(server.app, raise_server_exceptions=False)


def _carpeta(tid):
    return tickets.carpeta_tickets() / str(tid)


# ---------------------------------------------------------------- módulo

def test_reportar_crea_ticket_con_todo_lo_basico():
    try:
        raise ValueError("se rompió el parser")
    except ValueError as e:
        tid = tickets.reportar_error("radar", "lectura_fallida", "Error leyendo la consulta 'cv a'",
                                     excepcion=e, datos={"consulta": "cv a", "cuenta": "secundaria"})
    t = tickets.obtener(tid)
    assert t["estado"] == "abierto" and t["ocurrencias"] == 1
    assert t["componente"] == "radar" and t["tipo"] == "lectura_fallida"
    assert "ValueError: se rompió el parser" in t["traza"] and "Traceback" in t["traza"]
    assert t["datos"] == {"consulta": "cv a", "cuenta": "secundaria"}
    assert t["creado_at"] and t["ultima_vez"]
    assert t["commit"]  # git rev-parse HEAD del código que corre (o «desconocido» fuera de git)
    assert "cv a" in t["pasos"] and "secundaria" in t["pasos"]


def test_el_mismo_error_suma_ocurrencias_sin_duplicar():
    a = tickets.reportar_error("radar", "analisis_fallido", "Falló el análisis del post 12: timeout", datos={"post_id": 12})
    b = tickets.reportar_error("radar", "analisis_fallido", "Falló el análisis del post 15:  Timeout", datos={"post_id": 15})
    assert a == b
    t = tickets.obtener(a)
    assert t["ocurrencias"] == 2
    assert [o["datos"]["post_id"] for o in t["ocurrencias_detalle"]] == [12, 15]
    otro = tickets.reportar_error("radar", "envio_fallido", "Falló el análisis del post 12: timeout")
    assert otro != a


def test_un_error_resuelto_que_vuelve_reabre_el_mismo_ticket(monkeypatch):
    a = tickets.reportar_error("ia", "falla_agy", "AGY no respondió")
    tickets.cambiar_estado(a, "resuelto")
    monkeypatch.setattr(tickets, "commit_desplegado", lambda: "commitnuevo")
    b = tickets.reportar_error("ia", "falla_agy", "AGY no respondió")
    assert b == a
    t = tickets.obtener(a)
    assert t["estado"] == "abierto" and t["resuelto_at"] is None
    assert t["ocurrencias"] == 2 and t["reaperturas"] == 1
    assert t["commit"] == "commitnuevo"  # el commit donde volvió, no el viejo
    assert t["ocurrencias_detalle"][-1].get("reapertura") is True
    assert "Reabierto 1 vez" in tickets.texto_ticket(tickets.obtener(a))
    assert len(tickets.listar(estado="")) == 1
    tickets.cambiar_estado(a, "resuelto")
    tickets.reportar_error("ia", "falla_agy", "AGY no respondió")
    assert tickets.obtener(a)["reaperturas"] == 2 and "Reabierto 2 veces" in tickets.texto_ticket(tickets.obtener(a))


def test_base_vieja_sin_columna_reaperturas_se_migra():
    import sqlite3
    tickets.carpeta_tickets().mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(tickets.carpeta_tickets() / "tickets.db")
    conn.execute(tickets._TABLA.replace("    reaperturas INTEGER NOT NULL DEFAULT 0,\n", ""))
    conn.commit()
    conn.close()
    tid = tickets.reportar_error("x", "y", "z")
    tickets.cambiar_estado(tid, "resuelto")
    assert tickets.reportar_error("x", "y", "z") == tid
    assert tickets.obtener(tid)["reaperturas"] == 1


def test_estados_validos():
    tid = tickets.reportar_error("x", "y", "z")
    tickets.cambiar_estado(tid, "en_curso")
    assert tickets.obtener(tid)["estado"] == "en_curso"
    with pytest.raises(ValueError):
        tickets.cambiar_estado(tid, "arreglado")
    assert tickets.cambiar_estado(999999, "resuelto") is False


def test_tapa_secretos_en_peticion_y_datos():
    tid = tickets.reportar_error("api", "respuesta_4xx", "422", peticion={
        "metodo": "POST", "ruta": "/api/radar/gmail", "cuerpo": {
            "usuario": "sam@ejemplo.test", "app_password": "abcd efgh", "nested": {"Token": "t0k", "x": 1},
            "lista": [{"password": "p"}]},
        "consulta": "token=secreto&a=1",
    }, datos={"cookie": "li_at=xyz", "post_id": 3})
    t = tickets.obtener(tid)
    texto = str(t["peticion"]) + str(t["datos"])
    for secreto in ("abcd efgh", "t0k", "li_at=xyz", "secreto", "'p'"):
        assert secreto not in texto
    assert t["peticion"]["cuerpo"]["usuario"] == "sam@ejemplo.test"
    assert t["peticion"]["cuerpo"]["app_password"] == "***"
    assert t["datos"]["post_id"] == 3


def test_tapa_secretos_en_cuerpo_de_texto():
    assert "hunter2" not in tickets.tapar_secretos("password=hunter2&usuario=a")
    assert "hunter2" not in tickets.tapar_secretos('{"password": "hunter2"}')
    assert "hunter2" not in tickets.tapar_secretos("Authorization: Bearer hunter2")


def test_guarda_captura_y_html_en_la_carpeta_del_ticket(tmp_path):
    png = tmp_path / "screenshot.png"
    png.write_bytes(PNG_1x1)
    html = tmp_path / "page.html"
    html.write_text("<html><body>sin resultados</body></html>", encoding="utf-8")
    tid = tickets.reportar_error("radar", "bloqueo", "LinkedIn pausó la cuenta", captura=png, html=html)
    t = tickets.obtener(tid)
    assert (_carpeta(tid) / "captura.png").read_bytes() == PNG_1x1
    assert "sin resultados" in (_carpeta(tid) / "pagina.html").read_text(encoding="utf-8")
    assert t["tiene_captura"] and t["tiene_html"]


def test_captura_como_data_url_y_html_como_texto():
    url = "data:image/png;base64," + base64.b64encode(PNG_1x1).decode()
    tid = tickets.reportar_error("navegador", "reporte_usuario", "No carga", captura=url, html="<p>hola</p>")
    assert (_carpeta(tid) / "captura.png").read_bytes() == PNG_1x1
    assert (_carpeta(tid) / "pagina.html").read_text(encoding="utf-8") == "<p>hola</p>"


def test_incluye_ultimas_lineas_de_log():
    logging.getLogger("core.algo").warning("una advertencia previa al error")
    tid = tickets.reportar_error("x", "y", "z")
    assert "una advertencia previa al error" in tickets.obtener(tid)["log"]


def test_nunca_lanza_aunque_falle_la_base(monkeypatch):
    def roto(*a, **k):
        raise RuntimeError("base rota")
    monkeypatch.setattr(tickets, "_guardar", roto)
    assert tickets.reportar_error("x", "y", "z") == 0


def test_los_errores_registrados_se_vuelven_tickets():
    logging.getLogger("core.radar").error("[Radar] Error inesperado en el ciclo 77")
    lista = tickets.listar(componente="radar")
    assert any("Error inesperado en el ciclo" in t["mensaje"] for t in lista)


def test_listar_filtra_y_resumen_cuenta():
    tickets.reportar_error("radar", "bloqueo", "a")
    b = tickets.reportar_error("ia", "falla_agy", "b")
    tickets.cambiar_estado(b, "resuelto")
    assert {t["componente"] for t in tickets.listar(estado="abierto")} >= {"radar"}
    assert all(t["estado"] == "resuelto" for t in tickets.listar(estado="resuelto"))
    assert [t["tipo"] for t in tickets.listar(tipo="falla_agy")] == ["falla_agy"]
    r = tickets.resumen()
    assert r["sin_resolver"] >= 1 and r["resuelto"] == 1
    assert "radar" in r["componentes"] and "falla_agy" in r["tipos"]


def test_texto_para_copiar_tiene_todo():
    try:
        raise KeyError("falta")
    except KeyError as e:
        tid = tickets.reportar_error("api", "excepcion", "KeyError: 'falta'", excepcion=e,
                                     peticion={"metodo": "GET", "ruta": "/api/x", "cuerpo": None})
    texto = tickets.texto_ticket(tickets.obtener(tid))
    for pedazo in (f"Ticket #{tid}", "Componente: api", "Tipo: excepcion", "KeyError", "GET /api/x",
                   "Pasos para reproducir", "Commit:", "Últimas líneas del log"):
        assert pedazo in texto


# ---------------------------------------------------------------- servidor

def test_excepcion_no_manejada_genera_ticket_con_traza_y_peticion(cliente, monkeypatch):
    def explota(*a, **k):
        raise ZeroDivisionError("división rota")
    monkeypatch.setattr(server, "list_postulaciones", explota)
    r = cliente.get("/api/ofertas?filtro_lifecycle=POSTULADA")
    assert r.status_code == 500
    lista = [t for t in tickets.listar() if "división rota" in t["mensaje"]]
    assert len(lista) == 1
    t = tickets.obtener(lista[0]["id"])
    assert "ZeroDivisionError" in t["traza"]
    assert t["peticion"]["ruta"] == "/api/ofertas" and t["peticion"]["metodo"] == "GET"
    assert "filtro_lifecycle=POSTULADA" in t["peticion"]["consulta"]
    assert "GET /api/ofertas" in t["pasos"]


def test_respuesta_4xx_de_la_api_genera_ticket_con_cuerpo_sin_secretos(cliente):
    r = cliente.post("/api/ofertas", json={"titulo": 1, "password": "hunter2"})
    assert 400 <= r.status_code < 500
    lista = [t for t in tickets.listar() if t["tipo"] == "respuesta_4xx"]
    assert lista
    t = tickets.obtener(lista[0]["id"])
    assert t["peticion"]["ruta"] == "/api/ofertas" and t["peticion"]["estado_http"] == r.status_code
    assert "hunter2" not in str(t["peticion"])
    assert t["peticion"]["respuesta"]


def test_404_de_ruta_inexistente_no_genera_ticket(cliente):
    cliente.get("/wp-login.php")
    cliente.get("/api/no-existe")
    assert not [t for t in tickets.listar() if t["tipo"].startswith("respuesta_")]


def test_404_de_ruta_existente_si_genera_ticket(cliente):
    assert cliente.get("/api/ofertas/987654").status_code == 404
    assert [t for t in tickets.listar() if t["tipo"] == "respuesta_4xx"]


def test_api_tickets_crear_listar_detalle_estado(cliente):
    url = "data:image/png;base64," + base64.b64encode(PNG_1x1).decode()
    r = cliente.post("/api/tickets", json={
        "tipo": "reporte_usuario", "mensaje": "El botón Aprobar no hace nada", "componente": "navegador",
        "datos": {"vista": "radar", "post_id": 4, "consola": ["error x"], "llamadas": [{"url": "/api/radar/posts", "estado": 200}]},
        "captura": url, "html": "<html>pantalla</html>",
    })
    assert r.status_code == 200
    tid = r.json()["id"]
    assert cliente.get("/api/tickets/resumen").json()["sin_resolver"] >= 1
    assert [t["id"] for t in cliente.get("/api/tickets?tipo=reporte_usuario").json()] == [tid]
    d = cliente.get(f"/api/tickets/{tid}").json()
    assert d["datos"]["post_id"] == 4 and "El botón Aprobar" in d["texto"]
    assert cliente.get(f"/api/tickets/{tid}/captura").content == PNG_1x1
    h = cliente.get(f"/api/tickets/{tid}/html")
    assert h.headers["content-type"].startswith("text/plain") and "pantalla" in h.text
    assert cliente.post(f"/api/tickets/{tid}/estado", json={"estado": "en_curso"}).json()["estado"] == "en_curso"
    assert cliente.post(f"/api/tickets/{tid}/estado", json={"estado": "nada"}).status_code == 422
    assert cliente.get("/api/tickets/999999").status_code == 404
    assert cliente.get(f"/api/tickets/{tid}/captura").status_code == 200


def test_api_tickets_no_genera_tickets_de_si_misma(cliente):
    cliente.post("/api/tickets", json={"mensaje": ""})
    cliente.get("/api/tickets/999999")
    assert not [t for t in tickets.listar() if t["tipo"].startswith("respuesta_")]


def test_api_tickets_rechaza_mensaje_vacio_o_enorme(cliente):
    assert cliente.post("/api/tickets", json={"mensaje": "  "}).status_code == 422
    assert cliente.post("/api/tickets", json={"mensaje": "x" * 20001}).status_code == 422


def test_el_log_del_ticket_incluye_las_peticiones_de_uvicorn():
    acceso = logging.getLogger("uvicorn.access")
    previo = acceso.level
    # setLevel (no asignar `.level`): limpia el caché de niveles de logging. Si un test anterior levantó uvicorn
    # con log_level="warning", el caché seguía diciendo «INFO apagado» y la línea se descartaba.
    acceso.setLevel(logging.INFO)  # como lo configura uvicorn
    try:
        tickets.capturar_logs_de_uvicorn()
        tickets.capturar_logs_de_uvicorn()
        assert acceso.handlers.count(tickets._BUFFER) == 1
        acceso.info('127.0.0.1:5000 - "GET /api/radar/posts HTTP/1.1" 200')
        tid = tickets.reportar_error("x", "y", "z")
        assert "GET /api/radar/posts" in tickets.obtener(tid)["log"]
    finally:
        acceso.setLevel(previo)
