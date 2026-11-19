"""Filas de chips que se deslizan (.seg): cuando hay más contenido a un costado, ese borde se desvanece (mask-image),
así la fila se lee como deslizable y no como un texto cortado a mitad de palabra («Anal», «Descartada»).

Portal de prueba real (fixture de test_ui_navegador). Medidas: con contenido de más a la derecha, la fila lleva
data-mas-derecha y mask-image; al deslizarla hasta el final, el borde derecho deja de desvanecerse y el izquierdo
empieza; sin desborde, nada. Vale para los estados de LinkedIn y las etapas del editor, a 390, 768 y 1280 px.
"""
import pytest

from tests.test_ui_navegador import _abrir, _sembrar_radar, navegador, portal  # noqa: F401

pytestmark = [pytest.mark.ui, pytest.mark.timeout(240)]

MEDIR = """sel => { const e = document.querySelector(sel); return {
    derecha: e.hasAttribute('data-mas-derecha'), izquierda: e.hasAttribute('data-mas-izquierda'),
    desborda: e.scrollWidth > e.clientWidth + 1, mascara: getComputedStyle(e).webkitMaskImage || getComputedStyle(e).maskImage }; }"""


@pytest.mark.parametrize("ancho", [390, 768, 1280])
def test_estados_de_linkedin_se_desvanecen_donde_hay_mas(navegador, portal, ancho):  # noqa: F811
    base, _ = portal
    _sembrar_radar(2)
    contexto, page, errores = _abrir(navegador, base, ancho)
    try:
        page.click("#btnVistaRadar")
        page.wait_for_selector("#radarFiltros button")
        page.wait_for_timeout(300)
        antes = page.evaluate(MEDIR, "#radarFiltros")
        assert antes["desborda"], "la fila de estados no desborda a este ancho"
        assert antes["derecha"] and not antes["izquierda"] and "gradient" in antes["mascara"]
        page.evaluate("() => { const e = document.querySelector('#radarFiltros'); e.scrollLeft = e.scrollWidth; }")
        page.wait_for_timeout(200)
        despues = page.evaluate(MEDIR, "#radarFiltros")
        assert despues["izquierda"] and not despues["derecha"] and "gradient" in despues["mascara"]
        assert not errores.lista, errores.lista
    finally:
        contexto.close()


def test_sin_desborde_no_hay_desvanecido(navegador, portal):  # noqa: F811
    base, _ = portal
    contexto, page, errores = _abrir(navegador, base, 1280)
    try:
        page.click("#btnVistaRadar")
        page.wait_for_selector("#radarFiltros button")
        # La fila de canal («Todos · Con mail · Solo enlace») entra entera
        page.click("#radarFiltrosBtn")
        medida = page.evaluate(MEDIR, "#radarFiltroCanal")
        assert not medida["desborda"] and not medida["derecha"] and not medida["izquierda"]
        assert medida["mascara"] in ("none", "")
    finally:
        contexto.close()


def test_etapas_del_editor_tambien(navegador, portal):  # noqa: F811
    base, _ = portal
    contexto, page, errores = _abrir(navegador, base, 1280)
    try:
        page.evaluate("cambiarVista('editor')")
        page.wait_for_timeout(300)
        medida = page.evaluate(MEDIR, "#tabsLifecycleFiltros")
        assert medida["desborda"] and medida["derecha"] and "gradient" in medida["mascara"]
    finally:
        contexto.close()
