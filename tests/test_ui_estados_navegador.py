"""Estados visibles del Radar con un navegador real: la oferta elegida se distingue por su estilo calculado."""
import pytest

from core import radar_store
from tests.test_ui_navegador import _abrir, _sembrar_portales, _sembrar_radar, _vista, navegador, portal  # noqa: F401

pytestmark = [pytest.mark.ui, pytest.mark.timeout(240)]

ESTILO = """(ids) => ids.map((id) => {
  const el = document.querySelector(`[data-post-id="${id}"]`);
  const s = getComputedStyle(el);
  return { actual: el.getAttribute('aria-current'), fondo: s.backgroundColor, borde: s.borderTopColor, sombra: s.boxShadow };
})"""


def _listos(ids):
    for pid in ids:
        radar_store.update_post(pid, estado="LISTO", email_to="rrhh@acme.test")
    return ids


@pytest.mark.parametrize("vista,ancho", [("radar", 375), ("radar", 1200), ("portales", 1200)])
def test_la_oferta_elegida_se_distingue_por_su_estilo(navegador, portal, vista, ancho):
    base, _ = portal
    ids = _listos(_sembrar_radar(2) if vista == "radar" else _sembrar_portales())
    contexto, page, errores = _abrir(navegador, base, ancho)
    try:
        _vista(page, vista)
        page.click(f'[data-post-id="{ids[0]}"]')
        page.mouse.move(0, 0)  # sin hover sobre ninguna tarjeta
        page.wait_for_function(f"document.querySelector('[data-post-id=\"{ids[0]}\"]').getAttribute('aria-current') === 'true'")
        elegida, otra = page.evaluate(ESTILO, ids)
        assert (elegida["actual"], otra["actual"]) == ("true", "false")
        for prop in ("fondo", "borde", "sombra"):
            assert elegida[prop] != otra[prop], (prop, elegida, otra)
        assert "inset" in elegida["sombra"]
        assert not errores.lista, errores.lista
    finally:
        contexto.close()
