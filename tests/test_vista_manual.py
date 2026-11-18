"""Tests para la vista «Manual» (Task 6)."""
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

INDEX = Path("static/index.html")
APP_JS = Path("static/app.js")
RADAR_JS = Path("static/radar.js")

requiere_node = pytest.mark.skipif(shutil.which("node") is None, reason="node no está instalado")


@pytest.fixture(scope="module")
def soup():
    return BeautifulSoup(INDEX.read_text(encoding="utf-8"), "html.parser")


def _run_node(script: str):
    if shutil.which("node") is None:
        pytest.skip("node no está instalado")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(script)
        nombre = f.name
    try:
        res = subprocess.run(["node", nombre], capture_output=True, text=True, encoding="utf-8")
        if res.returncode != 0:
            raise RuntimeError(f"Node failed (code {res.returncode}):\nSTDOUT: {res.stdout}\nSTDERR: {res.stderr}")
        return json.loads(res.stdout.strip())
    finally:
        os.unlink(nombre)


NODE_PRELUDE = r"""
global.window = global;
global.document = { addEventListener() {}, getElementById() { return null; }, querySelectorAll() { return []; } };
global.escapeHtml = (s) => s == null ? '' : String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
require(%s);
"""

NODE_DOM_FALSO = r"""
const nodos = {};
const falso = (id) => {
  const clases = new Set();
  const attrs = {};
  let _text = '';
  return {
    id,
    tag: 'div',
    hidden: false,
    className: '',
    value: '',
    disabled: false,
    checked: false,
    children: [],
    dataset: {},
    get textContent() {
      if (!this.children || this.children.length === 0) return _text;
      return _text + this.children.map(c => (c && c.textContent) || '').join(' ');
    },
    set textContent(v) { _text = String(v); this.children = []; },
    setAttribute(k, v) { attrs[k] = String(v); if (k.startsWith('data-')) { this.dataset[k.slice(5)] = String(v); } },
    getAttribute(k) { return attrs[k]; },
    appendChild(c) { if (c) { c.parent = this; this.children.push(c); } },
    insertBefore(c, ref) {
      if (!c) return;
      c.parent = this;
      const idx = this.children.indexOf(ref);
      if (idx >= 0) this.children.splice(idx, 0, c);
      else this.children.push(c);
    },
    remove() {
      if (this.parent && this.parent.children) {
        const idx = this.parent.children.indexOf(this);
        if (idx >= 0) this.parent.children.splice(idx, 1);
      }
    },
    querySelector(sel) {
      return this.querySelectorAll(sel)[0] || null;
    },
    querySelectorAll(sel) {
      const res = [];
      const attrMatch = /^\[([a-zA-Z0-9_-]+)(?:="([^"]*)")?\]$/.exec(sel);
      const walk = (n) => {
        for (const c of (n.children || [])) {
          if (!c) continue;
          if (attrMatch) {
            const val = c.getAttribute ? c.getAttribute(attrMatch[1]) : undefined;
            if (val !== undefined && (attrMatch[2] === undefined || val === attrMatch[2])) res.push(c);
          } else if ((c.tag && c.tag.toLowerCase() === sel.toLowerCase()) || c.id === sel) {
            res.push(c);
          }
          walk(c);
        }
      };
      walk(this);
      return res;
    },
    classList: {
      toggle(c, on) { if (on) clases.add(c); else clases.delete(c); },
      contains: (c) => clases.has(c)
    }
  };
};
global.document.getElementById = (id) => nodos[id] || (nodos[id] = falso(id));
global.document.createElement = (tag) => { const f = falso('nuevo'); f.tag = tag; return f; };
global.document.createDocumentFragment = () => falso('fragment');
global.document.createTextNode = (t) => ({ textContent: t });
const T = window.Radar._test;
"""


def _node(body: str):
    prelude = NODE_PRELUDE % json.dumps(str(RADAR_JS.resolve()))
    return _run_node(prelude + body)


# --- Tests BeautifulSoup (Step 1) ---

def test_bs4_header_botones_manual_y_radar(soup):
    header = soup.find("header")
    assert header is not None
    btn_manual = header.find(id="btnVistaManual")
    btn_radar = header.find(id="btnVistaRadar")
    btn_postulaciones = header.find(id="btnVistaPostulaciones")

    assert btn_manual is not None, "Falta #btnVistaManual en el header"
    assert "Manual" in btn_manual.get_text()
    assert btn_radar is not None, "Falta #btnVistaRadar en el header"
    assert btn_postulaciones is None, "#btnVistaPostulaciones no debe existir en el header"


def test_bs4_cambiar_vista_manual_en_html(soup):
    html = INDEX.read_text(encoding="utf-8")
    assert "cambiarVista('manual')" in html


def test_bs4_elementos_data_solo_manual_y_solo_radar(soup):
    vista = soup.find(id="vista-radar")
    assert vista is not None

    nueva_btn = vista.find(id="manualNuevaBtn")
    assert nueva_btn is not None, "Falta #manualNuevaBtn dentro de #vista-radar"
    assert nueva_btn.has_attr("data-solo-manual"), "#manualNuevaBtn debe tener data-solo-manual"
    assert "abrirModalNueva()" in (nueva_btn.get("onclick") or "")

    historial = vista.find(id="manualHistorial")
    assert historial is not None, "Falta #manualHistorial dentro de #vista-radar"
    # Compartido (11/11): en Manual, las manuales; en Radar, las postulaciones del Radar sin post
    assert not historial.has_attr("data-solo-manual") and not historial.has_attr("data-solo-radar")
    assert historial.find(id="manualHistorialLista") is not None, "Falta #manualHistorialLista"

    toggle = vista.find(id="radarToggleBtn")
    assert toggle is not None
    assert toggle.has_attr("data-solo-radar"), "#radarToggleBtn debe tener data-solo-radar"


def test_bs4_main_inicial_oculto(soup):
    main = soup.find("main")
    assert main is not None
    style = main.get("style", "")
    assert "display:none" in style.replace(" ", ""), "main debe arrancar con style='display:none'"


# --- Tests Node (Step 1) ---

@requiere_node
def test_modo_manual_filtra_por_fuente():
    out = _node(NODE_DOM_FALSO + """
    T.S.modo = 'manual';
    const esModoManual1 = T.perteneceAlModo({ fuente: 'manual' });
    const esModoManual2 = T.perteneceAlModo({ fuente: 'busqueda' });
    const esModoManual3 = T.perteneceAlModo({ fuente: 'feed' });

    T.S.modo = 'radar';
    const esModoRadar1 = T.perteneceAlModo({ fuente: 'manual' });
    const esModoRadar2 = T.perteneceAlModo({ fuente: 'busqueda' });
    const esModoRadar3 = T.perteneceAlModo({ fuente: 'feed' });

    // onPost ignorado en modo manual si fuente es busqueda
    T.S.modo = 'manual';
    T.S.posts.clear();
    T.onPost({ id: 991, fuente: 'busqueda', autor: 'Alguien', estado: 'NUEVO' });
    const tiene991 = T.S.posts.has(991);

    // onPost aceptado en modo manual si fuente es manual
    T.onPost({ id: 992, fuente: 'manual', autor: 'Alguien', estado: 'NUEVO' });
    const tiene992 = T.S.posts.has(992);

    console.log(JSON.stringify({
      manual: [esModoManual1, esModoManual2, esModoManual3],
      radar: [esModoRadar1, esModoRadar2, esModoRadar3],
      tiene991,
      tiene992
    }));
    """)
    assert out["manual"] == [True, False, False]
    assert out["radar"] == [False, True, True]
    assert out["tiene991"] is False
    assert out["tiene992"] is True


@requiere_node
def test_cargar_posts_pide_la_fuente_del_modo():
    out = _node(NODE_DOM_FALSO + """
    const urls = [];
    global.fetch = async (url) => {
      urls.push(String(url));
      return { ok: true, status: 200, json: async () => [] };
    };

    (async () => {
      T.S.modo = 'radar';
      await T.cargarPosts();
      T.S.modo = 'manual';
      await T.cargarPosts();
      console.log(JSON.stringify({ urls }));
    })();
    """)
    urls = out["urls"]
    assert len(urls) >= 2
    assert "fuente=manual" not in urls[0], f"Modo radar no debe pedir fuente=manual: {urls[0]}"
    assert "fuente=manual" in urls[1], f"Modo manual debe pedir fuente=manual: {urls[1]}"


@requiere_node
def test_aplicar_modo_oculta_controles_del_radar():
    out = _node(NODE_DOM_FALSO + """
    const radarElem = falso('radarCtrl');
    radarElem.setAttribute('data-solo-radar', '');
    radarElem.hidden = false;

    const manualElem = falso('manualCtrl');
    manualElem.setAttribute('data-solo-manual', '');
    manualElem.hidden = true;

    global.document.querySelectorAll = (sel) => {
      if (sel === '[data-solo-radar]') return [radarElem];
      if (sel === '[data-solo-manual]') return [manualElem];
      return [];
    };

    // Aplicar manual
    T.S.modo = 'manual';
    T.aplicarModo();
    const estadoManual = {
      radarHidden: radarElem.hidden,
      manualHidden: manualElem.hidden,
      titulo: (nodos.radarPanelBusquedaTitulo && nodos.radarPanelBusquedaTitulo.textContent) || '',
      datasetModo: (nodos['vista-radar'] && nodos['vista-radar'].dataset && nodos['vista-radar'].dataset.modo) || ''
    };

    // Aplicar radar
    T.S.modo = 'radar';
    T.aplicarModo();
    const estadoRadar = {
      radarHidden: radarElem.hidden,
      manualHidden: manualElem.hidden,
      titulo: (nodos.radarPanelBusquedaTitulo && nodos.radarPanelBusquedaTitulo.textContent) || '',
      datasetModo: (nodos['vista-radar'] && nodos['vista-radar'].dataset && nodos['vista-radar'].dataset.modo) || ''
    };

    console.log(JSON.stringify({ estadoManual, estadoRadar }));
    """)
    assert out["estadoManual"]["radarHidden"] is True
    assert out["estadoManual"]["manualHidden"] is False
    assert "Ofertas manuales" in out["estadoManual"]["titulo"]
    assert out["estadoManual"]["datasetModo"] == "manual"

    assert out["estadoRadar"]["radarHidden"] is False
    assert out["estadoRadar"]["manualHidden"] is True
    assert "Feed general" in out["estadoRadar"]["titulo"]
    assert out["estadoRadar"]["datasetModo"] == "radar"


@requiere_node
def test_fila_historial_solo_lectura():
    out = _node(NODE_DOM_FALSO + """
    const item = {
      id: 55,
      titulo: '<b>x</b>',
      empresa: 'Globant',
      status: 'READY',
      created_at: '2026-11-01T12:00:00Z'
    };
    const fila = T.filaHistorial(item);
    const links = fila.querySelectorAll('a').map(a => ({ href: a.href, text: a.textContent }));
    const botones = fila.querySelectorAll('button');
    const tagsB = fila.querySelectorAll('b');

    console.log(JSON.stringify({
      textoCompleto: fila.textContent,
      links,
      cantBotones: botones.length,
      cantTagsB: tagsB.length
    }));
    """)
    assert "<b>x</b>" in out["textoCompleto"]
    assert out["cantTagsB"] == 0, "No debe parsearse <b> como etiqueta HTML"
    assert out["cantBotones"] == 0, "No debe haber botones de editar en historial"
    hrefs = [l["href"] for l in out["links"]]
    assert any("/api/ofertas/55/export/pdf" in h for h in hrefs)
    assert any("/api/ofertas/55/export/docx" in h for h in hrefs)


# --- Tests app.js (Step 1) ---

def test_app_js_cambiar_vista_editor_y_ver_en_manual():
    js = APP_JS.read_text(encoding="utf-8")
    # cambiarVista('editor') dentro de verPostulacion
    assert "cambiarVista('editor')" in js
    # 'Ver en Manual' presente para btnVerEnRadar cuando radar_post_ref empieza con manual:
    assert "Ver en Manual" in js


# Ronda de corrección 1: respuestas fuera de orden y controles de Manual.
NODE_MODO_ASYNC = r"""
const T = window.Radar._test;
T.S.iniciado = true;
const timers = new Map();
let timerId = 0;
global.setTimeout = (fn) => { timers.set(++timerId, fn); return timerId; };
global.clearTimeout = (id) => timers.delete(id);
const respuesta = (data) => ({ ok: true, status: 200, json: async () => data });
const manual = { id: 2, fuente: 'manual', estado: 'NUEVO' };
global.fetch = async (url) => respuesta(String(url).includes('/posts?')
  ? (String(url).includes('fuente=manual') ? [manual] : [{ id: 3, fuente: 'busqueda', estado: 'NUEVO' }]) : null);
const drenar = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
"""


@requiere_node
@pytest.mark.parametrize("volver_a_radar", [False, True])
def test_cargar_posts_viejo_no_borra_la_lista_del_modo_actual(volver_a_radar):
    out = _node(NODE_MODO_ASYNC + """
    (async () => {
      let resolver;
      const fetchActual = global.fetch;
      global.fetch = () => new Promise(r => { resolver = r; });
      const vieja = T.cargarPosts();
      global.fetch = fetchActual;
      await window.Radar.mostrar('manual');
    """ + ("await window.Radar.mostrar('radar');" if volver_a_radar else "") + """
      const antes = [...T.S.posts.keys()];
      resolver(respuesta([{ id: 1, fuente: 'busqueda', estado: 'NUEVO' }]));
      await vieja;
      console.log(JSON.stringify({ antes, despues: [...T.S.posts.keys()] }));
    })();
    """)
    assert out["antes"] == ([3] if volver_a_radar else [2])
    assert out["despues"] == out["antes"]


@requiere_node
def test_refresco_sse_pendiente_no_mezcla_fuentes_al_cambiar_modo():
    out = _node(NODE_MODO_ASYNC + """
    (async () => {
      T.onPost({ id: 1, fuente: 'busqueda', estado: 'NUEVO' });
      const refresco = [...timers.values()].at(-1);
      let resolver;
      const fetchActual = global.fetch;
      global.fetch = () => new Promise(r => { resolver = r; });
      refresco();
      global.fetch = fetchActual;
      await window.Radar.mostrar('manual');
      resolver(respuesta({ id: 1, fuente: 'busqueda', estado: 'NUEVO' }));
      await drenar();
      console.log(JSON.stringify([...T.S.posts.keys()]));
    })();
    """)
    assert out == [2]


@requiere_node
def test_cambio_modo_cancela_temporizador_sse_anterior():
    out = _node(NODE_MODO_ASYNC + """
    (async () => {
      T.onPost({ id: 1, fuente: 'busqueda', estado: 'NUEVO' });
      const idRefresco = timerId;
      await window.Radar.mostrar('manual');
      console.log(JSON.stringify({ pendiente: timers.has(idRefresco) }));
    })();
    """)
    assert out["pendiente"] is False


@requiere_node
def test_refresco_sse_descarta_fuente_ajena_al_modo_actual():
    out = _node(NODE_MODO_ASYNC + """
    (async () => {
      await window.Radar.mostrar('manual');
      T.onPost(manual);
      global.fetch = async () => respuesta({ id: 2, fuente: 'busqueda', estado: 'NUEVO' });
      [...timers.values()].at(-1)();
      await drenar();
      console.log(JSON.stringify(T.S.posts.get(2).fuente));
    })();
    """)
    assert out == "manual"


@requiere_node
def test_manual_desde_mi_feed_movil_muestra_la_lista():
    out = _node(NODE_DOM_FALSO + """
    global.document.getElementById = (id) => ['radarPanelBusqueda', 'radarPanelFeed', 'radar-panel-lista'].includes(id)
      ? (nodos[id] || (nodos[id] = falso(id))) : null;
    global.fetch = async () => ({ ok: true, json: async () => null });
    window.innerWidth = 375;
    T.S.iniciado = true;
    T.S.tab = 'feed';
    document.getElementById('radarPanelBusqueda').classList.toggle('hidden', true);
    (async () => {
      await window.Radar.mostrar('manual');
      console.log(JSON.stringify({ tab: T.S.tab, oculta: nodos.radarPanelBusqueda.classList.contains('hidden') }));
    })();
    """)
    assert out == {"tab": "lista", "oculta": False}


@requiere_node
def test_pestana_lista_dice_ofertas_en_manual_y_feed_general_en_radar(soup):
    texto = soup.find(id="radarTabListaTexto")
    assert texto is not None and texto.find_parent(attrs={"data-radar-tab": "lista"})
    out = _node(NODE_DOM_FALSO + """
    const textos = [];
    for (const modo of ['manual', 'radar']) {
      T.S.modo = modo;
      T.aplicarModo();
      textos.push(document.getElementById('radarTabListaTexto').textContent);
    }
    console.log(JSON.stringify(textos));
    """)
    assert out == ["Ofertas", "Feed general"]


def test_revision_y_afinidad_son_exclusivos_del_radar(soup):
    for id_ in ("radarRevisionBtn", "radarRevisionPanel", "radarUmbralInput"):
        el = soup.find(id=id_)
        assert el.has_attr("data-solo-radar") or el.find_parent(attrs={"data-solo-radar": True})
    label = soup.find("label", attrs={"for": "radarUmbralInput"})
    assert label.has_attr("data-solo-radar") or label.find_parent(attrs={"data-solo-radar": True})
    aprobar = soup.find(id="radarAprobarTodosBtn")
    assert not aprobar.has_attr("data-solo-radar") and not aprobar.find_parent(attrs={"data-solo-radar": True})


def test_modal_nueva_oferta_sin_jerga(soup):
    modal = soup.find(id="modalNueva")
    assert modal.find("h3").get_text(strip=True) == "Nueva oferta"
    assert modal.find(attrs={"onclick": "enviarNuevaOferta()"}).get_text(strip=True) == "Cargar oferta"
    assert [modal.find(id=f"modalTab-{tab}").get_text(strip=True) for tab in ("manual", "url", "batch")] == ["Texto", "Link", "Varios links"]
    assert not any(p in modal.get_text().lower() for p in ("ingesta", "único", "masivo"))
