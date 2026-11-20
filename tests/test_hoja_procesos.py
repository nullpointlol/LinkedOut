"""Hoja «Procesos» rediseñada:

1. Hoy: fichas con números (Feed, Búsquedas, Enviados N/tope, Postulados, Ofertas).
2. Qué corre: una fila por proceso (leer el feed, hacer búsquedas, analizar, CV y mail urgentes, CV y mail del
   resto) con nombre, el estado en texto («Corriendo», «En pausa · N esperando», «Detenido») y UN solo tipo de
   control en todas las filas: un botón que dice lo que hace («Pausar» / «Reanudar»). Sin interruptores.
3. Portales: una fila por portal con su estado, «N/M hoy» y «Última vuelta hace X · N ofertas», y el mismo botón.
4. En curso: compacto, con el progreso de cada uno.
5. Avanzado (plegado): Modo serie y lo técnico (colas, errores, terminados).
Mismos ids y endpoints que antes. Cada acción hace lo que dice y el estado se actualiza solo (por SSE).

Portal de prueba real (fixture de test_ui_navegador). Medidas: secciones en orden; 5 fichas; en cada fila un solo
botón con «Pausar» o «Reanudar» y ningún role=switch; cada línea de una fila en un solo renglón y el botón al lado
del texto a 360 px; al tocar, la base cambia y la fila dice el estado nuevo; un cambio hecho desde otro lado aparece
sin reabrir la hoja.
"""
import json
from datetime import datetime, timedelta

import pytest

import core.paths
import server
from core import portales_store, radar_store
from tests.portales_prueba import reglas
from tests.test_ui_navegador import _abrir, _vista, navegador, portal  # noqa: F401

pytestmark = [pytest.mark.ui, pytest.mark.timeout(240)]

FILAS = ["radarFeedSwitch", "radarBusquedasSwitch", "radarAnalisisBtn", "radarGeneracionBtn", "radarBacklogBtn"]

MEDIR_FILAS = """() => [...document.querySelectorAll('#radarProcesosPanel .proc-fila-control')].map(f => {
  const botones = [...f.querySelectorAll('button')];
  const texto = f.querySelector('.proc-fila-texto').getBoundingClientRect();
  const b = botones[0] ? botones[0].getBoundingClientRect() : null;
  const lineas = [...f.querySelectorAll('.proc-fila-texto > *')].map(l => ({
    texto: l.textContent.trim(), alto: l.getBoundingClientRect().height,
    renglon: parseFloat(getComputedStyle(l).lineHeight) || 20 }));
  return { id: botones[0] && botones[0].id, n: botones.length, boton: botones[0] ? botones[0].textContent.trim() : '',
           al_lado: !!b && b.left >= texto.right - 1 && b.top < texto.bottom, lineas,
           estado: (f.querySelector('.proc-estado') || {}).textContent || '' }; })"""


def _abrir_hoja(page):
    _vista(page, "radar")
    page.click("#radarCola")
    page.wait_for_selector("#radarProcesosPanel:not([hidden])")
    page.wait_for_timeout(600)


def _con_portales():
    (core.paths.DATA_DIR / "candidato").mkdir(parents=True, exist_ok=True)
    datos = reglas(apagados=("computrabajo", "bumeran", "remoteok", "remotive", "himalayas"))
    (core.paths.DATA_DIR / "candidato" / "reglas_portales.json").write_text(json.dumps(datos), encoding="utf-8")
    hace = (datetime.now().astimezone() - timedelta(minutes=12)).isoformat(timespec="seconds")
    portales_store.registrar_lectura("jooble", "Pastelería", "pastelera", hace, hace, traidas=9, nuevas=6)


@pytest.mark.parametrize("ancho", [360, 390, 1280])
def test_secciones_fichas_y_un_solo_tipo_de_control(navegador, portal, ancho):  # noqa: F811
    base, _ = portal
    _con_portales()
    radar_store.update_radar_state(desired_running=True, estado="CORRIENDO", feed_activo=True, busquedas_activas=False)
    contexto, page, errores = _abrir(navegador, base, ancho)
    try:
        _abrir_hoja(page)
        titulos = page.eval_on_selector_all("#radarProcesosPanel .proc-seccion-titulo", "t => t.map(x => x.textContent.trim())")
        assert [t.split(" ")[0] for t in titulos] == ["Hoy", "Qué", "Portales", "En"], titulos
        fichas = page.eval_on_selector_all("#radarProcesosTopes .proc-ficha", "f => f.map(x => x.innerText.split('\\n'))")
        assert [f[0] for f in fichas] == ["Feed", "Búsquedas", "Enviados", "Postulados", "Ofertas"], fichas
        assert all(f[1].replace("/", "").replace("—", "0").isdigit() for f in fichas), fichas
        assert page.locator("#radarProcesosPanel [role=switch]").count() == 0
        filas = page.evaluate(MEDIR_FILAS)
        assert [f["id"] for f in filas[:5]] == FILAS, filas
        portales = [f for f in filas[5:]]
        assert len(portales) >= 2, filas
        for f in filas:
            assert f["n"] == 1 and f["boton"] in ("Pausar", "Reanudar"), f
            assert f["al_lado"], f
            for linea in f["lineas"]:
                assert linea["alto"] <= linea["renglon"] * 1.5, (f["id"], linea)  # nada se parte en dos líneas
        # El texto dice lo que pasa y el botón, lo que hace
        por_id = {f["id"]: f for f in filas}
        assert por_id["radarFeedSwitch"]["estado"].startswith("Corriendo") and por_id["radarFeedSwitch"]["boton"] == "Pausar"
        assert por_id["radarBusquedasSwitch"]["estado"].startswith("En pausa") and por_id["radarBusquedasSwitch"]["boton"] == "Reanudar"
        jooble = page.inner_text('#radarPortalesInterruptores [data-portal="jooble"]')
        assert "Última vuelta hace 12 min · 6 ofertas" in jooble and "/" in jooble and "hoy" in jooble, jooble
        bumeran = page.inner_text('#radarPortalesInterruptores [data-portal="bumeran"]')
        assert "Apagado" in bumeran
        assert page.locator('#radarPortalesInterruptores [data-portal="bumeran"] button').is_disabled()
        # Avanzado, plegado, con Modo serie y lo técnico
        assert page.evaluate("() => document.getElementById('radarProcesosAvanzado').open") is False
        assert page.locator("#radarProcesosAvanzado #radarModoSerie").count() == 1
        assert page.locator("#radarProcesosAvanzado #radarProcesosLista").count() == 1
        assert page.evaluate("() => document.documentElement.scrollWidth <= innerWidth")
        assert not errores.lista, errores.lista
    finally:
        contexto.close()


@pytest.mark.parametrize("boton,campo,pausado_si", [
    ("radarAnalisisBtn", "analisis_pausado", True),
    ("radarGeneracionBtn", "generacion_pausada", True),
    ("radarBacklogBtn", "backlog_activo", False),  # la cola de fondo se guarda al revés: activa o no
])
def test_cada_boton_hace_lo_que_dice(navegador, portal, boton, campo, pausado_si):  # noqa: F811
    base, _ = portal
    radar_store.update_radar_state(analisis_pausado=False, generacion_pausada=False, backlog_activo=True)
    contexto, page, errores = _abrir(navegador, base, 1280)
    try:
        _abrir_hoja(page)
        fila = page.locator(f"#{boton}").locator("xpath=ancestor::*[contains(@class,'proc-fila-control')][1]")
        for accion, queda, estado in (("Pausar", "Reanudar", "En pausa"), ("Reanudar", "Pausar", "Corriendo")):
            assert page.inner_text(f"#{boton}").strip() == accion
            page.click(f"#{boton}")
            page.wait_for_function(f"() => document.getElementById('{boton}').textContent.trim() === '{queda}'", timeout=5000)
            pausado = radar_store.get_radar_state()[campo] is pausado_si
            assert pausado is (accion == "Pausar"), (accion, campo)
            assert fila.locator(".proc-estado").inner_text().startswith(estado), fila.locator(".proc-estado").inner_text()
        assert not errores.lista, errores.lista
    finally:
        contexto.close()


def test_feed_y_busquedas_con_el_radar_detenido(navegador, portal):  # noqa: F811
    base, _ = portal
    radar_store.update_radar_state(desired_running=False, estado="DETENIDO", feed_activo=True, busquedas_activas=True)
    contexto, page, errores = _abrir(navegador, base, 390)
    try:
        _abrir_hoja(page)
        for id_ in ("radarFeedSwitch", "radarBusquedasSwitch"):
            assert page.locator(f"#{id_}").is_disabled()
            fila = page.locator(f"#{id_}").locator("xpath=ancestor::*[contains(@class,'proc-fila-control')][1]")
            assert fila.locator(".proc-estado").inner_text().startswith("Detenido")
        assert "Encendé el Radar" in page.inner_text("#radarInterruptoresAyuda")
        assert not errores.lista, errores.lista
    finally:
        contexto.close()


def test_feed_y_busquedas_con_el_radar_en_marcha_cambian_la_base(navegador, portal):  # noqa: F811
    base, _ = portal
    radar_store.update_radar_state(desired_running=True, estado="CORRIENDO", feed_activo=True, busquedas_activas=False)
    contexto, page, errores = _abrir(navegador, base, 1280)
    try:
        _abrir_hoja(page)
        page.click("#radarFeedSwitch")
        page.wait_for_function("() => document.getElementById('radarFeedSwitch').textContent.trim() === 'Reanudar'")
        assert radar_store.get_radar_state()["feed_activo"] is False
        page.once("dialog", lambda d: d.accept())
        page.click("#radarBusquedasSwitch")
        page.wait_for_function("() => document.getElementById('radarBusquedasSwitch').textContent.trim() === 'Pausar'")
        assert radar_store.get_radar_state()["busquedas_activas"] is True
        assert not errores.lista, errores.lista
    finally:
        contexto.close()


def test_un_portal_se_pausa_y_se_reanuda(navegador, portal):  # noqa: F811
    base, _ = portal
    _con_portales()
    contexto, page, errores = _abrir(navegador, base, 390)
    try:
        _abrir_hoja(page)
        fila = page.locator('#radarPortalesInterruptores [data-portal="jooble"]')
        assert fila.locator("button").inner_text().strip() == "Pausar"
        fila.locator("button").click()
        page.wait_for_function("""() => document.querySelector('#radarPortalesInterruptores [data-portal="jooble"] button')
            .textContent.trim() === 'Reanudar'""")
        assert "jooble" in portales_store.pausados()
        assert page.locator('#radarPortalesInterruptores [data-portal="jooble"] .proc-estado').inner_text().startswith("En pausa")
        page.locator('#radarPortalesInterruptores [data-portal="jooble"] button').click()
        page.wait_for_function("""() => document.querySelector('#radarPortalesInterruptores [data-portal="jooble"] button')
            .textContent.trim() === 'Pausar'""")
        assert "jooble" not in portales_store.pausados()
        assert not errores.lista, errores.lista
    finally:
        contexto.close()


def test_el_estado_se_actualiza_solo(navegador, portal):  # noqa: F811
    """Un cambio hecho desde otro lado (otra pestaña, el celular) aparece sin cerrar y reabrir la hoja."""
    base, radar = portal

    async def emitir():  # el Radar de prueba es un doble: emite el SSE como el real (RadarLoop.notify_status)
        await server.broadcast_event("radar_status", radar_store.get_radar_state())
    radar.notify_status.side_effect = emitir
    radar_store.update_radar_state(analisis_pausado=False, backlog_activo=False)
    contexto, page, errores = _abrir(navegador, base, 1280)
    try:
        _abrir_hoja(page)
        assert page.inner_text("#radarAnalisisBtn").strip() == "Pausar"
        page.request.post(f"{base}/api/radar/analisis", data={"pausada": True})
        page.request.post(f"{base}/api/radar/backlog", data={"activo": True})
        page.wait_for_function("() => document.getElementById('radarAnalisisBtn').textContent.trim() === 'Reanudar'", timeout=8000)
        page.wait_for_function("() => document.getElementById('radarBacklogBtn').textContent.trim() === 'Pausar'", timeout=8000)
        assert page.inner_text("#radarAnalisisEstado").startswith("En pausa")
        assert page.inner_text("#radarBacklogQuedan").startswith("Corriendo")
        assert not errores.lista, errores.lista
    finally:
        contexto.close()


def test_rechazar_la_confirmacion_no_enciende_las_busquedas(navegador, portal):  # noqa: F811
    """Antes en test_ui_interruptores.py (feed y búsquedas eran interruptores; ahora, el mismo botón que el resto)."""
    base, _ = portal
    radar_store.update_radar_state(desired_running=True, estado="CORRIENDO", busquedas_activas=False)
    contexto, page, errores = _abrir(navegador, base, 1200)
    try:
        _abrir_hoja(page)
        page.evaluate("window.confirm = () => false")
        page.click("#radarBusquedasSwitch")
        page.wait_for_timeout(500)
        assert page.inner_text("#radarBusquedasSwitch").strip() == "Reanudar"
        assert radar_store.get_radar_state()["busquedas_activas"] is False
        assert not errores.lista, errores.lista
    finally:
        contexto.close()
