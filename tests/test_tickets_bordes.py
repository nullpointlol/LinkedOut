"""Bordes de `core/tickets.py` que no tocan la base: archivos adjuntos, pasos y filtros del registro."""
import base64
import logging

from core import tickets as t


def test_cargar_json_devuelve_el_defecto_si_no_se_puede():
    assert t._cargar_json("{roto", {"x": 1}) == {"x": 1}
    assert t._cargar_json("", []) == [] and t._cargar_json('{"a": 2}', None) == {"a": 2}


def test_pasos_de_una_oferta_vista_desde_el_navegador():
    pasos = t.armar_pasos("navegador", "error_js", "falló", None, {"vista": "manual", "oferta_id": 9})
    assert "Abrir la oferta #9." in pasos and "Resultado: falló" in pasos


def test_captura_en_bytes_ruta_y_data_url(tmp_path):
    destino = tmp_path / "t" / "captura.png"
    assert t._guardar_captura(destino, b"PNG") and destino.read_bytes() == b"PNG"
    assert not t._guardar_captura(tmp_path / "x.png", tmp_path / "no-existe.png")
    assert not t._guardar_captura(tmp_path / "x.png", "data:image/png;base64")  # sin coma: sin datos
    assert not t._guardar_captura(tmp_path / "x.png", "data:image/png;base64,")  # vacía
    datos = "data:image/png;base64," + base64.b64encode(b"img").decode()
    assert t._guardar_captura(tmp_path / "y.png", datos) and (tmp_path / "y.png").read_bytes() == b"img"


def test_html_desde_ruta_bytes_o_texto(tmp_path):
    assert not t._guardar_html(tmp_path / "a.html", tmp_path / "no-existe.html")
    assert t._guardar_html(tmp_path / "b.html", "<p>hola</p>".encode()) and "hola" in (tmp_path / "b.html").read_text(
        encoding="utf-8")
    assert not t._guardar_html(tmp_path / "c.html", "")


def test_el_buffer_del_log_no_rompe_con_un_mensaje_invalido():
    registro = logging.LogRecord("core.x", logging.INFO, __file__, 1, "%s %s", ("solo uno",), None)
    t._BufferLog().emit(registro)  # getMessage falla: se ignora


def test_tickets_del_log_ignoran_otros_loggers_y_los_reentrantes(monkeypatch):
    llamados = []
    real = t.reportar_error
    monkeypatch.setattr(t, "reportar_error", lambda *a, **k: llamados.append(a))
    manejador = t._TicketsDelLog()
    manejador.emit(logging.LogRecord("urllib3", logging.ERROR, __file__, 1, "ajeno", None, None))
    t._dentro.activo = True
    try:
        manejador.emit(logging.LogRecord("core.radar", logging.ERROR, __file__, 1, "adentro", None, None))
        assert real("radar", "error", "reentrante") == 0
    finally:
        t._dentro.activo = False
    assert llamados == []
    monkeypatch.setattr(t, "reportar_error", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("base caída")))
    manejador.emit(logging.LogRecord("core.radar", logging.ERROR, __file__, 1, "sin base", None, None))  # no propaga
