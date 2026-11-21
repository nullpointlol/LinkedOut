"""Tickets en un navegador real: errores de JavaScript, botón flotante, formulario y vista «Tickets»."""
import time

import pytest

from core import tickets
from tests.test_ui_navegador import _Errores, _sembrar_radar, _vista, navegador, portal  # noqa: F401

pytestmark = [pytest.mark.ui, pytest.mark.timeout(240)]

# html2canvas falso: el test no depende de la red; la captura real se prueba en el portal de prueba
_HTML2CANVAS_FALSO = """
window.html2canvas = async () => {
  const c = document.createElement('canvas'); c.width = 8; c.height = 8;
  const x = c.getContext('2d'); x.fillStyle = '#f00'; x.fillRect(0, 0, 8, 8); return c;
};
"""


def _abrir(navegador, base, ancho, **opciones):
    contexto = navegador.new_context(viewport={"width": ancho, "height": 900}, **opciones)
    contexto.add_init_script(_HTML2CANVAS_FALSO)
    page = contexto.new_page()
    page.set_default_timeout(15000)
    errores = _Errores(page, base)
    page.goto(base + "/", wait_until="load", timeout=30000)
    page.wait_for_function("document.readyState === 'complete' && !!document.getElementById('btnVistaRadar')")
    return contexto, page, errores


def _esperar(condicion, segundos=10):
    limite = time.monotonic() + segundos
    while time.monotonic() < limite:
        valor = condicion()
        if valor:
            return valor
        time.sleep(0.2)
    return condicion()


def test_un_error_de_javascript_queda_como_ticket(navegador, portal):  # noqa: F811
    base, _ = portal
    contexto, page, _ = _abrir(navegador, base, 1200)
    try:
        page.evaluate("setTimeout(() => { throw new Error('boom de prueba en la pantalla') }, 0)")
        page.evaluate("() => { Promise.reject(new Error('promesa de prueba sin catch')); }")
        js = _esperar(lambda: tickets.listar(tipo="js_error"))
        assert js and "boom de prueba" in js[0]["mensaje"]
        t = tickets.obtener(js[0]["id"])
        assert t["componente"] == "navegador" and t["datos"]["vista"] == "manual" and "Error" in t["traza"]
        assert _esperar(lambda: tickets.listar(tipo="promesa_rechazada"))
    finally:
        contexto.close()


@pytest.mark.parametrize("ancho", (375, 1200))
def test_boton_flotante_amarillo_sin_tickets_y_rojo_con_tickets(navegador, portal, ancho):  # noqa: F811
    base, _ = portal
    contexto, page, errores = _abrir(navegador, base, ancho)
    try:
        boton = page.locator("#btnReportarError")
        page.wait_for_function("document.getElementById('btnReportarError').dataset.hayAbiertos === '0'")
        caja = boton.bounding_box()
        assert caja["width"] >= 44 and caja["height"] >= 44
        assert boton.inner_text().strip() == ""  # solo el ícono
        assert boton.evaluate("b => getComputedStyle(b).backgroundColor") == "rgb(217, 168, 78)"  # --warning (rediseño E1)
        for vista in ("radar", "perfil", "tickets", "manual"):
            _vista(page, vista)
            assert boton.is_visible(), f"el botón flotante no se ve en la vista {vista}"
        tickets.reportar_error("radar", "bloqueo", "LinkedIn bloqueó las búsquedas")
        page.evaluate("window.Tickets.actualizarBoton()")
        page.wait_for_function("document.getElementById('btnReportarError').dataset.hayAbiertos === '1'")
        assert boton.evaluate("b => getComputedStyle(b).backgroundColor") == "rgb(228, 119, 119)"  # --danger (rediseño E1)
        assert not errores.lista, errores.lista
    finally:
        contexto.close()


@pytest.mark.parametrize("ancho", (375, 1200))
def test_reportar_desde_el_boton_lleva_el_contexto_y_la_captura(navegador, portal, ancho):  # noqa: F811
    base, _ = portal
    ids = _sembrar_radar(2)
    contexto, page, errores = _abrir(navegador, base, ancho)
    try:
        _vista(page, "radar")
        page.click(f'[data-post-id="{ids[1]}"]')
        page.wait_for_selector("[data-req]")
        page.evaluate("console.warn('aviso de consola de prueba')")
        page.click("#btnReportarError")
        page.wait_for_selector("#modalTicket:not([hidden])")
        page.click("#btnTicketEnviar")
        assert "Contanos qué pasó" in page.inner_text("#ticketEstado")
        page.fill("#ticketTexto", "Toqué Aprobar y no pasó nada")
        page.click("#btnTicketEnviar")
        page.wait_for_function("document.getElementById('ticketEstado').textContent.includes('quedó el ticket')")
        [t] = tickets.listar(tipo="reporte_usuario")
        t = tickets.obtener(t["id"])
        assert t["mensaje"] == "Toqué Aprobar y no pasó nada"
        assert t["datos"]["vista"] == "radar" and t["datos"]["post_id"] == ids[1]
        assert any("/api/radar/" in ll["url"] for ll in t["datos"]["llamadas"])
        assert any("aviso de consola de prueba" in c["texto"] for c in t["datos"]["consola"])
        assert t["tiene_captura"] and t["tiene_html"]
        assert "Toqué Aprobar" in t["pasos"] and f"post #{ids[1]}" in t["pasos"]
        page.wait_for_selector("#modalTicket[hidden]", state="attached")
        assert not errores.lista, errores.lista
    finally:
        contexto.close()


@pytest.mark.parametrize("ancho", (375, 1200))
def test_vista_tickets_filtra_muestra_detalle_cambia_estado_y_copia(navegador, portal, ancho):  # noqa: F811
    base, _ = portal
    abierto = tickets.reportar_error("radar", "sin_resultados", "La búsqueda «cv a» terminó sin resultados",
                                     datos={"consulta": "cv a", "cuenta": "secundaria"},
                                     html="<p>sin resultados</p>")
    resuelto = tickets.reportar_error("ia", "falla_agy", "AGY no respondió")
    tickets.cambiar_estado(resuelto, "resuelto")
    contexto, page, errores = _abrir(navegador, base, ancho, permissions=["clipboard-read", "clipboard-write"])
    try:
        _vista(page, "tickets")
        page.wait_for_selector(f'[data-ticket-id="{abierto}"]')
        assert page.locator(f'[data-ticket-id="{resuelto}"]').count() == 0  # por defecto, solo sin resolver
        page.select_option("#ticketsFiltroEstado", "resuelto")
        page.wait_for_selector(f'[data-ticket-id="{resuelto}"]')
        assert page.locator(f'[data-ticket-id="{abierto}"]').count() == 0
        page.select_option("#ticketsFiltroEstado", "")
        page.select_option("#ticketsFiltroComponente", "radar")
        page.wait_for_function(f"!document.querySelector('[data-ticket-id=\"{resuelto}\"]')")
        page.click(f'[data-ticket-id="{abierto}"]')
        page.wait_for_selector("[data-ticket-copiar]")
        detalle = page.inner_text("#vista-tickets .tk-detalle")
        for pedazo in ("Ticket #%d" % abierto, "Pasos para reproducir", "cv a", "secundaria", "Commit:"):
            assert pedazo in detalle
        page.click('[data-ticket-estado="en_curso"]')
        assert _esperar(lambda: tickets.obtener(abierto)["estado"] == "en_curso")
        # El detalle se redibuja después del cambio: se espera el nuevo (insignia «en curso» y su botón «Volver a
        # abierto»). El botón «resuelto» ya estaba en el detalle viejo; copiar ahí perdía el aviso al redibujar.
        page.wait_for_selector('#vista-tickets .tk-detalle .tk-insignia[data-estado="en_curso"]')
        page.wait_for_selector('#vista-tickets [data-ticket-estado="abierto"]')
        page.click("[data-ticket-copiar]")
        page.wait_for_function("document.querySelector('#vista-tickets .tk-detalle').textContent.includes('copiado')")
        copiado = page.evaluate("navigator.clipboard.readText()")
        assert copiado.startswith(f"Ticket #{abierto}") and "Pasos para reproducir" in copiado
        desborde = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert desborde <= 0, f"scroll horizontal de {desborde}px en la vista Tickets a {ancho}px"
        # Rediseño E4c: en el celular, lista → detalle (una pantalla por vez) y «‹ Tickets» vuelve a la lista
        volver = page.get_by_role("button", name="Volver a Tickets")
        if ancho < 1024:
            assert not page.locator(f'[data-ticket-id="{abierto}"]').is_visible()
            volver.click()
            assert page.locator(f'[data-ticket-id="{abierto}"]').is_visible()
            assert not page.locator("#vista-tickets .tk-detalle").is_visible()
        else:
            assert not volver.is_visible() and page.locator(f'[data-ticket-id="{abierto}"]').is_visible()
        menor = page.evaluate("""() => Math.min(...[...document.querySelectorAll('#vista-tickets *, #modalTicket *')]
            .filter(e => e.childNodes.length && [...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim()))
            .map(e => parseFloat(getComputedStyle(e).fontSize)))""")
        assert menor >= 12, f"texto de {menor}px en la vista Tickets"
        assert not errores.lista, errores.lista
    finally:
        contexto.close()
