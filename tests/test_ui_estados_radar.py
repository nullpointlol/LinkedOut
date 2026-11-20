"""Estados visibles del Radar: «Por enviar», oferta elegida, avance tras descartar y filtro inicial.

DOM sintético en node (sin portal, sin proveedor ni datos reales).
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from test_radar_ui import NODE_DOM_FALSO, NODE_PRELUDE

RADAR_JS = Path("static/radar.js")

requiere_node = pytest.mark.skipif(shutil.which("node") is None, reason="node no está instalado")

# Íconos y botones con hijos: crearIcono devuelve un nodo que dice qué ícono es
NODE_ICONOS = r"""
window.crearIcono = (nombre, clase) => ({ textContent: '', nombre, clase: clase || '' });
const conHijos = (el) => { el.dataset = el.dataset || {}; el.replaceChildren = (...cs) => { el.textContent = ''; cs.forEach((c) => el.appendChild(c)); }; return el; };
const boton = (id) => conHijos(document.getElementById(id));
const crearFalso = document.createElement;
document.createElement = (tag) => {
  const el = crearFalso(tag); el.dataset = {}; el.handlers = {};
  el.addEventListener = (ev, fn) => { el.handlers[ev] = fn; };
  return el;
};
const iconos = (el) => el.children.filter((c) => c && c.nombre).map((c) => c.nombre + (c.clase ? '.' + c.clase : ''));
const LISTO = { id: 1, fuente: 'busqueda', estado: 'LISTO', email_to: 'a@b.test', emails: ['a@b.test'],
  email_subject: 's', email_body: 'b', updated_at: 'v1', postulacion_id: 3, aprobado_at: null };
"""


def node(body: str):
    script = NODE_PRELUDE % json.dumps(str(RADAR_JS.resolve())) + NODE_DOM_FALSO + NODE_ICONOS + body
    res = subprocess.run([shutil.which("node"), "-e", script], capture_output=True, text=True,
                         encoding="utf-8", timeout=20)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout.strip())


@requiere_node
def test_aprobado_sin_enviar_dice_por_enviar_en_tarjeta_y_detalle():
    out = node("""
const aprobado = { ...LISTO, aprobado_at: '2026-11-20 10:00:00' };
const card = T.crearCard(aprobado);
T.S.selId = 1; T.S.detalle = aprobado; T.renderDetalle();
console.log(JSON.stringify({
  etiqueta: U.etiquetaEstado(aprobado), card: card.innerHTML,
  detalle: document.getElementById('radarDetallePost').innerHTML,
  mail: U.textoEstadoEmail(aprobado), listo: U.etiquetaEstado(LISTO).texto,
  enviando: U.etiquetaEstado({ ...LISTO, estado: 'ENVIANDO' }).texto,
  enviada: U.etiquetaEstado({ ...LISTO, estado: 'ENVIADO' }).texto,
  cola: U.etiquetaEstado({ ...LISTO, estado: 'GENERANDO' }).texto,
}));
""")
    assert out["etiqueta"]["texto"] == "Por enviar"
    chip = re.compile(r'class="chip chip-info"><span class="ico icono-clock"[^>]*></span>Por enviar</span>')
    for html in (out["card"], out["detalle"]):
        assert chip.search(html), html
    # El chip suelto «en cola» de la línea del mail ya no está: «En cola» es solo la cola de CV
    assert ">en cola<" not in out["card"].lower()
    assert "cola" not in out["mail"].lower() and "Por enviar" in out["mail"]
    assert out["listo"] == "Lista para enviar"
    assert (out["enviando"], out["enviada"], out["cola"]) == ("Enviando", "Enviada", "En cola")


@requiere_node
def test_boton_enviar_queda_en_su_lugar_deshabilitado_mientras_espera():
    out = node("""
const btn = boton('radarAprobarBtn');
const estado = (detalle) => {
  T.S.selId = detalle.id; T.S.detalle = detalle; T.renderEmail(true);
  return { hidden: btn.hidden, disabled: btn.disabled, texto: btn.textContent.trim(), iconos: iconos(btn) };
};
console.log(JSON.stringify({
  listo: estado(LISTO),
  porEnviar: estado({ ...LISTO, aprobado_at: '2026-11-20 10:00:00' }),
  generando: estado({ ...LISTO, estado: 'GENERANDO', email_body: null }),
  deNuevo: estado(LISTO),
  otroCanal: estado({ ...LISTO, canal: 'link', aprobado_at: '2026-11-20 10:00:00' }),
}));
""")
    assert out["listo"] == {"hidden": False, "disabled": False, "texto": "Enviar", "iconos": ["send"]}
    assert out["porEnviar"] == {"hidden": False, "disabled": True, "texto": "Por enviar…",
                                "iconos": ["loader-circle.girando"]}
    assert out["generando"] == {"hidden": False, "disabled": True, "texto": "Generando CV…",
                                "iconos": ["loader-circle.girando"]}
    assert out["deNuevo"] == out["listo"]  # vuelve a habilitarse con su texto
    assert out["otroCanal"]["hidden"] is True  # un canal sin email no se envía por Gmail


@requiere_node
def test_el_clic_en_enviar_no_rehabilita_el_boton_que_quedo_esperando():
    out = node("""
(async () => {
  const btn = boton('radarAprobarBtn');
  T.S.selId = 1; T.S.detalle = { ...LISTO };
  // El clic aprueba: la acción redibuja el botón como «Por enviar…» antes de que `ejecutar` termine
  await T.ejecutar(btn, async () => { T.S.detalle = { ...LISTO, aprobado_at: 'x' }; T.renderEmail(true); });
  const trasAprobar = btn.disabled;
  T.S.detalle = { ...LISTO }; T.renderEmail(true);
  await T.ejecutar(btn, async () => {});
  console.log(JSON.stringify({ trasAprobar, listo: btn.disabled }));
})();
""")
    assert out == {"trasAprobar": True, "listo": False}


@requiere_node
def test_la_oferta_elegida_lleva_aria_current_y_la_clase_de_seleccion():
    out = node("""
const a = { ...LISTO, id: 1 }, b = { ...LISTO, id: 2 };
T.S.posts.set(1, a); T.S.posts.set(2, b);
T.S.selId = 1;
const ca = T.crearCard(a), cb = T.crearCard(b);
const lista = document.getElementById('radarLista');
lista.appendChild(ca); lista.appendChild(cb);
const antes = [ca.getAttribute('aria-current'), cb.getAttribute('aria-current'), ca.className, cb.className];
T.S.selId = 2; T.marcarSeleccion();
console.log(JSON.stringify({ antes, despues: [ca.getAttribute('aria-current'), cb.getAttribute('aria-current'), ca.className, cb.className] }));
""")
    assert out["antes"] == ["true", "false", "oferta elegida", "oferta"]
    assert out["despues"] == ["false", "true", "oferta", "oferta elegida"]


def test_el_estilo_de_la_elegida_usa_acento_fondo_y_barra_lateral():
    css = Path("static/css/radar.css").read_text(encoding="utf-8")
    regla = css[css.index('.oferta[aria-current="true"]'):]
    regla = regla[:regla.index("}")]
    assert "border-color: var(--accent)" in regla
    assert "background: var(--bg-selected)" in regla
    assert "box-shadow: inset" in regla and "var(--accent)" in regla.split("box-shadow")[1]
    assert "#" not in regla  # solo tokens


NODE_DESCARTE = r"""
const db = new Map();
const llamadas = [];
global.fetch = async (url, opts) => {
  const metodo = (opts && opts.method) || 'GET';
  llamadas.push(metodo + ' ' + url);
  const id = Number((/\/posts\/(\d+)/.exec(url) || [])[1]);
  if (url.endsWith('/discard')) db.set(id, { ...db.get(id), estado: 'DESCARTADO' });
  if (url.endsWith('/restaurar')) db.set(id, { ...db.get(id), estado: 'LISTO' });
  return { ok: true, status: 200, json: async () => ({ ...db.get(id) }) };
};
global.setTimeout = () => 0;
global.clearTimeout = () => {};
[[1, 90], [2, 80], [3, 70], [4, 60]].forEach(([id, match]) => {
  const p = { ...LISTO, id, match_score: match, aprobado_at: null };
  db.set(id, p); T.S.posts.set(id, { ...p });
});
db.set(4, { ...db.get(4), estado: 'ANALIZADA' });  // fuera del filtro: no es «la siguiente visible»
T.S.posts.set(4, { ...db.get(4) });
T.S.filtro = 'LISTO';
const descartarConfirmado = async () => { await T.descartar(); await T.descartar(); };
const espera = () => new Promise((r) => setImmediate(r));
const deshacer = () => document.getElementById('radarMensajeTexto').children[0].children[0];
"""


@requiere_node
def test_descartar_selecciona_la_siguiente_visible_y_si_era_la_ultima_la_anterior():
    out = node(NODE_DESCARTE + """
(async () => {
  T.S.selId = 2; T.S.detalle = { ...db.get(2) };
  await descartarConfirmado();
  await espera();
  const trasDescartarLaDelMedio = T.S.selId;
  await descartarConfirmado();  // la 3 es la última visible (1, 3): vuelve a la anterior
  await espera();
  const trasDescartarLaUltima = T.S.selId;
  const detalle = T.S.detalle && T.S.detalle.id;
  console.log(JSON.stringify({ trasDescartarLaDelMedio, trasDescartarLaUltima, detalle, llamadas }));
  process.exit(0);
})();
""")
    assert out["trasDescartarLaDelMedio"] == 3
    assert out["trasDescartarLaUltima"] == 1
    assert out["detalle"] == 1
    assert "POST /api/radar/posts/2/discard" in out["llamadas"] and "POST /api/radar/posts/3/discard" in out["llamadas"]


@requiere_node
def test_deshacer_tras_avanzar_restaura_y_vuelve_a_la_descartada():
    out = node(NODE_DESCARTE + """
(async () => {
  T.S.selId = 2; T.S.detalle = { ...db.get(2) };
  await descartarConfirmado();
  await espera();
  const antes = T.S.selId;
  const boton = deshacer();
  boton.handlers.click();
  for (let i = 0; i < 5; i += 1) await espera();
  console.log(JSON.stringify({ antes, texto: boton.textContent, despues: T.S.selId, estado: T.S.posts.get(2).estado,
    restaurar: llamadas.filter((l) => l.includes('restaurar')) }));
  process.exit(0);
})();
""")
    assert out["antes"] == 3 and out["texto"] == "Deshacer"
    assert out["restaurar"] == ["POST /api/radar/posts/2/restaurar"]
    assert out["estado"] == "LISTO" and out["despues"] == 2


@requiere_node
def test_descartar_la_unica_visible_deja_la_seleccion_vacia_sin_errores():
    out = node(NODE_DESCARTE + """
(async () => {
  T.S.filtro = 'ANALIZADA';
  T.S.selId = 4; T.S.detalle = { ...db.get(4) };
  await descartarConfirmado();
  await espera();
  console.log(JSON.stringify({ sel: T.S.selId }));
  process.exit(0);
})();
""")
    assert out["sel"] == 4  # no hay otra visible: queda la descartada a la vista
