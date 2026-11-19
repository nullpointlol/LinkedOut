"""Fallas que encontró el creador probando la versión final en el dev (19/11).

1. En escritorio, la fila de estados de LinkedIn (columna de 320 px) mostraba dos chips y escondía el resto detrás de
   «Filtros», sin forma de llegar con el mouse. Los estados pasaron a un selector (test_selector_estados.py); las
   etapas y los filtros de CV del editor van en varias líneas, todos visibles y tocables sin deslizar.
2. «Nueva oferta › Varios links» con 5 o 6 links: la lista mostraba 1. La vista Manual se cargaba antes de que
   existieran las ofertas nuevas y solo entraba la que se abría. Ahora la lista se recarga con la respuesta.

Portal de prueba real (fixture de test_ui_navegador). Medidas: a 1024, 1280 y 1440 px cada chip del editor está
dentro de su fila, sin desborde, y recibe el clic en su centro (nada lo tapa); tras cargar N links, Manual muestra N
ofertas nuevas y «Por analizar» sube en N.
"""
import pytest

from tests.test_ui_navegador import _abrir, navegador, portal  # noqa: F401

pytestmark = [pytest.mark.ui, pytest.mark.timeout(240)]

ALCANZABLES = """sel => { const fila = document.querySelector(sel); const caja = fila.getBoundingClientRect();
  return { desborda: fila.scrollWidth > fila.clientWidth + 1,
    chips: [...fila.querySelectorAll(':scope > button')].filter(b => b.offsetParent !== null).map(b => {
      const r = b.getBoundingClientRect(); const x = r.left + r.width / 2, y = r.top + r.height / 2;
      const arriba = document.elementFromPoint(x, y);
      return { texto: b.textContent.trim(), dentro: r.left >= caja.left - 1 && r.right <= caja.right + 1 && r.width > 0,
               tocable: !!arriba && (arriba === b || b.contains(arriba)) }; }) }; }"""


def _alcanzables(page, selector):
    medida = page.evaluate(ALCANZABLES, selector)
    assert not medida["desborda"], f"{selector} desborda: hay que deslizar"
    for chip in medida["chips"]:
        assert chip["dentro"] and chip["tocable"], (selector, chip)
    return medida["chips"]


@pytest.mark.parametrize("ancho", [1024, 1280, 1440])
def test_etapas_y_filtros_de_cv_del_editor_tambien(navegador, portal, ancho):  # noqa: F811
    base, _ = portal
    contexto, page, errores = _abrir(navegador, base, ancho)
    try:
        page.evaluate("cambiarVista('editor')")
        page.wait_for_timeout(300)
        assert len(_alcanzables(page, "#tabsLifecycleFiltros")) == 5
        assert len(_alcanzables(page, ".editor-fila-filtros > .seg")) == 3
    finally:
        contexto.close()


@pytest.mark.parametrize("ancho", [390, 1280])
def test_varios_links_aparecen_todos_en_manual(navegador, portal, ancho):  # noqa: F811
    """Como el creador: desde LinkedIn, «Varios links» pasa a Manual. La lista de Manual que se pide al cambiar de
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
