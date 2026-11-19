"""App de escritorio (PWA, docs/app-escritorio/plan.md): instalable desde Edge sobre localhost, sin datos nuevos expuestos.

- Archivos: manifest, íconos, service worker, pantalla sin portal y script de instalación.
- Superficie: lo nuevo es público y sin datos; el SW no guarda la página ni la API.
- Navegador real: Chromium no da errores de instalabilidad; con el portal apagado se ve la pantalla propia y,
  al volver, recarga sola; «Instalar app» solo en la PC.
"""
import json
import re
import socket
import struct
import threading
import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import uvicorn
from fastapi.testclient import TestClient

import server
from core.radar import RadarLoop
from core.radar_api import ESTADO_HOSTS_DE_TEST

STATIC = Path(server.__file__).resolve().parent / "static"
PWA = STATIC / "pwa"
TITULO_SIN_PORTAL = "LinkedOut no está corriendo"


def _manifest() -> dict:
    return json.loads((PWA / "manifest.webmanifest").read_text(encoding="utf-8"))


def _lado_png(path: Path):
    """(ancho, alto) leídos del encabezado IHDR, sin dependencias."""
    datos = path.read_bytes()[:24]
    assert datos[:8] == b"\x89PNG\r\n\x1a\n", f"{path.name} no es PNG"
    return struct.unpack(">II", datos[16:24])


@pytest.fixture
def afuera(monkeypatch):
    """Un cliente de la red local sin emparejar, como en producción (sin el hook de hosts de test)."""
    monkeypatch.setattr(server.app.state, ESTADO_HOSTS_DE_TEST, set(), raising=False)
    return TestClient(server.app, base_url="http://192.168.1.50:1111", client=("192.168.1.51", 51000))


# ---------------------------------------------------------------- archivos

def test_manifest_instalable_como_ventana_propia():
    m = _manifest()
    assert m["name"] == m["short_name"] == "LinkedOut"
    assert m["id"] == m["start_url"] == m["scope"] == "/"
    assert m["display"] == "standalone"
    assert re.fullmatch(r"#[0-9a-fA-F]{6}", m["theme_color"]) and re.fullmatch(r"#[0-9a-fA-F]{6}", m["background_color"])


def test_iconos_existen_con_el_tamano_declarado():
    pngs = [i for i in _manifest()["icons"] if i["type"] == "image/png"]
    for icono in pngs:
        assert icono["src"].startswith("/static/pwa/")
        ancho, alto = (int(x) for x in icono["sizes"].split("x"))
        assert _lado_png(PWA / Path(icono["src"]).name) == (ancho, alto)
    lados = {(i["sizes"], i["purpose"]) for i in pngs}
    assert {("192x192", "any"), ("512x512", "any"), ("512x512", "maskable")} <= lados


def test_service_worker_solo_atiende_navegaciones_y_no_guarda_datos():
    sw = (PWA / "sw.js").read_text(encoding="utf-8")
    assert 'pedido.mode === "navigate"' in sw
    assert "/api" not in sw
    guardados = re.search(r"const GUARDADOS = \[(.*?)\];", sw).group(1)
    assert guardados == ('SIN_PORTAL, "/static/pwa/offline.css", "/static/pwa/offline.js", "/static/pwa/icono.svg"'),         "solo la pantalla sin portal y lo que usa: nunca la página ni la API"
    assert "GUARDADOS.includes(url.pathname)" in sw, "fuera de las navegaciones, solo atiende lo guardado"
    assert '"/static/pwa/offline.html"' in sw


@pytest.mark.parametrize("nombre", ["offline.html", "offline.css", "offline.js", "pwa.js", "sw.js"])
def test_sin_red_ni_innerhtml_ni_texto_chico(nombre):
    texto = (PWA / nombre).read_text(encoding="utf-8")
    assert not re.search(r"https?://", texto), "sin dependencias de red: anda con el portal apagado y sin internet"
    assert "innerHTML" not in texto
    for px in re.findall(r"font-size:\s*(\d+)px", texto):
        assert int(px) >= 12


def test_pantalla_sin_portal_sin_codigo_en_linea():
    """Anda aunque el portal sume una CSP estricta (sin 'unsafe-inline')."""
    html = (PWA / "offline.html").read_text(encoding="utf-8")
    assert "<style" not in html and " style=" not in html and not re.search(r"\son\w+=", html)
    assert re.findall(r"<script[^>]*>", html) == ['<script src="/static/pwa/offline.js">']


def test_index_enlaza_manifest_iconos_y_script():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert '<link rel="manifest" href="/static/pwa/manifest.webmanifest">' in html
    # Rediseño E5: el color de la barra del sistema es el fondo de tokens.css, igual en el manifest y sin portal
    import json
    fondo = re.search(r"--bg-canvas:\s*(#[0-9A-Fa-f]{6})", (STATIC / "css" / "tokens.css").read_text(encoding="utf-8")).group(1)
    assert f'<meta name="theme-color" content="{fondo}">' in html
    manifest = json.loads((PWA / "manifest.webmanifest").read_text(encoding="utf-8"))
    assert manifest["theme_color"] == manifest["background_color"] == fondo
    assert f'<meta name="theme-color" content="{fondo}">' in (PWA / "offline.html").read_text(encoding="utf-8")
    assert '<link rel="icon" href="/static/pwa/icono.svg" type="image/svg+xml">' in html
    assert '<script src="/static/pwa/pwa.js" defer></script>' in html
    assert re.search(r'<span id="pwaInstalar" hidden>\s*</span>', html)
    assert "<title>LinkedOut</title>" in html


# ---------------------------------------------------------------- superficie (S1)

def test_manifest_e_iconos_publicos_sin_datos(afuera):
    r = afuera.get("/static/pwa/manifest.webmanifest")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/manifest+json")
    assert afuera.get("/static/pwa/icono-192.png").status_code == 200


def test_sw_en_la_raiz_publico_y_sin_cache(afuera):
    r = afuera.get("/sw.js")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/javascript")
    assert r.headers["cache-control"] == "no-cache"
    assert r.content == (PWA / "sw.js").read_bytes()


def test_la_api_sigue_cerrada_desde_afuera(afuera):
    assert afuera.get("/api/ofertas").status_code == 403


# ---------------------------------------------------------------- navegador real

CANALES = ({"channel": "msedge"}, {})  # Edge si está; si no, el Chromium de Playwright


@pytest.fixture(scope="module")
def playwright():
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as pw:
        yield pw


@pytest.fixture(scope="module")
def navegador(playwright):
    for opciones in CANALES:
        try:
            browser = playwright.chromium.launch(headless=True, **opciones)
            break
        except Exception:
            continue
    else:
        pytest.skip("no hay Edge ni Chromium para Playwright")
    yield browser
    browser.close()


@pytest.fixture
def perfil_persistente(playwright, tmp_path):
    """Contexto con perfil en disco: en uno de incógnito (el de `new_context`) Chromium no ofrece instalar."""
    for opciones in CANALES:
        try:
            contexto = playwright.chromium.launch_persistent_context(
                str(tmp_path / "perfil"), headless=True, viewport={"width": 1200, "height": 800}, **opciones)
            break
        except Exception:
            continue
    else:
        pytest.skip("no hay Edge ni Chromium para Playwright")
    yield contexto
    contexto.close()


class _Portal:
    """server.app en un hilo, en un puerto fijo, para apagarlo y volver a levantarlo en el mismo origen."""

    def __init__(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.puerto = s.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.puerto}"
        self._servidor = self._hilo = None

    def levantar(self):
        config = uvicorn.Config(server.app, host="127.0.0.1", port=self.puerto, log_level="warning",
                                lifespan="on", timeout_graceful_shutdown=2)
        self._servidor = uvicorn.Server(config)
        self._hilo = threading.Thread(target=self._servidor.run, daemon=True)
        self._hilo.start()
        limite = time.monotonic() + 20
        while not self._servidor.started:
            if time.monotonic() > limite or not self._hilo.is_alive():
                pytest.fail("el portal de prueba no arrancó")
            time.sleep(0.05)

    def apagar(self):
        if self._servidor:
            self._servidor.should_exit = True
            self._hilo.join(timeout=15)
            self._servidor = None


@pytest.fixture
def portal(monkeypatch, tmp_path):
    monkeypatch.setenv("LINKEDOUT_SIN_RADAR", "1")
    radar = AsyncMock(spec=RadarLoop)
    radar.estado_cuentas = lambda: []
    radar.debug_dir = radar._debug_dir = tmp_path / "radar_debug"
    monkeypatch.setattr(server, "radar_loop", radar)
    p = _Portal()
    p.levantar()
    try:
        yield p
    finally:
        p.apagar()


def _abrir_controlada(navegador, url):
    """Página ya controlada por el service worker (la primera carga lo instala; la recarga lo usa)."""
    contexto = navegador.new_context(viewport={"width": 1200, "height": 800})
    pagina = contexto.new_page()
    pagina.goto(url)
    pagina.evaluate("navigator.serviceWorker.ready.then(() => true)")
    pagina.reload()
    pagina.wait_for_function("!!navigator.serviceWorker.controller")
    return contexto, pagina


@pytest.mark.ui
@pytest.mark.timeout(120)
def test_chromium_la_considera_instalable(perfil_persistente, portal):
    pagina = perfil_persistente.new_page()
    pagina.goto(portal.url + "/")
    pagina.evaluate("navigator.serviceWorker.ready.then(() => true)")
    cdp = perfil_persistente.new_cdp_session(pagina)
    errores = cdp.send("Page.getInstallabilityErrors")["installabilityErrors"]
    assert errores == [], errores
    manifest = cdp.send("Page.getAppManifest")
    assert manifest["url"].endswith("/static/pwa/manifest.webmanifest") and not manifest["errors"], manifest["errors"]


@pytest.mark.ui
@pytest.mark.timeout(120)
def test_portal_apagado_muestra_pantalla_propia_y_vuelve_solo(navegador, portal):
    contexto, pagina = _abrir_controlada(navegador, portal.url + "/")
    try:
        portal.apagar()
        pagina.goto(portal.url + "/")
        assert pagina.get_by_role("heading", name=TITULO_SIN_PORTAL).is_visible()
        portal.levantar()
        # La pantalla reintenta sola y recarga el portal cuando vuelve
        pagina.wait_for_function("!document.body.innerText.includes('no está corriendo')", timeout=30_000)
        assert pagina.locator("#pwaInstalar").count() == 1
    finally:
        contexto.close()


@pytest.mark.ui
@pytest.mark.timeout(120)
def test_instalar_app_solo_en_la_pc(navegador, portal):
    contexto, pagina = _abrir_controlada(navegador, portal.url + "/")
    try:
        assert pagina.locator("#pwaInstalar").is_hidden()
        # Chromium dispara beforeinstallprompt según su propia heurística: se simula el evento
        pagina.evaluate("""() => {
            const e = new Event('beforeinstallprompt', {cancelable: true});
            e.prompt = () => { window.__pedidoInstalar = true; return Promise.resolve(); };
            window.dispatchEvent(e);
        }""")
        boton = pagina.locator("#pwaInstalarBtn")
        assert boton.is_visible() and boton.inner_text().strip() == "Instalar app"
        boton.click()
        assert pagina.evaluate("window.__pedidoInstalar === true")
        assert pagina.locator("#pwaInstalar").is_hidden()
        ofrecer = "([h, i]) => window.LinkedOutApp.puedeOfrecer(h, i)"
        assert pagina.evaluate(ofrecer, ["localhost", False]) is True
        assert pagina.evaluate(ofrecer, ["192.168.1.50", False]) is False  # celular por la red
        assert pagina.evaluate(ofrecer, ["127.0.0.1", True]) is False  # ya abierta como app
    finally:
        contexto.close()
