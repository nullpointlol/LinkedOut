"""Fallas que encontró el usuario probando la versión final en el dev.

1. En escritorio, la fila de estados de LinkedIn (columna de 320 px) mostraba dos chips y escondía el resto detrás de
   «Filtros», sin forma de llegar con el mouse. Los estados y las etapas del editor pasaron a un selector
   (test_selector_estados.py y test_selector_etapas.py).
2. «Nueva oferta › Varios links» con 5 o 6 links: la lista mostraba 1. La vista Manual se cargaba antes de que
   existieran las ofertas nuevas y solo entraba la que se abría. Ahora la lista se recarga con la respuesta.

Portal de prueba real (fixture de test_ui_navegador). Medida: tras cargar N links, Manual muestra N ofertas nuevas
y «Por analizar» sube en N.
"""
import pytest

from tests.test_ui_navegador import _abrir, navegador, portal  # noqa: F401

pytestmark = [pytest.mark.ui, pytest.mark.timeout(240)]

@pytest.mark.parametrize("ancho", [390, 1280])
def test_varios_links_aparecen_todos_en_manual(navegador, portal, ancho):  # noqa: F811
    """Como el usuario: desde LinkedIn, «Varios links» pasa a Manual. La lista de Manual que se pide al cambiar de
    vista (antes de que existan las ofertas nuevas) llega tarde: no puede pisar la que se pidió después.
    Punta a punta: en el portal de prueba la carrera no siempre se da; la reproduce, siempre, el test de node
    tests/test_lista_respuesta_vieja.py (falla con el código anterior)."""
    base, _ = portal
    contexto, page, errores = _abrir(navegador, base, ancho)
    try:
        page.click("#btnVistaManual")
        page.wait_for_selector("#radarFiltros button", state="attached")
        page.wait_for_timeout(500)
        contar = "() => Number(document.querySelector('#radarFiltros [data-estado=PENDIENTES] [data-count]').textContent)"
        antes = page.evaluate(contar)
        page.click("#btnVistaRadar")
        page.wait_for_selector("#vista-radar[data-modo=radar]")
        retenidas = []
        primera = {"vista": False}

        def retener_la_primera(route):
            if not primera["vista"]:
                primera["vista"] = True  # antes de route.fetch(): mientras espera, el manejador puede volver a entrar
                # La respuesta se pide ya (sin las ofertas del lote) y se entrega después: llega tarde y vieja
                retenidas.append((route, route.fetch()))
            else:
                route.continue_()

        page.route("**/api/radar/posts?*fuente=manual*", retener_la_primera)
        page.click("#btnNuevaOfertaEncabezado")
        page.click("#modalTab-batch")
        page.fill("#inputBatchUrls", "\n".join(f"https://www.ejemplo.com/empleos/lote-{i}" for i in range(1, 6)))
        with page.expect_response("**/api/ofertas/batch"):
            page.get_by_role("button", name="Cargar oferta").click()
        page.wait_for_timeout(800)  # la lista pedida después (abrirPost) ya llegó
        assert retenidas, "no hubo un pedido de la lista al pasar a Manual"
        retenidas[0][0].fulfill(response=retenidas[0][1])
        page.wait_for_timeout(800)
        page.wait_for_function(f"() => ({contar})() === {antes + 5}", timeout=10000)
        page.evaluate("() => document.querySelector('#radarFiltros [data-estado=PENDIENTES]').click()")
        page.wait_for_timeout(300)
        nuevas = page.evaluate("""() => [...document.querySelectorAll('[data-post-id]')]
            .filter(e => e.textContent.includes('ejemplo.com/empleos/lote-')).length""")
        assert nuevas == 5
        assert not errores.lista, errores.lista
    finally:
        contexto.close()
