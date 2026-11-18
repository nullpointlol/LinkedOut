"""Rediseño E3, Mi perfil (docs/rediseno/plan.md §6).

E3a: seis secciones por tarea con un índice; cada tarjeta conserva su borrador, versión y respaldo (sin cambios de
comportamiento). Medidas: las 6 secciones en orden, cada tarjeta en su sección, estilos solo con tokens y fuera del JS.
"""
import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
PERFIL_JS = RAIZ / "static" / "perfil.js"
PERFIL_CSS = RAIZ / "static" / "css" / "perfil.css"
SECCIONES = ["datos", "sabe", "busco", "cv", "aprendio", "automatizacion"]


def test_seis_secciones_en_el_orden_del_plan():
    js = PERFIL_JS.read_text(encoding="utf-8")
    bloque = js[js.index("const SECCIONES = ["):js.index("];", js.index("const SECCIONES = ["))]
    # Filas de sección: [id, título, ayuda, ícono, [tarjetas]]
    assert re.findall(r"^\s+\['([a-z]+)', '[^']+', '[^']*', '[a-z-]+',$", bloque, re.M) == SECCIONES


def test_cada_tarjeta_tiene_su_seccion():
    js = PERFIL_JS.read_text(encoding="utf-8")
    definidas = set(re.findall(r"\['([a-z_]+)', '[^']+'\]", js[:js.index("const JSON_FORMULARIO")]))
    bloque = js[js.index("const SECCIONES = ["):js.index("];", js.index("const SECCIONES = ["))]
    ubicadas = set(re.findall(r"'([a-z_]+)'", "".join(re.findall(r"\[([^\[\]]*)\]\]", bloque))))
    assert definidas and definidas <= ubicadas, definidas - ubicadas
    assert {"busquedas", "regenerar"} <= ubicadas


def test_estilos_de_mi_perfil_en_perfil_css_con_tokens():
    js = PERFIL_JS.read_text(encoding="utf-8")
    assert "nodo('style'" not in js and not re.search(r"#[0-9a-fA-F]{6}\b", js)
    tokens = set(re.findall(r"--([a-z0-9-]+)\s*:", (RAIZ / "static/css/tokens.css").read_text(encoding="utf-8")))
    css = re.sub(r"/\*.*?\*/", "", PERFIL_CSS.read_text(encoding="utf-8"), flags=re.S)
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b(?![\w-])(?!\s*[{,])", css.replace("#vista-perfil", ""))
    assert set(re.findall(r"var\(--([a-z0-9-]+)", css)) <= tokens
    html = (RAIZ / "static/index.html").read_text(encoding="utf-8")
    assert 0 < html.find('href="/static/css/radar.css') < html.find('href="/static/css/perfil.css')
