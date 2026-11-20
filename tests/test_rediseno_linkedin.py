"""Rediseño E2, LinkedIn y Portales.

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


def test_estados_en_un_selector_y_el_resto_en_el_panel_filtros():
    soup = documento()
    # Los estados en un selector que abre la lista, y «Filtros» al lado, en una línea
    fila = soup.select_one("#radarFiltros")
    assert fila.has_attr("hidden")
    selector = soup.select_one("#radarEstadoBtn")
    assert selector["aria-controls"] == "radarFiltros" and selector["aria-expanded"] == "false"
    assert selector.find_parent(class_="filtros-fila") is soup.select_one("#radarFiltrosBtn").find_parent(class_="filtros-fila")
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
    assert out[1] == [{"clave": "alcance", "texto": "Argentina"}, {"clave": "canal", "texto": "Con mail"},
                      {"clave": "orden", "texto": "Más recientes"}, {"clave": "no_apto", "texto": "También las no aptas"}]  # §8


# ---------------------------------------------------------------- E2b: tarjeta

def test_tarjeta_y_sus_piezas_sin_colores_de_tailwind_ni_font_awesome():
    for nombre in ("claseCard", "badgeEstado", "subEstado", "badgeMatch", "badgeAlcance", "htmlAfinidad", "selloNoApto",
                   "logoPortal", "botonPostularSitio", "htmlProgreso", "crearCard"):
        fuente = fuente_funcion(nombre)
        assert not TAILWIND_COLOR.search(fuente), nombre
        assert "fa-solid" not in fuente and "fa-regular" not in fuente, nombre


@requiere_node
def test_tarjeta_con_titulo_estado_y_acciones_propias():
    out = _node("""
const T = window.Radar._test;
global.document = { createElement: () => ({ dataset: {}, style: {}, setAttribute() {}, appendChild() {}, querySelector() { return null; } }) };
const html = T.crearCard({id: 3, estado: 'ANALIZADA', puesto: 'Desarrolladora Frontend', empresa: 'Nexo Data',
  autor: 'Laura', match_score: 86, canal: 'EMAIL', email_to: 'talento@nexodata.example', postulacion_id: null}).innerHTML;
console.log(JSON.stringify(html));
""")
    soup = BeautifulSoup(out, "html.parser")
    assert soup.select_one(".oferta-titulo.titulo-linea").get_text() == "Desarrolladora Frontend"
    assert soup.select_one(".oferta-estados .chip.chip-ok") is not None
    assert "chip" in soup.select_one("[data-score] .num")["class"]
    for boton in soup.select("[data-accion]"):
        assert "btn" in boton["class"] and boton.select_one(".ico[class*=icono-]")  # ícono por máscara CSS



# ---------------------------------------------------------------- E2c: detalle

def test_detalle_y_sus_piezas_sin_colores_de_tailwind_ni_font_awesome():
    for nombre in ("renderDetalle", "htmlTextoPost", "bloque", "htmlAnalisis", "renderRequisitos", "htmlTiempos",
                   "extrasDePortal"):
        fuente = fuente_funcion(nombre)
        assert not TAILWIND_COLOR.search(fuente), nombre
        assert "fa-solid" not in fuente and "fa-regular" not in fuente and "fa-brands" not in fuente, nombre


def test_texto_de_la_oferta_literal_en_pre_wrap():
    """Invariante del plan: el texto de la oferta va literal (escapado, sin transformar) y respeta los saltos."""
    fuente = fuente_funcion("htmlTextoPost")
    assert "escapeHtml(t)" in fuente and 'class="texto-oferta"' in fuente
    css = (RAIZ / "static/css/radar.css").read_text(encoding="utf-8")
    regla = css[css.index(".texto-oferta {"):]
    assert "white-space: pre-wrap" in regla[:regla.index("}")]


# ---------------------------------------------------------------- E2: layout de 3 paneles

def test_tres_paneles_con_grilla_propia():
    soup = documento()
    columnas = soup.select_one("#radarColumnas")
    assert "columnas-radar" in columnas["class"] and not any(c.startswith("lg:") for c in columnas["class"])
    for id_ in ("radar-panel-lista", "radar-panel-detalle", "radar-panel-email"):
        clases = soup.select_one(f"#{id_}")["class"]
        assert "panel-radar" in clases and not any("col-span" in c for c in clases), id_
    css = (RAIZ / "static/css/radar.css").read_text(encoding="utf-8")
    assert "grid-template-columns: 320px minmax(0, 1fr) 360px" in css
    assert "grid-template-columns: 360px minmax(0, 1fr) 400px" in css


def test_busquedas_y_mi_feed_en_pestanas_dentro_de_la_lista():
    soup = documento()
    lista = soup.select_one("#radar-panel-lista")
    pestanas = lista.select("#radarTabsLista [data-lista-tab]")
    assert [b["data-lista-tab"] for b in pestanas] == ["lista", "feed"]
    assert pestanas[1].has_attr("data-solo-linkedin")
    assert "listas" in soup.select_one("#radarPanelBusqueda").parent["class"]


def test_set_tab_recuerda_la_lista_abierta():
    fuente = fuente_funcion("setTab")
    assert "S.listaTab" in fuente and "aria-selected" in fuente
    assert not TAILWIND_COLOR.search(fuente)


# ---------------------------------------------------------------- E2 completa

def test_vista_linkedin_sin_colores_de_tailwind_ni_font_awesome():
    """Cierre de E2: todo lo de LinkedIn y Portales (marcado y radar.js) usa clases propias y el sprite."""
    js = RADAR_JS.read_text(encoding="utf-8")
    assert not TAILWIND_COLOR.search(js), TAILWIND_COLOR.search(js).group(0)
    assert not re.search(r"fa-(?:solid|regular|brands)", js)
    vista = documento().select_one("#vista-radar")
    for el in vista.find_all(True):
        clases = " ".join(el.get("class", []))
        assert not TAILWIND_COLOR.search(clases), (el.get("id"), clases)
        assert "fa-" not in clases, (el.get("id"), clases)


def test_cada_icono_de_radar_js_existe_en_iconos_css():
    """svgIcono('x') arma <span class="ico icono-x">: cada nombre tiene su clase en static/css/iconos.css."""
    js = RADAR_JS.read_text(encoding="utf-8")
    usados = set(re.findall(r"svgIcono\('([a-z0-9-]+)'", js))
    css = (RAIZ / "static/css/iconos.css").read_text(encoding="utf-8")
    definidos = set(re.findall(r"^\.icono-([a-z0-9-]+) \{", css, re.M))
    assert usados and usados <= definidos, usados - definidos
    html = (RAIZ / "static/index.html").read_text(encoding="utf-8")
    assert 'href="/static/css/iconos.css' in html


def test_tarjetas_fuera_de_pantalla_no_se_dibujan_hasta_acercarse():
    """Rendimiento (test_ui_navegador: vista Radar con la base grande ≤ 4000 ms): content-visibility en .oferta."""
    css = (RAIZ / "static/css/radar.css").read_text(encoding="utf-8")
    assert re.search(r"\.oferta \{ content-visibility: auto; contain-intrinsic-size: auto \d+px; \}", css)
