"""Scoring del detalle de un post del Radar con navegador real, a 375 y 1200 px.

Un post con «Brecha fuerte» (requisito excluyente duro que falta), «Condición» (requisito logístico) y
«Me descarta» (el control para mandarlo a Ignoradas) se ve completo: cada marca visible, dentro del ancho
de la pantalla, sin texto cortado, sin superponerse y sin scroll horizontal. Deja una captura en tmp_path.
"""
import json

import pytest

from core import radar_store
from tests.test_ui_navegador import ANCHOS, ANALISIS_FALSO, TEXTO_OFERTA, _abrir, _vista, navegador, portal  # noqa: F401

pytestmark = [pytest.mark.ui, pytest.mark.timeout(240)]

REQUISITOS = [
    {"nombre": "SQL", "tipo": "REQUISITO_EXCLUYENTE", "estado": "MATCH_DIRECTO", "approved": True,
     "justificacion": "SQL en la base"},
    {"nombre": "Certificación PMP vigente emitida por el PMI", "tipo": "REQUISITO_EXCLUYENTE",
     "estado": "BRECHA_REAL", "approved": False, "dura": "cert", "justificacion": "No figura en la base"},
    {"nombre": "Horario de lunes a viernes de 9 a 18", "tipo": "REQUISITO_EXCLUYENTE", "estado": "MATCH_DIRECTO",
     "approved": True, "logistico": True, "justificacion": "Condición de la oferta"},
]


def _sembrar() -> int:
    pid = radar_store.insert_post_if_new({
        "urn": "urn:li:activity:9100", "url": "https://www.linkedin.com/feed/update/urn:li:activity:9100/",
        "autor": "Selección Acme", "cargo_autor": "Talent Acquisition", "fecha_rel": "2 h",
        "texto": TEXTO_OFERTA, "emails": ["rrhh@acme.test"],
    }, "consulta de prueba")
    analisis = json.loads(json.dumps(ANALISIS_FALSO))
    analisis["requisitos"] = json.loads(json.dumps(REQUISITOS))
    radar_store.update_post(pid, estado="ANALIZADA", analisis=analisis,
                            triage={"oferta": 1, "puesto": "Analista Funcional", "canal": "email"})
    return pid


MEDIR = """() => {
  const caja = (el) => { const r = el.getBoundingClientRect(); return {x: r.left, y: r.top, w: r.width, h: r.height}; };
  const det = document.getElementById('radarDetallePost');
  const uno = (sel) => { const el = det.querySelector(sel); return el ? {
      ...caja(el), texto: el.textContent.trim(), visible: !!(el.offsetWidth || el.offsetHeight),
      cortado: el.scrollWidth > el.clientWidth + 1} : null; };
  // Nombre del requisito con «Brecha fuerte»: ninguna palabra se parte en dos renglones
  const marca = det.querySelector('[data-brecha-fuerte]');
  const nombre = marca ? [...marca.parentElement.children].find((e) => e.textContent.includes('Certificación')) : null;
  let partida = false;
  if (nombre) {
    const nodo = nombre.firstChild, rango = document.createRange();
    let pos = 0;
    for (const palabra of nodo.textContent.split(' ')) {
      rango.setStart(nodo, pos); rango.setEnd(nodo, pos + palabra.length);
      if (rango.getClientRects().length > 1) partida = true;
      pos += palabra.length + 1;
    }
  }
  const condicion = [...det.querySelectorAll('span')].find((s) => s.textContent.trim() === 'Condición');
  return {
    ancho: document.documentElement.clientWidth,
    desborde: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    brecha: uno('[data-brecha-fuerte]'),
    descarta: uno('[data-descarte-req]'),
    palabraPartida: partida,
    condicion: condicion ? {...caja(condicion), texto: condicion.textContent.trim(),
                            visible: !!condicion.offsetWidth, cortado: condicion.scrollWidth > condicion.clientWidth + 1} : null,
  };
}"""


def _superpuestos(a, b) -> bool:
    return not (a["x"] + a["w"] <= b["x"] or b["x"] + b["w"] <= a["x"]
                or a["y"] + a["h"] <= b["y"] or b["y"] + b["h"] <= a["y"])


@pytest.mark.parametrize("ancho", ANCHOS)
def test_detalle_con_brecha_fuerte_condicion_y_me_descarta_se_ve_entero(navegador, portal, ancho, tmp_path):
    base, _ = portal
    pid = _sembrar()
    contexto, page, errores = _abrir(navegador, base, ancho)
    try:
        _vista(page, "radar")
        page.click(f'[data-post-id="{pid}"]')
        # Sin plazo propio: el de _abrir (15 s), holgado para la suite en paralelo
        # renderDetalle reescribe el detalle cuando llega el texto completo: si el nodo se reemplaza durante el
        # scroll, el localizador se resuelve de nuevo (antes fallaba a veces con «not attached to the DOM»)
        brecha = page.locator("#radarDetallePost [data-brecha-fuerte]").first
        for intento in range(5):
            try:
                brecha.wait_for()
                brecha.scroll_into_view_if_needed()
                break
            except Exception:
                if intento == 4:
                    raise
                page.wait_for_timeout(200)
        m = page.evaluate(MEDIR)
        assert m["desborde"] <= 0, f"scroll horizontal de {m['desborde']} px a {ancho} px"
        for clave, texto in (("brecha", "Brecha fuerte"), ("condicion", "Condición"), ("descarta", "Me descarta")):
            el = m[clave]
            assert el is not None, f"falta «{texto}» a {ancho} px"
            assert el["visible"] and el["texto"] == texto, el
            assert el["x"] >= 0 and el["x"] + el["w"] <= m["ancho"] + 0.5, f"«{texto}» se sale de la pantalla: {el}"
            assert not el["cortado"], f"«{texto}» quedó cortado a {ancho} px"
        assert not _superpuestos(m["brecha"], m["descarta"]), (m["brecha"], m["descarta"])
        assert not m["palabraPartida"], f"el nombre del requisito parte palabras a {ancho} px"
        page.locator("#radarDetallePost").screenshot(path=str(tmp_path / f"scoring-{ancho}.png"))
        assert (tmp_path / f"scoring-{ancho}.png").stat().st_size > 0
        assert not errores.lista, errores.lista
    finally:
        contexto.close()
