"""Rediseño E2, LinkedIn y Portales (docs/rediseno/plan.md §7 y §10).

E2a: barra de operación, franja de la cola, avisos y mensajes del Radar con clases propias y tokens.
Medidas: mismos ids y data-* que hoy (los tests del grupo A y el inventario siguen igual); sin Font Awesome ni colores
de Tailwind en lo migrado; el estado del Radar y los mensajes llevan su tono por clase o data-*, no por color suelto.
"""
import re
from pathlib import Path

from bs4 import BeautifulSoup

from tests.test_radar_ui import _node, requiere_node

RAIZ = Path(__file__).resolve().parents[1]
INDEX = RAIZ / "static" / "index.html"
RADAR_JS = RAIZ / "static" / "radar.js"
TAILWIND_COLOR = re.compile(r"\b(?:bg|text|border|hover:bg|hover:text)-(?:zinc|emerald|red|sky|amber|rose|white|black)\b|\[#[0-9a-fA-F]{3,6}\]")
MIGRADOS_E2A = ("#vista-radar .opbar", "#radarCola", "#radarCuotaAviso", "#radarGmailAviso", "#radarEnviosPausados",
                "#radarBusquedasPausadas", "#radarCuentas", "#radarMensaje")


def documento():
    return BeautifulSoup(INDEX.read_text(encoding="utf-8"), "html.parser")


def fuente_funcion(nombre: str) -> str:
    js = RADAR_JS.read_text(encoding="utf-8")
    ini = js.index(f"function {nombre}(")
    fin = js.find("\n  function ", ini + 1)
    return js[ini:fin if fin > 0 else None]


def test_barra_de_operacion_conserva_ids_y_usa_clases_propias():
    soup = documento()
    barra = soup.select_one("#vista-radar .opbar")
    assert barra is not None
    for id_ in ("manualNuevaBtn", "radarToggleBtn", "radarEstadoPill", "radarReintentarAhoraBtn",
                "radarMostrarNavegadorBtn", "radarCelularBtn", "radarCelularPanel", "radarRevisionBtn",
                "radarRevisionPanel", "radarUmbralInput", "radarAprobarTodosBtn"):
        assert barra.select_one(f"#{id_}") is not None, id_
    assert "btn" in soup.select_one("#radarToggleBtn")["class"]
    assert "chip" in soup.select_one("#radarEstadoPill")["class"]
    assert "input" in soup.select_one("#radarUmbralInput")["class"]


def test_lo_migrado_no_usa_font_awesome_ni_colores_de_tailwind():
    soup = documento()
    for sel in MIGRADOS_E2A:
        nodo = soup.select_one(sel)
        assert nodo is not None, sel
        for el in [nodo, *nodo.find_all(True)]:
            clases = " ".join(el.get("class", []))
            assert "fa-" not in clases, (sel, clases)
            assert not TAILWIND_COLOR.search(clases), (sel, clases)


def test_las_funciones_de_la_barra_no_arman_colores_de_tailwind():
    for nombre in ("renderControl", "renderPill", "renderRevisionBtn", "renderAprobarTodos", "mostrarMensaje"):
        assert not TAILWIND_COLOR.search(fuente_funcion(nombre)), nombre
        assert "fa-solid" not in fuente_funcion(nombre), nombre


def test_radar_css_solo_con_tokens_y_cargado_despues_del_marco():
    tokens = set(re.findall(r"--([a-z0-9-]+)\s*:", (RAIZ / "static/css/tokens.css").read_text(encoding="utf-8")))
    css = re.sub(r"/\*.*?\*/", "", (RAIZ / "static/css/radar.css").read_text(encoding="utf-8"), flags=re.S)
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css)
    assert set(re.findall(r"var\(--([a-z0-9-]+)", css)) <= tokens
    html = INDEX.read_text(encoding="utf-8")
    assert 0 < html.find('href="/static/css/marco.css') < html.find('href="/static/css/radar.css')


@requiere_node
def test_estado_del_radar_con_tono_de_chip():
    out = _node("""
console.log(JSON.stringify(['CORRIENDO', 'PAUSA', 'ESPERANDO_LOGIN', 'ERROR', 'DETENIDO']
  .map((e) => U.describirPill({estado: e}, false).clase).concat([U.describirPill({estado: 'CORRIENDO'}, true).clase])));
""")
    assert out == ["chip-ok", "chip-info", "chip-warn", "chip-danger", "", "chip-warn"]


# ---------------------------------------------------------------- E2b: filtros

ESTADOS = ["RELEVANTES", "PENDIENTES", "ANALIZADA", "LISTO", "GENERANDO", "ENVIADO", "DESCARTADO", "ERROR", "TODOS",
           "IGNORADA"]


def test_estados_en_una_fila_y_el_resto_en_el_panel_filtros():
    soup = documento()
    fila = soup.select_one("#radarFiltros")
    assert "seg" in fila["class"]
    assert [b["data-estado"] for b in fila.find_all("button")] == ESTADOS
    assert fila.select_one('[data-estado="RELEVANTES"]')["aria-pressed"] == "true"
    boton = soup.select_one("#radarFiltrosBtn")
    assert boton["aria-controls"] == "radarFiltrosPanel" and boton["aria-expanded"] == "false"
    panel = soup.select_one("#radarFiltrosPanel")
    assert panel.has_attr("hidden")
    for id_ in ("radarFiltroNoApto", "radarFiltroAlcance", "radarOrden", "radarFiltroCanal"):
        assert panel.select_one(f"#{id_}") is not None, id_
    # Portales filtra por portal a la vista: no queda escondido en el panel
    assert soup.select_one("#radarFiltroPortal").find_parent(id="radarFiltrosPanel") is None
    zona = soup.select_one("#vista-radar .filtros-zona")
    for el in zona.find_all(True):
        assert not TAILWIND_COLOR.search(" ".join(el.get("class", []))), el.get("id")
        assert "fa-" not in " ".join(el.get("class", []))


def test_render_de_filtros_por_aria_pressed_sin_colores_de_tailwind():
    fuente = fuente_funcion("renderChips") + fuente_funcion("marcar") + fuente_funcion("renderFiltrosActivos")
    assert not TAILWIND_COLOR.search(fuente) and "aria-pressed" in fuente


@requiere_node
def test_filtros_activos_se_listan_para_quitarlos():
    out = _node("""
const F = window.Radar._test.filtrosActivos;
console.log(JSON.stringify([
  F({alcance: 'TODAS', canal: 'TODOS', orden: 'match', filtro: 'RELEVANTES'}),
  F({alcance: 'ARGENTINA', canal: 'EMAIL', orden: 'recientes', filtro: 'NO_APTO'}),
]));
""")
    assert out[0] == []
    assert out[1] == [{"clave": "alcance", "texto": "Argentina"}, {"clave": "canal", "texto": "Con email"},
                      {"clave": "orden", "texto": "Más recientes"}, {"clave": "no_apto", "texto": "No apto"}]
