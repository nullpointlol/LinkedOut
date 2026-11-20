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
document.createElement = (tag) => { const el = crearFalso(tag); el.dataset = {}; return el; };
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
