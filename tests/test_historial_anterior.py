"""«Historial anterior (N)» en Manual (creador, 19/11): arranca cerrado, se abre al tocarlo y, si el usuario lo
abrió, se recuerda solo durante la sesión (al volver a Manual o al recargar sigue abierto; en una sesión nueva,
cerrado). Antes se abría solo cuando no había ofertas manuales.

Portal de prueba real (fixture de test_ui_navegador). Medidas: `open` del <details> al entrar, al tocarlo, al ir y
volver de LinkedIn, al recargar y en un contexto nuevo del navegador.
"""
import pytest

from tests.test_ui_navegador import _abrir, navegador, portal  # noqa: F401

pytestmark = [pytest.mark.ui, pytest.mark.timeout(240)]

ABIERTO = "() => document.getElementById('manualHistorial').open"


def _a_manual(page):
    page.click("#btnVistaManual")
    page.locator("#manualHistorial summary").wait_for(state="visible")
    page.wait_for_timeout(500)


@pytest.mark.parametrize("ancho", [390, 1280])
def test_arranca_cerrado_aunque_no_haya_ofertas_manuales(navegador, portal, ancho):  # noqa: F811
    base, _ = portal
    contexto, page, errores = _abrir(navegador, base, ancho)
    try:
        _a_manual(page)
        assert page.evaluate(ABIERTO) is False
        assert page.locator("#manualHistorial summary").inner_text().startswith("Historial anterior (")
        assert not errores.lista, errores.lista
    finally:
        contexto.close()


def test_se_abre_al_tocarlo_y_se_recuerda_en_la_sesion(navegador, portal):  # noqa: F811
    base, _ = portal
    contexto, page, errores = _abrir(navegador, base, 1280)
    try:
        _a_manual(page)
        page.click("#manualHistorial summary")
        assert page.evaluate(ABIERTO) is True
        page.click("#btnVistaRadar")
        page.wait_for_timeout(300)
        _a_manual(page)
        assert page.evaluate(ABIERTO) is True, "al volver a Manual tiene que seguir abierto"
        page.reload()
        _a_manual(page)
        assert page.evaluate(ABIERTO) is True, "en la misma sesión, al recargar, sigue abierto"
        page.click("#manualHistorial summary")  # lo cierra: también se recuerda
        assert page.evaluate(ABIERTO) is False
        page.click("#btnVistaRadar")
        page.wait_for_timeout(300)
        _a_manual(page)
        assert page.evaluate(ABIERTO) is False
        page.click("#manualHistorial summary")
        assert page.evaluate(ABIERTO) is True
        assert not errores.lista, errores.lista
    finally:
        contexto.close()
    # Sesión nueva: arranca cerrado otra vez
    contexto, page, errores = _abrir(navegador, base, 1280)
    try:
        _a_manual(page)
        assert page.evaluate(ABIERTO) is False
    finally:
        contexto.close()


def test_con_el_teclado_tambien(navegador, portal):  # noqa: F811
    base, _ = portal
    contexto, page, errores = _abrir(navegador, base, 1280)
    try:
        _a_manual(page)
        page.focus("#manualHistorial summary")
        page.keyboard.press("Enter")
        assert page.evaluate(ABIERTO) is True
        page.click("#btnVistaRadar")
        page.wait_for_timeout(300)
        _a_manual(page)
        assert page.evaluate(ABIERTO) is True
    finally:
        contexto.close()
