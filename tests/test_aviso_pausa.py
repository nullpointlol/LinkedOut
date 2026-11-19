"""Cargar una oferta a mano con el análisis o la generación en pausa (decisión del 19/11: «la pausa es la pausa»).

La oferta no se analiza ni se le genera CV y mail mientras dure la pausa, pero queda en «Por analizar» y la UI lo
avisa con un botón que lleva a Procesos, donde se reanuda. Sin pausa, no hay aviso.
Portal de prueba real (fixture de test_ui_navegador). Medidas: texto del aviso según qué esté en pausa, el botón abre
el panel de Procesos, «Por analizar» sube en 1; a 390 y 1280 px.
"""
import pytest

from tests.test_ui_navegador import _abrir, navegador, portal  # noqa: F401

pytestmark = [pytest.mark.ui, pytest.mark.timeout(240)]

CONTAR = "() => Number(document.querySelector('#radarFiltros [data-estado=PENDIENTES] [data-count]').textContent)"
AVISOS = {
    "analisis": "El análisis está en pausa: reanudalo en Procesos",
    "generacion": "La generación de CV y mail está en pausa: reanudala en Procesos",
    "ambas": "El análisis y la generación de CV y mail están en pausa: reanudalos en Procesos",
}


def _pausar(page, base, analisis, generacion):
    for ruta, pausada in (("analisis", analisis), ("generacion", generacion)):
        r = page.request.post(f"{base}/api/radar/{ruta}", data={"pausada": pausada})
        assert r.ok, r.text()


def _cargar_texto(page):
    page.click("#btnNuevaOfertaEncabezado")
    page.click("#modalTab-manual")
    page.fill("#nuevoTitulo", "Cocinero de línea")
    page.fill("#nuevaEmpresa", "Bodegón de prueba")
    page.fill("#nuevoContenido", "Buscamos cocinero de línea con experiencia en parrilla y manejo de cámara de frío.")
    with page.expect_response("**/api/ofertas"):
        page.get_by_role("button", name="Cargar oferta").click()


@pytest.mark.parametrize("ancho", [390, 1280])
@pytest.mark.parametrize("pausa", ["analisis", "generacion", "ambas"])
def test_con_pausa_avisa_y_lleva_a_procesos(navegador, portal, ancho, pausa):  # noqa: F811
    base, _ = portal
    contexto, page, errores = _abrir(navegador, base, ancho)
    try:
        _pausar(page, base, pausa in ("analisis", "ambas"), pausa in ("generacion", "ambas"))
        page.click("#btnVistaManual")
        page.wait_for_selector("#radarFiltros button")
        page.wait_for_timeout(500)
        antes = page.evaluate(CONTAR)
        _cargar_texto(page)
        aviso = page.locator("#radarMensaje")
        aviso.wait_for(state="visible", timeout=10000)
        assert AVISOS[pausa] in aviso.inner_text()
        if pausa != "generacion":
            page.wait_for_function(f"() => ({CONTAR})() === {antes + 1}", timeout=10000)
        aviso.get_by_role("button", name="Ir a Procesos").click()
        page.locator("#radarProcesosPanel").wait_for(state="visible", timeout=5000)
        assert not errores.lista, errores.lista
    finally:
        contexto.close()


def test_sin_pausa_no_hay_aviso(navegador, portal):  # noqa: F811
    base, _ = portal
    contexto, page, errores = _abrir(navegador, base, 1280)
    try:
        _pausar(page, base, False, False)
        page.click("#btnVistaManual")
        page.wait_for_selector("#radarFiltros button")
        page.wait_for_timeout(500)
        _cargar_texto(page)
        page.wait_for_timeout(1000)
        texto = page.locator("#radarMensaje").inner_text() if page.locator("#radarMensaje").is_visible() else ""
        assert "en pausa" not in texto
        assert not errores.lista, errores.lista
    finally:
        contexto.close()
