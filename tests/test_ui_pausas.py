"""Tests de UI para banner de envíos honesto y botón de pausa de análisis."""
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

INDEX = Path("static/index.html")
RADAR_JS = Path("static/radar.js")

requiere_node = pytest.mark.skipif(shutil.which("node") is None, reason="node no está instalado")


def _run_node(script: str):
    if shutil.which("node") is None:
        pytest.skip("node no está instalado")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(script)
        nombre = f.name
    try:
        res = subprocess.run(["node", nombre], capture_output=True, text=True, encoding="utf-8", check=True)
        return json.loads(res.stdout.strip())
    finally:
        os.unlink(nombre)


NODE_PRELUDE = r"""
global.window = global;
global.document = { addEventListener() {}, getElementById() { return null; } };
global.escapeHtml = (s) => s == null ? '' : String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
require(%s);
const U = window.Radar._util;
"""


def _node(body: str):
    prelude = NODE_PRELUDE % json.dumps(str(RADAR_JS.resolve()))
    return _run_node(prelude + body)


NODE_DOM_FALSO = r"""
const nodos = {};
const falso = (id) => {
  const clases = new Set();
  const attrs = {};
  let _text = '';
  return { id, hidden: true, className: '', value: '', disabled: false, checked: false, children: [], oyentes: {},
    get textContent() {
      if (!this.children || this.children.length === 0) return _text;
      return _text + this.children.map(c => (c && c.textContent) || '').join(' ');
    },
    set textContent(v) { _text = String(v); this.children = []; },
    setAttribute(k, v) { attrs[k] = String(v); },
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
    addEventListener(ev, fn) { this.oyentes[ev] = fn; },
    click() { if (this.oyentes.click) this.oyentes.click({ currentTarget: this, target: this, preventDefault() {} }); },
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
    classList: { toggle(c, on) { if (on) clases.add(c); else clases.delete(c); }, contains: (c) => clases.has(c) } };
};
global.document.getElementById = (id) => nodos[id] || (nodos[id] = falso(id));
global.document.createElement = (tag) => { const f = falso('nuevo'); f.tag = tag; return f; };
global.document.createDocumentFragment = () => falso('fragment');
global.document.createTextNode = (t) => ({ textContent: t });
global.document.querySelectorAll = () => [];
const T = window.Radar._test;
"""


def test_banner_envios_pausados_sin_afirmacion_fija():
    soup = BeautifulSoup(INDEX.read_text(encoding="utf-8"), "html.parser")
    banner = soup.find(id="radarEnviosPausados")
    assert banner is not None, "Debe existir #radarEnviosPausados"
    texto = banner.get_text(" ", strip=True)
    assert "Envíos en pausa. Si Gmail rechazó las credenciales, corregí GMAIL_ADDRESS / GMAIL_APP_PASSWORD en .env y tocá «Reintentar envíos»." in texto
    assert "Envíos pausados: Gmail rechazó las credenciales" not in texto


def test_boton_radar_analisis_presente():
    soup = BeautifulSoup(INDEX.read_text(encoding="utf-8"), "html.parser")
    btn = soup.find(id="radarAnalisisBtn")
    assert btn is not None, "Debe existir el botón #radarAnalisisBtn"
    assert btn.name == "button"
    assert btn.get("type") == "button"

    # En el historial de procesos
    panel = soup.find(id="radarProcesosPanel")
    assert panel is not None
    assert btn in panel.find_all(btn.name), "#radarAnalisisBtn debe estar dentro de #radarProcesosPanel"

    # Junto al botón #radarGeneracionBtn
    btn_gen = soup.find(id="radarGeneracionBtn")
    assert btn_gen is not None
    # 08/11: cada control en su fila del panel, con su estado escrito
    assert btn.find_parent(id="radarProcesosPanel") is not None and btn_gen.find_parent(id="radarProcesosPanel") is not None

    # Mismo estilo que el de generación
    assert set(btn.get("class", [])) == set(btn_gen.get("class", [])), "Mismo estilo que #radarGeneracionBtn"

    # Contrato visual
    for c in btn.get("class", []):
        assert c not in ("text-zinc-500", "text-zinc-600"), f"Clase prohibida {c}"
        assert c not in ("text-[10px]", "text-[11px]"), f"Tamaño de texto menor a 12px {c}"

    assert btn.get_text(strip=True) == "Pausar"


@requiere_node
def test_radar_analisis_texto_segun_estado():
    out = _node(NODE_DOM_FALSO + """
T.S.estado = { ...T.S.estado, analisis_pausado: false, generacion_pausada: false, cola: null };
T.renderCola();
const sinPausaBtn = nodos.radarAnalisisBtn.textContent;
const sinPausaFranja = nodos.radarCola.textContent;

T.S.estado.analisis_pausado = true;
T.renderCola();
const conPausaBtn = nodos.radarAnalisisBtn.textContent;
const conPausaFranja = nodos.radarCola.textContent;

T.S.estado.generacion_pausada = true;
T.renderCola();
const ambasPausaFranja = nodos.radarCola.textContent;

T.S.estado.analisis_pausado = false;
T.renderCola();
const reanudadoBtn = nodos.radarAnalisisBtn.textContent;
const reanudadoFranja = nodos.radarCola.textContent;

console.log(JSON.stringify({
    sinPausaBtn, sinPausaFranja,
    conPausaBtn, conPausaFranja,
    ambasPausaFranja,
    reanudadoBtn, reanudadoFranja
}));
""")
    assert out["sinPausaBtn"] == "Pausar"
    assert " · análisis en pausa" not in out["sinPausaFranja"]
    assert out["conPausaBtn"] == "Reanudar"
    assert out["conPausaFranja"].endswith(" · análisis en pausa")
    assert out["ambasPausaFranja"] == "Generación de CV en pausa · análisis en pausa"
    assert out["reanudadoBtn"] == "Pausar"
    assert " · análisis en pausa" not in out["reanudadoFranja"]


@requiere_node
def test_click_analisis_llama_api_con_valor_invertido():
    out = _node(NODE_DOM_FALSO + """
const llamadas = [];
global.fetch = async (url, opts) => {
    llamadas.push({
        url,
        method: opts && opts.method,
        body: opts && opts.body ? JSON.parse(opts.body) : null
    });
    return {
        ok: true,
        status: 200,
        json: async () => ({
            analisis_pausado: true,
            generacion_pausada: false,
            cola: null
        })
    };
};

(async () => {
    T.S.estado.analisis_pausado = false;
    if (typeof T.alternarAnalisis === 'function') {
        await T.alternarAnalisis();
    } else if (nodos.radarAnalisisBtn && nodos.radarAnalisisBtn.oyentes && nodos.radarAnalisisBtn.oyentes.click) {
        await nodos.radarAnalisisBtn.oyentes.click();
    }

    T.S.estado.analisis_pausado = true;
    if (typeof T.alternarAnalisis === 'function') {
        await T.alternarAnalisis();
    } else if (nodos.radarAnalisisBtn && nodos.radarAnalisisBtn.oyentes && nodos.radarAnalisisBtn.oyentes.click) {
        await nodos.radarAnalisisBtn.oyentes.click();
    }

    console.log(JSON.stringify({ llamadas }));
})();
""")
    llamadas = [l for l in out["llamadas"] if "/analisis" in l["url"]]
    assert len(llamadas) == 2, f"Se esperaban 2 llamadas a /analisis, se obtuvieron: {out['llamadas']}"
    assert llamadas[0]["method"] == "POST"
    assert llamadas[0]["url"].endswith("/api/radar/analisis")
    assert llamadas[0]["body"] == {"pausada": True}

    assert llamadas[1]["method"] == "POST"
    assert llamadas[1]["url"].endswith("/api/radar/analisis")
    assert llamadas[1]["body"] == {"pausada": False}
