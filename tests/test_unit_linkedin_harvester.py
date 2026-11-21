"""Unitarios de `core/linkedin_harvester.py` con dobles de la página y el contexto de Playwright:
nunca se abre Edge ni se entra a LinkedIn (el módulo `playwright` también se reemplaza)."""
import sys
from unittest import mock

import pytest

from core import linkedin_harvester as lh
from core.linkedin_harvester import LinkedInHarvester

URL_POST = "https://www.linkedin.com/posts/acme_busqueda-activity-123"


def _harvester(**kwargs):
    return LinkedInHarvester(sleep_fn=lambda s: None, **kwargs)


# --------------------------------------------------------------------------- funciones puras

def test_url_mal_formada_no_es_de_sesion():
    """Una URL que `urlparse` no puede leer no cuenta como página con sesión."""
    assert lh._is_authenticated_linkedin_url("https://[::1/feed") is False


def test_error_target_closed_por_nombre_de_clase():
    """Una excepción cuya clase se llama TargetClosedError indica navegador cerrado."""
    TargetClosedError = type("TargetClosedError", (Exception,), {})
    assert lh._is_target_closed(TargetClosedError("x")) is True
    assert lh._is_target_closed(ValueError("otra cosa")) is False


def test_contenido_seguro_ante_pagina_rota():
    """Si la página no entrega el HTML, `_safe_content` devuelve cadena vacía."""
    class Rota:
        def content(self):
            raise RuntimeError("cerrada")

    assert LinkedInHarvester._safe_content(Rota()) == ""


# --------------------------------------------------------------------------- apertura y cierre

def test_open_sync_no_relanza_si_ya_esta_abierto():
    """Con el harvester ya abierto, `_open_sync` no importa Playwright ni lanza nada."""
    h = _harvester()
    h._opened = True
    with mock.patch.dict(sys.modules, {"playwright.sync_api": None}):
        h._open_sync()  # importar playwright fallaría: no llega a hacerlo
    assert h._opened is True


def test_open_sync_fallido_libera_contexto_y_playwright(tmp_path):
    """Si falla la preparación del contexto, se intenta cerrarlo y detener Playwright (aunque eso
    también falle) y se propaga el error original; el harvester queda cerrado."""
    eventos = []

    class Contexto:
        pages = []

        def add_init_script(self, script):
            raise RuntimeError("init script")

        def close(self):
            eventos.append("context.close")
            raise RuntimeError("ya cerrado")

    class Playwright:
        class chromium:
            @staticmethod
            def launch_persistent_context(**kwargs):
                eventos.append(("launch", kwargs["channel"]))
                return Contexto()

        def stop(self):
            eventos.append("pw.stop")
            raise RuntimeError("ya detenido")

    api = mock.Mock()
    api.sync_playwright.return_value.start.return_value = Playwright()
    with mock.patch.dict(sys.modules, {"playwright": mock.Mock(), "playwright.sync_api": api}):
        h = _harvester(profile_dir=tmp_path / "perfil")
        with pytest.raises(RuntimeError, match="init script"):
            h._open_sync()
    assert eventos == [("launch", "msedge"), "context.close", "pw.stop"]
    assert h._opened is False and h._context is None and h._pw is None


def test_close_sync_tolera_errores_al_cerrar():
    """Cerrar con contexto y Playwright que fallan deja el estado limpio igual."""
    h = _harvester()
    h._context = mock.Mock(close=mock.Mock(side_effect=RuntimeError("cerrado por el usuario")))
    pw = mock.Mock(stop=mock.Mock(side_effect=RuntimeError("detenido")))
    h._pw = pw
    h._opened = True
    h._close_sync()
    pw.stop.assert_called_once()
    assert (h._context, h._pw, h._page, h._opened) == (None, None, None, False)


# --------------------------------------------------------------------------- API async

class _Pagina:
    def __init__(self, html="", url=URL_POST):
        self.html = html
        self.url = url
        self.goto_urls = []
        self.al_frente = False

    def is_closed(self):
        return False

    def goto(self, url, wait_until=None):
        self.goto_urls.append(url)

    def content(self):
        return self.html

    def bring_to_front(self):
        self.al_frente = True


@pytest.mark.asyncio
async def test_recapturar_post_abre_la_publicacion_y_devuelve_none_si_no_hay_posts():
    """Con una URL de LinkedIn se navega a la publicación; sin posts en el HTML devuelve None."""
    h = _harvester()
    h._opened = True
    h._page = _Pagina(html="<html><body>nada</body></html>")
    try:
        assert await h.recapturar_post(URL_POST) is None
        assert h._page.goto_urls == [URL_POST]
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_show_restaura_la_ventana_por_cdp_y_la_trae_al_frente():
    """`show` pide la ventana por CDP, la pone en estado normal, se desconecta y la trae al frente."""
    sesion = mock.Mock()
    sesion.send.side_effect = lambda metodo, *a: {"windowId": 7} if metodo == "Browser.getWindowForTarget" else None
    h = _harvester()
    h._opened = True
    pagina = _Pagina()
    h._page = pagina
    h._context = mock.Mock(new_cdp_session=mock.Mock(return_value=sesion))
    h._context.pages = [pagina]
    try:
        await h.show()
    finally:
        h._context = None
        await h.close()
    sesion.send.assert_any_call("Browser.setWindowBounds", {"windowId": 7, "bounds": {"windowState": "normal"}})
    sesion.detach.assert_called_once()
    assert pagina.al_frente is True


def test_show_sync_sin_cdp_igual_trae_la_pestana_al_frente():
    """Si CDP falla, igual se intenta traer la pestaña al frente."""
    h = _harvester()
    pagina = _Pagina()
    h._page = pagina
    h._context = mock.Mock(new_cdp_session=mock.Mock(side_effect=RuntimeError("sin CDP")))
    h._show_sync()
    assert pagina.al_frente is True


def test_lectura_que_falla_al_primer_content_se_propaga():
    """Un error de `content()` que no es «page is navigating» se propaga al llamador."""
    class Rota(_Pagina):
        def content(self):
            raise RuntimeError("Target crashed")

    h = _harvester()
    h._page = Rota(url="https://www.linkedin.com/search/results/content/")
    with pytest.raises(RuntimeError, match="Target crashed"):
        h._scroll_and_parse("https://www.linkedin.com/search/results/content/", 10, 2)


# --------------------------------------------------------------------------- link por menú

class _Locator:
    def __init__(self, page, nombre):
        self.page = page
        self.nombre = nombre

    @property
    def first(self):
        return self

    def locator(self, selector):
        return self.page.boton

    def filter(self, has_text=None):
        return self.page.item

    def count(self):
        return self.page.botones

    def click(self, timeout=None):
        self.page.eventos.append(f"click:{self.nombre}")

    def wait_for(self, state=None, timeout=None):
        self.page.eventos.append("wait_for")
        if self.page.fallas_menu:
            self.page.fallas_menu -= 1
            raise TimeoutError("menú cerrado")


class _PaginaMenu:
    def __init__(self, copiado="https://lnkd.in/abc", botones=1, fallas_menu=0):
        self.copiado = copiado
        self.botones = botones
        self.fallas_menu = fallas_menu
        self.eventos = []
        self.tarjeta = _Locator(self, "tarjeta")
        self.boton = _Locator(self, "boton")
        self.item = _Locator(self, "item")
        self.keyboard = mock.Mock(press=lambda tecla: self.eventos.append(f"tecla:{tecla}"))

    def locator(self, selector):
        return self.item if "menuitem" in selector else self.tarjeta

    def evaluate(self, script):
        return None if "= null" in script else self.copiado


def _harvester_con_redireccion(location):
    h = _harvester()
    respuesta = mock.Mock(headers={"location": location} if location else {})
    h._context = mock.Mock()
    h._context.request.get.return_value = respuesta
    return h


def test_link_por_menu_resuelve_el_link_corto_a_linkedin_sin_query():
    """Al reintentar el menú que no abrió, se copia el link corto y se resuelve a la URL de LinkedIn
    sin parámetros de rastreo."""
    h = _harvester_con_redireccion("https://www.linkedin.com/posts/acme_123?rcm=XYZ&utm=1")
    page = _PaginaMenu(fallas_menu=1)
    assert h._link_por_menu(page, "urn:li:card:abc") == "https://www.linkedin.com/posts/acme_123"
    assert page.eventos.count("click:boton") == 2
    assert page.eventos[-1] == "tecla:Escape"
    h._context.request.get.assert_called_once_with("https://lnkd.in/abc", max_redirects=0, timeout=10000)


def test_link_por_menu_redireccion_externa_conserva_el_corto():
    """Si el link corto no redirige a LinkedIn, se devuelve el corto sin query."""
    h = _harvester_con_redireccion("https://otro.example/x")
    assert h._link_por_menu(_PaginaMenu(copiado="https://lnkd.in/abc?x=1"), "urn:li:card:abc") == "https://lnkd.in/abc"


def test_link_por_menu_sin_boton_devuelve_vacio():
    """Una tarjeta sin botón de menú no da link."""
    assert _harvester()._link_por_menu(_PaginaMenu(botones=0), "urn:li:card:abc") == ""


def test_link_por_menu_que_no_abre_dos_veces_desiste():
    """Si el menú no abre en dos intentos se cierra con Escape y no hay link."""
    page = _PaginaMenu(fallas_menu=2)
    assert _harvester()._link_por_menu(page, "urn:li:card:abc") == ""
    assert page.eventos.count("tecla:Escape") == 2
    assert "click:item" not in page.eventos


def test_link_por_menu_copia_que_no_es_url_se_descarta():
    """Si lo copiado no es una URL https, no se usa."""
    assert _harvester()._link_por_menu(_PaginaMenu(copiado="texto suelto"), "urn:li:card:abc") == ""
