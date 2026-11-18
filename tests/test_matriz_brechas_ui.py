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

    # Verificar clases cromáticas de bajo contraste para cada estado
    assert "text-emerald-" in fn_body or "bg-emerald-" in fn_body
    assert "text-amber-" in fn_body or "bg-amber-" in fn_body
    assert "text-rose-" in fn_body or "bg-rose-" in fn_body


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
    # Debe incluir indicadores visuales
    assert "fa-check" in fn_body
    assert "fa-arrows-rotate" in fn_body or "fa-arrows-spin" in fn_body or "fa-repeat" in fn_body
    assert "fa-triangle-exclamation" in fn_body or "fa-circle-exclamation" in fn_body


def test_index_html_has_matriz_container():
    with open("static/index.html", "r", encoding="utf-8") as f:
        index_html = f.read()

    assert "id=\"viewCentralMatriz\"" in index_html
    assert "id=\"contenedorMatrizOferta\"" in index_html
