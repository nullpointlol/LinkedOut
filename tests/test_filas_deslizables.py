"""Filas de chips que se deslizan (.seg): cuando hay más contenido a un costado, ese borde se desvanece (mask-image),
así la fila se lee como deslizable y no como un texto cortado a mitad de palabra.

Desde el 19/11 los estados de LinkedIn y las etapas del editor van en varias líneas (no se deslizan: ver
test_prueba_final.py); el desvanecido queda para toda fila .seg que sí desborde. Se prueba con una fila de prueba
dentro de la página real (portal de test_ui_navegador).
Medidas: con contenido de más a la derecha, data-mas-derecha y mask-image; al deslizar hasta el final se invierte;
sin desborde, nada; a 390, 768 y 1280 px.
"""
import pytest

from tests.test_ui_navegador import _abrir, navegador, portal  # noqa: F401

pytestmark = [pytest.mark.ui, pytest.mark.timeout(240)]

MEDIR = """sel => { const e = document.querySelector(sel); return {
    derecha: e.hasAttribute('data-mas-derecha'), izquierda: e.hasAttribute('data-mas-izquierda'),
    desborda: e.scrollWidth > e.clientWidth + 1, mascara: getComputedStyle(e).webkitMaskImage || getComputedStyle(e).maskImage }; }"""
FILA_DE_PRUEBA = """n => { const fila = document.createElement('div'); fila.id = 'filaDePrueba'; fila.className = 'seg';
    fila.style.maxWidth = '240px';
    for (let i = 0; i < n; i++) { const b = document.createElement('button'); b.type = 'button'; b.textContent = 'Opción ' + i; fila.append(b); }
    document.getElementById('vista-radar').prepend(fila); window.vigilarDesbordes(); }"""


@pytest.mark.parametrize("ancho", [390, 768, 1280])
def test_una_fila_que_desborda_se_desvanece_donde_hay_mas(navegador, portal, ancho):  # noqa: F811
    base, _ = portal
    contexto, page, errores = _abrir(navegador, base, ancho)
    try:
        page.click("#btnVistaRadar")
        page.wait_for_selector("#radarFiltros button", state="attached")
        page.evaluate(FILA_DE_PRUEBA, 12)
        page.wait_for_timeout(200)
        antes = page.evaluate(MEDIR, "#filaDePrueba")
        assert antes["desborda"] and antes["derecha"] and not antes["izquierda"] and "gradient" in antes["mascara"]
        page.evaluate("() => { const e = document.querySelector('#filaDePrueba'); e.scrollLeft = e.scrollWidth; }")
        page.wait_for_timeout(200)
        despues = page.evaluate(MEDIR, "#filaDePrueba")
        assert despues["izquierda"] and not despues["derecha"] and "gradient" in despues["mascara"]
        assert not errores.lista, errores.lista
    finally:
        contexto.close()


def test_sin_desborde_no_hay_desvanecido(navegador, portal):  # noqa: F811
    base, _ = portal
    contexto, page, errores = _abrir(navegador, base, 1280)
    try:
        page.click("#btnVistaRadar")
        page.wait_for_selector("#radarFiltros button", state="attached")
        page.evaluate(FILA_DE_PRUEBA, 2)
        page.wait_for_timeout(200)
        for selector in ("#filaDePrueba",):
            medida = page.evaluate(MEDIR, selector)
            assert not medida["desborda"] and not medida["derecha"] and not medida["izquierda"], selector
            assert medida["mascara"] in ("none", ""), selector
    finally:
        contexto.close()
