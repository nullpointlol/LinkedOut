import pytest
import re


def test_render_matriz_oferta_evaluates_item_estados():
    with open("static/app.js", "r", encoding="utf-8") as f:
        app_js = f.read()

    # Extraer la función renderMatrizOferta
    fn_match = re.search(r"function renderMatrizOferta\([\s\S]*?\n\}", app_js)
    assert fn_match is not None, "renderMatrizOferta debe existir en app.js"
    fn_body = fn_match.group(0)

    # Verificar que evalúa los 3 estados taxonómicos de la matriz
    assert "MATCH_DIRECTO" in fn_body
    assert "TRANSFERIBLE" in fn_body
    assert "BRECHA_REAL" in fn_body

    # Rediseño E4: cada estado con su clase propia y su token (coincide = éxito, transferible = atención, falta = error)
    css = open("static/css/editor.css", encoding="utf-8").read()
    for clase, token in (("req-ok", "--success"), ("req-transf", "--warning"), ("req-falta", "--danger")):
        assert clase in fn_body, clase
        regla = css[css.index(f".{clase} {{"):]
        assert f"var({token})" in regla[:regla.index("}")], clase


def test_render_matriz_oferta_displays_brechas_criticas_summary():
    with open("static/app.js", "r", encoding="utf-8") as f:
        app_js = f.read()

    fn_match = re.search(r"function renderMatrizOferta\([\s\S]*?\n\}", app_js)
    assert fn_match is not None
    fn_body = fn_match.group(0)

    # Verificar bloque o resumen de brechas críticas vs vacante
    assert "Brechas Críticas" in fn_body or "brechas" in fn_body.lower()
    # Debe contar o filtrar items según su estado
    assert "BRECHA_REAL" in fn_body
    # Debe incluir indicadores visuales (rediseño E4: íconos del sprite en vez de Font Awesome)
    assert "iconoSvg('check')" in fn_body
    assert "'refresh-cw'" in fn_body
    assert "'triangle-alert'" in fn_body


def test_index_html_has_matriz_container():
    with open("static/index.html", "r", encoding="utf-8") as f:
        index_html = f.read()

    assert "id=\"viewCentralMatriz\"" in index_html
    assert "id=\"contenedorMatrizOferta\"" in index_html
