"""Tests de contrato de UI y panel para la feature levantar-bloqueo.

Verifica:
- Botón #radarLevantarBloqueoBtn en static/index.html dentro de #radarBusquedasPausadas.
- Función levantarBloqueo() en static/radar.js (confirmación previa, llamada API, onStatus y mensaje).
"""
from pathlib import Path

from bs4 import BeautifulSoup

from tests.test_radar_ui import _node, requiere_node


def test_boton_en_index():
    soup = BeautifulSoup(Path("static/index.html").read_text(encoding="utf-8"), "html.parser")
    pausadas = soup.find(id="radarBusquedasPausadas")
    assert pausadas is not None, "Falta #radarBusquedasPausadas en static/index.html"
    btn = pausadas.find(id="radarLevantarBloqueoBtn")
    assert btn is not None, "Falta #radarLevantarBloqueoBtn dentro de #radarBusquedasPausadas"
    assert btn.name == "button"
    assert btn.get("type") == "button"
    assert btn.get_text(strip=True) == "Ya no hay bloqueo"


@requiere_node
def test_js_confirma_antes_de_llamar():
    out = _node("""
const llamadas = [];
const respuestas = [];
global.fetch = async (url, opts) => {
  const metodo = (opts && opts.method) || 'GET';
  llamadas.push({ metodo, url });
  const r = respuestas.shift() || { status: 200, body: {} };
  return { ok: r.status < 400, status: r.status, json: async () => r.body };
};
const elementos = {};
const falso = () => ({
  hidden: true, textContent: '', className: '', value: '', children: [], handlers: {},
  appendChild(c) { this.children.push(c); },
  addEventListener(ev, fn) { this.handlers[ev] = fn; }
});
global.document.getElementById = (id) => elementos[id] || (elementos[id] = falso());
global.document.createElement = () => falso();
global.document.createTextNode = (t) => ({ textContent: t });

const T = window.Radar._test;
if (!T || typeof T.levantarBloqueo !== 'function') {
  throw new Error("levantarBloqueo no está exportada en window.Radar._test");
}

(async () => {
  // 1. Con confirm falso, no llama a api
  window.confirm = () => false;
  await T.levantarBloqueo();
  const llamadasCancelado = llamadas.slice();

  // 2. Con confirm verdadero, llama api('POST', '/levantar-bloqueo'), pasa r.data a onStatus y muestra «Pausa levantada»
  llamadas.length = 0;
  window.confirm = () => true;
  respuestas.push({ status: 200, body: { busquedas_pausa: null, levantadas: 1 } });
  await T.levantarBloqueo();
  const llamadasAceptado = llamadas.slice();
  const estadoPausa = T.S.estado.busquedas_pausa;
  const textoMensaje = elementos.radarMensajeTexto && elementos.radarMensajeTexto.children[0]
    ? elementos.radarMensajeTexto.children[0].textContent
    : '';

  console.log(JSON.stringify({
    llamadasCancelado,
    llamadasAceptado,
    estadoPausa,
    textoMensaje,
  }));
  process.exit(0);
})();
""")
    assert out["llamadasCancelado"] == []
    assert len(out["llamadasAceptado"]) == 1
    assert out["llamadasAceptado"][0]["metodo"] == "POST"
    assert out["llamadasAceptado"][0]["url"] == "/api/radar/levantar-bloqueo"
    assert out["estadoPausa"] is None
    assert "Pausa levantada" in out["textoMensaje"]
