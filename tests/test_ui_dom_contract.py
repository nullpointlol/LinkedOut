from bs4 import BeautifulSoup
from pathlib import Path

def test_dom_central_panel_is_unified_without_hidden_tab_trap():
    html_path = Path("static/index.html")
    soup = BeautifulSoup(html_path.read_text(encoding="utf-8"), "html.parser")

    # El panel central no debe tener la matriz oculta bajo 'hidden' por defecto
    matriz_container = soup.find(id="contenedorMatrizOferta")
    assert matriz_container is not None
    parent_tab = matriz_container.find_parent(id="viewCentralMatriz")
    assert "hidden" not in parent_tab.get("class", []), "viewCentralMatriz no debe estar oculto con class 'hidden'"

def test_dom_static_scripts_have_cache_busting():
    html_path = Path("static/index.html")
    html = html_path.read_text(encoding="utf-8")
    assert 'src="/static/app.js?v=' in html, "static/app.js debe tener parámetro de versión para cache-busting"
