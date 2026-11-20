"""Tests de interfaz con un navegador real (Playwright + Edge/Chromium headless).

Levantan el portal en un puerto libre, con la base y el perfil aislados de cada test, la IA simulada y el Radar
reemplazado por un doble (nunca abre LinkedIn ni envía nada). Recorren las tres vistas a 375 y 1200 px, pulsan
cada botón visible y siguen los flujos críticos hasta la base de datos.

Se saltean solos si no hay un navegador para Playwright (Edge o Chromium).
"""
import json
import socket
import threading
import time
from unittest.mock import AsyncMock

import pytest
import uvicorn

import server
from core import database, radar_store
from core.bridge import AntigravityBridge
from core.radar import RadarLoop

pytestmark = [pytest.mark.ui, pytest.mark.timeout(240)]

ANCHOS = (375, 1200)
VISTAS = ("manual", "radar", "portales", "perfil", "tickets")
TEXTO_OFERTA = ("Estamos buscando Analista Funcional para proyecto bancario. Requisitos: SQL, relevamiento de "
                "requerimientos, historias de usuario y Jira. Modalidad híbrida en CABA. Enviar CV a rrhh@acme.test")


# ---------------------------------------------------------------- navegador

def _lanzar(pw):
    for opciones in ({"channel": "msedge"}, {}):
        try:
            return pw.chromium.launch(headless=True, **opciones)
        except Exception:
            continue
    return None


@pytest.fixture(scope="module")
def navegador():
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as pw:
        browser = _lanzar(pw)
        if browser is None:
            pytest.skip("no hay Edge ni Chromium para Playwright")
        yield browser
        browser.close()


# ---------------------------------------------------------------- portal en vivo

def _puerto_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


ANALISIS_FALSO = {
    "es_oferta": True, "empresa": "Acme", "puesto": "Analista Funcional",
    "dolores_empresa": ["Ordenar el relevamiento de requerimientos"],
    "requisitos": [
        {"nombre": "SQL", "tipo": "REQUISITO_EXCLUYENTE", "estado": "MATCH_DIRECTO", "approved": True, "justificacion": "SQL en la base"},
        {"nombre": "Jira", "tipo": "REQUISITO_EXCLUYENTE", "estado": "MATCH_DIRECTO", "approved": True, "justificacion": "Jira en la base"},
        {"nombre": "Historias de usuario", "tipo": "RESPONSABILIDAD", "estado": "TRANSFERIBLE", "approved": True, "justificacion": "Relevamiento"},
        {"nombre": "Salesforce", "tipo": "REQUISITO_DESEABLE", "estado": "BRECHA_REAL", "approved": False, "justificacion": "No figura"},
    ],
    "match_score": 80.0,
}


@pytest.fixture
def portal(monkeypatch, tmp_path):
    """Portal real (server.app) en un hilo, con la base aislada del test (conftest) y dobles de IA y Radar."""
    monkeypatch.setenv("LINKEDOUT_SIN_RADAR", "1")

    async def ia_falsa(self, prompt, json_mode=True, **kwargs):
        return json.loads(json.dumps(ANALISIS_FALSO)) if json_mode else "Texto de prueba."
    monkeypatch.setattr(AntigravityBridge, "run_prompt", ia_falsa)

    radar = AsyncMock(spec=RadarLoop)
    radar.estado_cuentas = lambda: [
        {"cuenta": "principal", "con_sesion": True, "en_uso": True, "pausada_hasta": None},
        {"cuenta": "secundaria", "con_sesion": False, "en_uso": False, "pausada_hasta": None},
    ]
    radar.pedir_login_secundaria = lambda: None
    radar.debug_dir = radar._debug_dir = tmp_path / "radar_debug"
    monkeypatch.setattr(server, "radar_loop", radar)

    puerto = _puerto_libre()
    config = uvicorn.Config(server.app, host="127.0.0.1", port=puerto, log_level="warning", lifespan="on",
                            timeout_graceful_shutdown=2)
    servidor = uvicorn.Server(config)
    hilo = threading.Thread(target=servidor.run, daemon=True)
    hilo.start()
    limite = time.monotonic() + 20
    while not servidor.started:
        if time.monotonic() > limite or not hilo.is_alive():
            pytest.fail("el portal de prueba no arrancó")
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{puerto}", radar
    finally:
        servidor.should_exit = True
        hilo.join(timeout=15)


def _sembrar_radar(n=3):
    """Posts del Radar ya analizados (el doble del Radar no los procesa)."""
    ids = []
    for i in range(n):
        pid = radar_store.insert_post_if_new({
            "urn": f"urn:li:activity:{9000 + i}", "url": f"https://www.linkedin.com/feed/update/urn:li:activity:{9000 + i}/",
            "autor": f"Selección Acme {i}", "cargo_autor": "Talent Acquisition", "fecha_rel": "2 h",
            "texto": f"{TEXTO_OFERTA} (aviso {i})", "emails": ["rrhh@acme.test"],
        }, "consulta de prueba")
        analisis = json.loads(json.dumps(ANALISIS_FALSO))
        radar_store.update_post(pid, estado="ANALIZADA", analisis=analisis,
                                triage={"oferta": 1, "puesto": "Analista Funcional", "canal": "email"})
        ids.append(pid)
    return ids


def _sembrar_portales():
    """Una oferta de Jooble sin mail y una de Indeed con mail, ya analizadas (Portales)."""
    from core import portales_store
    ids = []
    for portal, id_, titulo, empresa, mails in (("jooble", "1", "Analista Funcional SSr", "Umbrella", []),
                                                ("indeed", "in-2", "Coordinador de mesa de ayuda", "Globex",
                                                 ["rrhh@globex.test"])):
        _, pid = portales_store.guardar_oferta({
            "portal": portal, "id": id_, "urn": f"{portal}:{id_}", "titulo": titulo, "empresa": empresa,
            "ubicacion": "CABA", "descripcion": TEXTO_OFERTA, "completa": True,
            "link": f"https://{portal}.test/aviso/{id_}", "link_directo": f"https://{empresa.lower()}.test/postular",
            "emails": mails, "fecha": "",
        }, "Análisis")
        radar_store.update_post(pid, estado="ANALIZADA", analisis=json.loads(json.dumps(ANALISIS_FALSO)),
                                triage={"oferta": 1, "puesto": titulo, "canal": "email" if mails else "link"})
        ids.append(pid)
    return ids


class _Errores:
    """Junta errores de JavaScript, respuestas 5xx y fallas de red contra el portal."""

    def __init__(self, page, base):
        self.lista = []
        page.on("pageerror", lambda e: self.lista.append(f"JS: {e}"))
        page.on("console", lambda m: m.type == "error" and "Failed to load resource" not in m.text
                and self.lista.append(f"consola: {m.text}"))
        page.on("response", lambda r: r.url.startswith(base) and r.status >= 500
                and self.lista.append(f"{r.status} {r.request.method} {r.url}"))
        page.on("dialog", lambda d: d.accept())


def _abrir(navegador, base, ancho):
    contexto = navegador.new_context(viewport={"width": ancho, "height": 900})
    page = contexto.new_page()
    # Holgado: con la suite en paralelo la PC está cargada. No se espera «red quieta»: el canal SSE queda abierto.
    page.set_default_timeout(15000)
    errores = _Errores(page, base)
    page.goto(base + "/", wait_until="load", timeout=30000)
    page.wait_for_function("document.readyState === 'complete' && !!document.getElementById('btnVistaRadar')")
    return contexto, page, errores


def _vista(page, vista):
    if vista == "portales":
        # Portales es la vista del Radar en otro modo; en el celular no tiene botón (por ahora, solo monitor)
        if not page.is_visible("#btnVistaPortales"):
            return
        page.click("#btnVistaPortales")
        page.wait_for_selector("#vista-radar[data-modo=portales]:not([hidden])")
        page.wait_for_timeout(400)
        return
    if vista == "tickets" and not page.is_visible("#btnVistaTickets"):
        # En el celular la vista Tickets se abre desde el botón flotante
        page.click("#btnReportarError")
        page.click("#btnTicketVerTodos")
    else:
        page.click(f"#btnVista{vista.capitalize()}")
    # Rediseño E3: salir de Mi perfil con cambios (el barrido quita chips) pide confirmación; se sale sin guardar
    salir = page.get_by_role("button", name="Salir sin guardar", exact=True)
    if salir.is_visible():
        salir.click()
    page.wait_for_selector(f"#vista-{vista}:not([hidden])" if vista != "manual" else "#btnVistaManual")
    page.wait_for_timeout(400)


# ---------------------------------------------------------------- tests

@pytest.mark.parametrize("ancho", ANCHOS)
def test_las_tres_vistas_cargan_sin_errores_ni_scroll_horizontal(navegador, portal, ancho):
    base, _ = portal
    _sembrar_radar()
    _sembrar_portales()
    contexto, page, errores = _abrir(navegador, base, ancho)
    try:
        for vista in VISTAS:
            _vista(page, vista)
            desborde = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
            assert desborde <= 0, f"scroll horizontal de {desborde}px en la vista {vista} a {ancho}px"
        assert not errores.lista, errores.lista
    finally:
        contexto.close()


@pytest.mark.parametrize("ancho", ANCHOS)
def test_cada_boton_visible_responde_sin_errores(navegador, portal, ancho):
    """Pulsa cada botón visible y habilitado de cada vista (confirmaciones aceptadas): ningún error de
    JavaScript, ninguna respuesta 5xx y la página sigue viva."""
    base, _ = portal
    _sembrar_radar()
    _sembrar_portales()
    contexto, page, errores = _abrir(navegador, base, ancho)
    pulsados = 0
    try:
        for vista in VISTAS:
            _vista(page, vista)
            vistos = set()
            for _ in range(3):  # algunos botones aparecen recién después de pulsar otros
                # Foto de los botones de este momento: pulsar uno puede redibujar la lista
                for boton in page.query_selector_all("button"):
                    try:
                        if not boton.is_visible() or not boton.is_enabled():
                            continue
                        clave = boton.evaluate("b => b.id || b.getAttribute('aria-label') || b.textContent.trim()")
                    except Exception:
                        continue  # se quitó del DOM
                    if not clave or clave in vistos or clave.startswith("btnVista"):
                        continue
                    try:
                        boton.click(timeout=1500)
                    except Exception:
                        continue  # tapado por un panel abierto: se reintenta en la pasada siguiente
                    vistos.add(clave)
                    pulsados += 1
                    page.wait_for_timeout(150)
                    page.keyboard.press("Escape")
            page.wait_for_timeout(300)
            assert page.evaluate("1 + 1") == 2
        print(f"botones pulsados a {ancho}px: {pulsados}")
        assert pulsados >= 40, f"solo se pudieron pulsar {pulsados} botones"
        assert not errores.lista, errores.lista
    finally:
        contexto.close()


def test_escape_cierra_el_modal_nueva_oferta(navegador, portal):
    base, _ = portal
    contexto, page, errores = _abrir(navegador, base, 1200)
    try:
        page.locator("button", has_text="Nueva Oferta").first.click()
        page.wait_for_selector("#modalNueva:not(.hidden)")
        page.keyboard.press("Escape")
        page.wait_for_selector("#modalNueva.hidden", state="attached")
        assert not errores.lista, errores.lista
    finally:
        contexto.close()


def test_radar_ciclar_un_requisito_llega_a_la_base(navegador, portal):
    base, _ = portal
    pid = _sembrar_radar(1)[0]
    contexto, page, errores = _abrir(navegador, base, 1200)
    try:
        _vista(page, "radar")
        page.click(f'[data-post-id="{pid}"]')
        boton = page.locator('[data-req="Salesforce"]').first
        boton.wait_for(timeout=5000)
        boton.click()
        page.wait_for_timeout(800)
        req = next(r for r in radar_store.get_post(pid)["analisis"]["requisitos"] if r["nombre"] == "Salesforce")
        assert req["estado"] == "TRANSFERIBLE" and req["aprendido"] is True
        assert not errores.lista, errores.lista
    finally:
        contexto.close()


def test_radar_descartar_un_post_llega_a_la_base(navegador, portal):
    base, _ = portal
    pid = _sembrar_radar(1)[0]
    contexto, page, errores = _abrir(navegador, base, 1200)
    try:
        _vista(page, "radar")
        page.click(f'[data-post-id="{pid}"]')
        descartar = page.locator("#radarDescartarBtn")
        descartar.wait_for(state="visible", timeout=5000)
        descartar.click()
        page.locator("#radarDescartarBtn", has_text="¿Confirmar descarte?").wait_for(timeout=3000)
        descartar.click()  # confirma en el mismo botón
        page.wait_for_function(f"!document.querySelector('#radarDescartarBtn').textContent.includes('Confirmar')")
        page.wait_for_timeout(800)
        assert radar_store.get_post(pid)["estado"] in ("DESCARTADO", "IGNORADA")
        assert not errores.lista, errores.lista
    finally:
        contexto.close()


def test_mi_perfil_guarda_en_el_archivo_del_candidato(navegador, portal):
    import core.paths
    base, _ = portal
    contexto, page, errores = _abrir(navegador, base, 1200)
    try:
        _vista(page, "perfil")
        area = page.locator("#vista-perfil textarea").first
        area.wait_for(timeout=5000)
        original = area.input_value()
        area.fill(original.rstrip("\n") + "\n• Habilidad agregada desde la prueba de interfaz.\n")
        page.locator("#vista-perfil button", has_text="Guardar").first.click()
        page.wait_for_timeout(800)
        archivos = list((core.paths.DATA_DIR / "candidato").glob("*"))
        assert any("Habilidad agregada desde la prueba de interfaz" in f.read_text(encoding="utf-8", errors="ignore")
                   for f in archivos if f.is_file()), "lo guardado en Mi perfil no llegó a los archivos del candidato"
        assert not errores.lista, errores.lista
    finally:
        contexto.close()


def test_radar_generar_cv_a_mano_con_la_generacion_en_pausa(navegador, portal):
    """La pausa solo frena lo automático: «Generar CV y mail» sigue habilitado y el aviso no
    dice que está pausado."""
    base, radar = portal
    pid = _sembrar_radar(1)[0]
    radar_store.update_radar_state(generacion_pausada=True)
    contexto, page, errores = _abrir(navegador, base, 1200)
    try:
        _vista(page, "radar")
        page.wait_for_function("document.getElementById('radarGeneracionBtn').textContent.trim() === 'Reanudar'")
        page.click(f'[data-post-id="{pid}"]')
        boton = page.locator("#radarGenerarCvBtn")
        boton.wait_for(state="visible", timeout=5000)
        assert boton.is_enabled()
        boton.click()
        aviso = page.locator("#radarMensajeTexto")
        aviso.wait_for(state="visible", timeout=5000)
        texto = aviso.inner_text()
        assert "CV en cola" in texto and "pausa" not in texto.lower(), texto
        radar.generate_cv.assert_awaited_once_with(pid)
        assert radar_store.get_radar_state()["generacion_pausada"] is True
        assert not errores.lista, errores.lista
    finally:
        contexto.close()


# ---------------------------------------------------------------- rendimiento en el navegador

from tests.test_rendimiento_api import base_grande  # noqa: E402,F401  (base del tamaño de la real)

TOPE_RADAR_MS = 4000  # medido el 11/11 sin otra carga: 1660 ms con 2760 tarjetas


@pytest.mark.rendimiento
@pytest.mark.aislado
def test_la_vista_radar_con_la_base_grande_se_dibuja_a_tiempo(navegador, portal, base_grande):
    base, _ = portal
    contexto, page, errores = _abrir(navegador, base, 1200)
    try:
        inicio = time.perf_counter()
        page.click("#btnVistaRadar")
        page.wait_for_selector("#vista-radar [data-post-id]", timeout=TOPE_RADAR_MS * 2)
        ms = (time.perf_counter() - inicio) * 1000
        tarjetas = page.locator("#vista-radar [data-post-id]").count()
        print(f"\nVista Radar con {tarjetas} tarjetas: {ms:.0f} ms (tope {TOPE_RADAR_MS} ms)")
        assert tarjetas > 0
        assert ms <= TOPE_RADAR_MS, f"la vista Radar tardó {ms:.0f} ms"
        assert not errores.lista, errores.lista
    finally:
        contexto.close()


def test_portales_a_1200_solo_ofertas_de_portales_con_logo_y_postular(navegador, portal):
    """Pestañas Manual · LinkedIn · Portales · Tickets; Portales muestra solo sus ofertas, con logo, chips por portal
    y «Postular en el sitio» cuando no hay mail; LinkedIn no las muestra."""
    linkedin = _sembrar_radar(1)
    jooble, indeed = _sembrar_portales()
    contexto, page, errores = _abrir(navegador, portal[0], 1200)
    try:
        orden = page.evaluate("""() => [...document.querySelectorAll('#selectorVista button')]
            .map(b => b.textContent.trim())""")
        assert orden == ["Manual", "LinkedIn", "Portales", "Tickets", "Mi perfil"]
        _vista(page, "radar")
        page.wait_for_selector(f"#radarLista [data-post-id='{linkedin[0]}']")
        assert page.locator(f"[data-post-id='{jooble}']").count() == 0
        _vista(page, "portales")
        page.wait_for_selector(f"#radarLista [data-post-id='{jooble}']")
        tarjetas = page.evaluate("""() => [...document.querySelectorAll('#radarLista [data-post-id]')].map(c => ({
            id: Number(c.dataset.postId), logo: (c.querySelector('.portal-logo') || {}).textContent || '',
            postular: (c.querySelector('a[data-postular-sitio]') || {}).href || ''}))""")
        assert sorted(t["id"] for t in tarjetas) == sorted([jooble, indeed])
        por_id = {t["id"]: t for t in tarjetas}
        assert por_id[jooble]["logo"] == "Jooble" and por_id[indeed]["logo"] == "Indeed"
        assert por_id[jooble]["postular"] == "https://umbrella.test/postular"
        assert por_id[indeed]["postular"] == ""  # tiene mail: se postula por mail
        assert page.is_visible("#radarFiltroPortal") and not page.is_visible("#radarPanelFeed")
        cuentas = page.evaluate("""() => Object.fromEntries([...document.querySelectorAll('#radarFiltroPortal button')]
            .map(b => [b.dataset.portal, b.querySelector('[data-count]').textContent]))""")
        assert cuentas == {"TODOS": "2", "jooble": "1", "indeed": "1", "computrabajo": "0", "bumeran": "0"}
        page.click("#radarFiltroPortal [data-portal=indeed]")
        page.wait_for_timeout(300)
        assert page.evaluate("() => [...document.querySelectorAll('#radarLista [data-post-id]')]"
                             ".map(c => Number(c.dataset.postId))") == [indeed]
        page.click(f"#radarLista [data-post-id='{indeed}']")
        page.wait_for_selector("#radarDetallePost [data-portal-detalle=indeed]")
        desborde = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert desborde <= 0
        _vista(page, "tickets")
        assert not errores.lista, errores.lista
    finally:
        contexto.close()
