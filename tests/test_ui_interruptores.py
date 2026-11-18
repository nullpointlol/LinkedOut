"""Interruptores Feed y Búsquedas del panel de procesos, en un navegador real a 375 y 1200 px.

Con el Radar detenido se ven apagados, grisados y deshabilitados, con un tooltip que dice por qué; en marcha
muestran el estado guardado y al tocarlos cambian la base. El Radar es un doble: nunca abre LinkedIn."""
import pytest

from core import radar_store
from tests.test_ui_navegador import _abrir, _vista, navegador, portal  # noqa: F401

pytestmark = [pytest.mark.ui, pytest.mark.timeout(240)]

SWITCHES = ("#radarFeedSwitch", "#radarBusquedasSwitch")


def _abrir_panel(page):
    _vista(page, "radar")
    page.click("#radarCola")
    page.wait_for_selector("#radarProcesosPanel:not([hidden])")


def _estado(page, sel):
    return page.eval_on_selector(sel, "b => ({checked: b.getAttribute('aria-checked'), role: b.getAttribute('role'),"
                                      " disabled: b.disabled, title: b.title, opacity: getComputedStyle(b).opacity,"
                                      " visible: b.offsetWidth > 0 && b.offsetHeight > 0})")


@pytest.mark.parametrize("ancho", (375, 1200))
def test_con_el_radar_detenido_se_ven_apagados_y_grisados(navegador, portal, ancho):  # noqa: F811
    base, _ = portal
    # Aunque la base los tenga encendidos, con el Radar detenido no se muestran encendidos
    radar_store.update_radar_state(desired_running=False, estado="DETENIDO", feed_activo=True, busquedas_activas=True)
    contexto, page, errores = _abrir(navegador, base, ancho)
    try:
        _abrir_panel(page)
        for sel in SWITCHES:
            e = _estado(page, sel)
            assert e["visible"] and e["role"] == "switch"
            assert e["checked"] == "false" and e["disabled"] is True
            assert "detenido" in e["title"].lower()
            assert float(e["opacity"]) < 1
        assert "Encendé el Radar" in page.text_content("#radarInterruptoresAyuda")
        desborde = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert desborde <= 0
        assert not errores.lista, errores.lista
    finally:
        contexto.close()


@pytest.mark.parametrize("ancho", (375, 1200))
def test_con_el_radar_en_marcha_muestran_el_estado_y_cambian_la_base(navegador, portal, ancho):  # noqa: F811
    base, _ = portal
    radar_store.update_radar_state(desired_running=True, estado="CORRIENDO", feed_activo=True, busquedas_activas=False)
    contexto, page, errores = _abrir(navegador, base, ancho)
    try:
        _abrir_panel(page)
        feed, busq = (_estado(page, s) for s in SWITCHES)
        assert feed["checked"] == "true" and feed["disabled"] is False
        assert busq["checked"] == "false" and busq["disabled"] is False
        assert page.text_content("#radarInterruptoresEstado") == "solo feed"

        # Encender las búsquedas pide confirmación (el doble de diálogos la acepta) y llega a la base
        page.focus("#radarBusquedasSwitch")
        page.keyboard.press("Enter")
        page.wait_for_selector('#radarBusquedasSwitch[aria-checked="true"]:not([disabled])')
        assert radar_store.get_radar_state()["busquedas_activas"] is True

        page.click("#radarFeedSwitch")
        page.wait_for_selector('#radarFeedSwitch[aria-checked="false"]:not([disabled])')
        assert radar_store.get_radar_state()["feed_activo"] is False
        assert page.text_content("#radarInterruptoresEstado") == "solo búsquedas"

        page.click("#radarBusquedasSwitch")
        page.wait_for_selector('#radarBusquedasSwitch[aria-checked="false"]:not([disabled])')
        assert radar_store.get_radar_state()["busquedas_activas"] is False
        assert not errores.lista, errores.lista
    finally:
        contexto.close()


def test_rechazar_la_confirmacion_no_enciende_las_busquedas(navegador, portal):  # noqa: F811
    base, _ = portal
    radar_store.update_radar_state(desired_running=True, estado="CORRIENDO", busquedas_activas=False)
    contexto, page, errores = _abrir(navegador, base, 1200)
    try:
        _abrir_panel(page)
        page.evaluate("window.confirm = () => false")
        page.click("#radarBusquedasSwitch")
        page.wait_for_timeout(500)
        assert page.get_attribute("#radarBusquedasSwitch", "aria-checked") == "false"
        assert radar_store.get_radar_state()["busquedas_activas"] is False
    finally:
        contexto.close()
