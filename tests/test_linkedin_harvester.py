"""Tests del harvester de LinkedIn: sin navegador real, con un `page` falso inyectado."""
import importlib
import sys
import threading
from pathlib import Path
from unittest import mock

import pytest

from core.linkedin_harvester import FEED_URL, HarvestResult, LinkedInHarvester
from core.linkedin_parser import build_search_url

FIXTURE_HTML = (Path(__file__).parent / "fixtures" / "linkedin_search_posts.html").read_text(encoding="utf-8")
FEED_FIXTURE_HTML = (Path(__file__).parent / "fixtures" / "linkedin_feed_2026_11.html").read_text(encoding="utf-8")
SEARCH_URL = "https://www.linkedin.com/search/results/content/?keywords=x"


class FakeMouse:
    def __init__(self, page):
        self._page = page

    def move(self, x, y):
        self._page.mouse_pos = (x, y)

    def wheel(self, dx, dy):
        self._page.wheel_positions.append(self._page.mouse_pos)
        self._page.wheels.append((dx, dy))
        self._page.threads.add(threading.current_thread().name)
        self._page.scrolls += 1
        if self._page.checkpoint_after_scrolls is not None \
                and self._page.scrolls >= self._page.checkpoint_after_scrolls:
            self._page.url = "https://www.linkedin.com/checkpoint/challenge/abc"


class FakePage:
    closed = False

    def is_closed(self):
        return self.closed

    def __init__(self, url_after_goto=SEARCH_URL, html=FIXTURE_HTML, checkpoint_after_scrolls=None):
        self._url_after_goto = url_after_goto
        self.url = "about:blank"
        self.html = html
        self.goto_calls = []
        self.wait_until_calls = []
        self.scrolls = 0
        self.wheels = []
        self.mouse_pos = (0, 0)
        self.wheel_positions = []
        self.threads = set()
        self.checkpoint_after_scrolls = checkpoint_after_scrolls
        self.mouse = FakeMouse(self)
        self.screenshots = []
        self.brought_to_front = 0

    def goto(self, url, wait_until=None):
        self.threads.add(threading.current_thread().name)
        self.goto_calls.append(url)
        self.wait_until_calls.append(wait_until)
        self.url = self._url_after_goto

    def content(self):
        self.threads.add(threading.current_thread().name)
        return self.html

    def wait_for_timeout(self, ms):
        pass

    def evaluate(self, script):
        # Las pruebas heredadas simulan una lista que sigue creciendo.
        return 1000 + self.scrolls * 100

    def screenshot(self, path=None, **kwargs):
        self.screenshots.append(path)
        Path(path).write_bytes(b"png")

    def bring_to_front(self):
        self.brought_to_front += 1


class FakeContext:
    def __init__(self, page):
        self.pages = [page]
        self.closed = False
        self.handlers = {}
        self.new_pages = 0

    def on(self, evento, handler):
        self.handlers[evento] = handler

    def add_init_script(self, script):
        self.init_scripts = getattr(self, "init_scripts", []) + [script]

    def fire_close(self):
        self.handlers["close"](self)

    def new_page(self):
        self.new_pages += 1
        pagina = FakePage()
        self.pages.append(pagina)
        return pagina

    def close(self):
        self.closed = True


class BrokenPagesContext(FakeContext):
    """Contexto cuyo acceso a `pages` falla justo después del lanzamiento."""

    @property
    def pages(self):
        raise RuntimeError("fallo al leer pages")

    @pages.setter
    def pages(self, value):
        pass


class FakePW:
    def __init__(self, context_factory):
        self.stopped = False
        self.contexts = []
        self.chromium = self
        self._factory = context_factory

    def launch_persistent_context(self, **kwargs):
        ctx = self._factory()
        self.contexts.append(ctx)
        return ctx

    def stop(self):
        self.stopped = True


class FakeSyncApi:
    """Sustituye playwright.sync_api: cada sync_playwright().start() devuelve un FakePW nuevo."""

    def __init__(self, context_factory=None):
        self.pws = []
        self._factory = context_factory or (lambda: FakeContext(FakePage()))

    def sync_playwright(self):
        api = self

        class _Handle:
            def start(self_inner):
                pw = FakePW(api._factory)
                api.pws.append(pw)
                return pw

        return _Handle()


def _patch_playwright(api):
    modulo = mock.Mock(sync_playwright=api.sync_playwright)
    return mock.patch.dict(sys.modules, {"playwright": mock.Mock(), "playwright.sync_api": modulo})


def _harvester(page, **kwargs):
    h = LinkedInHarvester(sleep_fn=lambda s: None, **kwargs)
    h._page = page
    h._opened = True
    return h


@pytest.mark.asyncio
async def test_harvest_parses_posts_with_fake_page():
    h = _harvester(FakePage())
    try:
        r = await h.harvest("x", max_posts=30, max_scrolls=2)
        assert isinstance(r, HarvestResult)
        assert r.needs_login is False and len(r.posts) == 4
        assert h._page.goto_calls == [build_search_url("x")]
        assert h._page.wait_until_calls == ["domcontentloaded"]
        assert h._page.scrolls == 2
        assert all(1600 <= dy <= 2600 for _, dy in h._page.wheels)
        assert "data-urn" in r.html
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_harvest_detects_login_redirect():
    h = _harvester(FakePage(url_after_goto="https://www.linkedin.com/login?x", html=""))
    try:
        r = await h.harvest("x")
        assert r.needs_login is True and r.posts == []
        assert h._page.scrolls == 0
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_harvest_stops_scrolling_at_max_posts():
    h = _harvester(FakePage())
    try:
        r = await h.harvest("x", max_posts=3, max_scrolls=8)
        assert h._page.scrolls == 0
        assert len(r.posts) == 3
        assert r.needs_login is False
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_harvest_stops_at_checkpoint_while_scrolling():
    h = _harvester(FakePage(checkpoint_after_scrolls=1))
    try:
        r = await h.harvest("x", max_posts=30, max_scrolls=8)
        assert r.needs_login is True
        assert h._page.scrolls == 1
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_harvest_waits_between_scrolls_with_injected_sleep():
    esperas = []
    page = FakePage()
    h = LinkedInHarvester(sleep_fn=esperas.append)
    h._page, h._opened = page, True
    try:
        await h.harvest("x", max_posts=30, max_scrolls=3)
        assert page.scrolls == 3
        # Espera de 3 a 7 s con ±25 % de variación (core.linkedin_harvester._SCROLL_WAIT y _JITTER)
        assert esperas and all(2.0 <= s <= 8.75 for s in esperas)
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_harvest_feed_goes_to_feed_url_and_parses_fixture():
    h = _harvester(FakePage(url_after_goto=FEED_URL, html=FEED_FIXTURE_HTML))
    try:
        r = await h.harvest_feed(max_posts=40, max_scrolls=2)
        assert FEED_URL == "https://www.linkedin.com/feed/"
        assert h._page.goto_calls == [FEED_URL]
        assert h._page.wait_until_calls == ["domcontentloaded"]
        assert r.needs_login is False and len(r.posts) == 5
        assert h._page.scrolls == 2
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_scroll_wheel_happens_over_the_center_of_the_page():
    # En el feed de 2026 la lista scrollea dentro de <main>: una rueda en (0,0) cae sobre la barra
    # superior y no mueve nada (el feed quedaba en 8 posts)
    page = FakePage(url_after_goto=FEED_URL, html=FEED_FIXTURE_HTML)
    h = _harvester(page)
    try:
        await h.harvest_feed(max_posts=40, max_scrolls=3)
        assert len(page.wheel_positions) == 3
        assert all(400 <= x <= 880 and 250 <= y <= 650 for x, y in page.wheel_positions)
    finally:
        await h.close()


class _PaginaVirtualizada(FakePage):
    """Tras el primer scroll el DOM solo conserva las tarjetas nuevas (lista virtualizada)."""

    def content(self):
        super().content()
        return FIXTURE_HTML if self.scrolls == 0 else FEED_FIXTURE_HTML


@pytest.mark.asyncio
async def test_harvest_accumulates_posts_seen_across_scrolls():
    h = _harvester(_PaginaVirtualizada(url_after_goto=FEED_URL))
    try:
        r = await h.harvest_feed(max_posts=40, max_scrolls=2)
        assert len(r.posts) == 4 + 5
        assert len({p["urn"] for p in r.posts}) == 9
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_harvest_stops_without_scrolling_when_search_results_are_all_known():
    # Búsqueda ordenada por fecha: si lo primero que aparece ya se leyó, no hay nada nuevo debajo
    page = FakePage()
    h = _harvester(page)
    try:
        primero = await h.harvest("x", max_posts=30, max_scrolls=8)
        page.scrolls = 0
        r = await h.harvest("x", max_posts=30, max_scrolls=8,
                            conocidos={p["urn"] for p in primero.posts})
        assert page.scrolls == 0
        assert len(r.posts) == 4
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_harvest_feed_stops_after_two_reads_without_unknown_posts():
    page = FakePage(url_after_goto=FEED_URL, html=FEED_FIXTURE_HTML)
    h = _harvester(page)
    try:
        primero = await h.harvest_feed(max_posts=40, max_scrolls=1)
        page.scrolls = 0
        await h.harvest_feed(max_posts=40, max_scrolls=10,
                             conocidos={p["urn"] for p in primero.posts})
        assert page.scrolls == 1
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_harvest_keeps_scrolling_while_unknown_posts_appear():
    page = _PaginaVirtualizada(url_after_goto=FEED_URL)
    h = _harvester(page)
    try:
        r = await h.harvest_feed(max_posts=40, max_scrolls=10, conocidos=set())
        # 1ª lectura: 4 nuevos; 2ª: 5 nuevos; 3ª y 4ª sin novedades → corta
        assert page.scrolls == 3
        assert len(r.posts) == 9
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_harvest_feed_detects_login_redirect():
    h = _harvester(FakePage(url_after_goto="https://www.linkedin.com/login?x", html=""))
    try:
        r = await h.harvest_feed()
        assert r.needs_login is True and r.posts == []
        assert h._page.scrolls == 0
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_harvest_feed_stops_at_max_posts():
    h = _harvester(FakePage(url_after_goto=FEED_URL, html=FEED_FIXTURE_HTML))
    try:
        r = await h.harvest_feed(max_posts=2, max_scrolls=10)
        assert h._page.scrolls == 0
        assert len(r.posts) == 2
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_harvest_feed_waits_between_scrolls_and_stops_at_checkpoint():
    esperas = []
    page = FakePage(url_after_goto=FEED_URL, html=FEED_FIXTURE_HTML, checkpoint_after_scrolls=2)
    h = LinkedInHarvester(sleep_fn=esperas.append)
    h._page, h._opened = page, True
    try:
        r = await h.harvest_feed(max_posts=40, max_scrolls=10)
        assert r.needs_login is True and page.scrolls == 2
        # Espera de 3 a 7 s con ±25 % de variación (core.linkedin_harvester._SCROLL_WAIT y _JITTER)
        assert esperas and all(2.0 <= s <= 8.75 for s in esperas)
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_harvest_feed_runs_on_dedicated_thread():
    page = FakePage(url_after_goto=FEED_URL, html=FEED_FIXTURE_HTML)
    h = _harvester(page)
    try:
        await h.harvest_feed(max_posts=40, max_scrolls=1)
        assert len(page.threads) == 1
        assert next(iter(page.threads)).startswith("linkedin")
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_methods_run_on_single_dedicated_thread():
    page = FakePage()
    h = _harvester(page)
    try:
        await h.harvest("x", max_posts=30, max_scrolls=2)
        await h.harvest("y", max_posts=30, max_scrolls=1)
        assert len(page.threads) == 1
        nombre = next(iter(page.threads))
        assert nombre.startswith("linkedin")
        assert nombre != threading.current_thread().name
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_check_logged_in_does_not_navigate_when_on_login_page():
    page = FakePage()
    page.url = "https://www.linkedin.com/login?session_redirect=x"
    h = _harvester(page)
    try:
        assert await h.check_logged_in() is False
        page.url = "https://www.linkedin.com/checkpoint/challenge/abc"
        assert await h.check_logged_in() is False
        assert page.goto_calls == []
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_check_logged_in_does_not_navigate_when_already_authenticated():
    for url in ("https://www.linkedin.com/feed/", SEARCH_URL, "https://www.linkedin.com/in/alguien/"):
        page = FakePage()
        page.url = url
        h = _harvester(page)
        try:
            assert await h.check_logged_in() is True
            assert page.goto_calls == []
        finally:
            await h.close()


@pytest.mark.asyncio
async def test_check_logged_in_navigates_to_feed_from_neutral_page():
    page = FakePage(url_after_goto="https://www.linkedin.com/feed/")
    h = _harvester(page)
    try:
        assert page.url == "about:blank"
        assert await h.check_logged_in() is True
        assert page.goto_calls == ["https://www.linkedin.com/feed/"]
    finally:
        await h.close()
    for destino in ("https://www.linkedin.com/checkpoint/lg/login-submit", "https://www.linkedin.com/login",
                    "https://www.linkedin.com/"):
        page = FakePage(url_after_goto=destino)
        h = _harvester(page)
        try:
            assert await h.check_logged_in() is False
            assert page.goto_calls == ["https://www.linkedin.com/feed/"]
        finally:
            await h.close()


@pytest.mark.asyncio
async def test_dump_debug_writes_screenshot_and_html(tmp_path):
    h = _harvester(FakePage(html="<html>debug</html>"))
    try:
        destino = tmp_path / "radar_debug" / "ts"
        await h.dump_debug(destino)
        assert (destino / "screenshot.png").exists()
        assert (destino / "page.html").read_text(encoding="utf-8") == "<html>debug</html>"
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_close_is_idempotent_and_safe_without_open():
    h = LinkedInHarvester(sleep_fn=lambda s: None)
    await h.close()
    await h.close()
    assert h._opened is False


@pytest.mark.asyncio
async def test_close_shuts_executor_and_executor_is_recreated_later():
    page = FakePage()
    h = _harvester(page)
    await h.harvest("x", max_posts=1)
    primero = h._executor
    assert primero is not None
    await h.close()
    await h.close()
    assert h._executor is None and h._opened is False and h._page is None
    # Un nuevo uso (el loop puede detenerse y arrancar de nuevo) recrea el executor
    h._page, h._opened = FakePage(), True
    r = await h.harvest("x", max_posts=1)
    assert len(r.posts) == 1
    assert h._executor is not None and h._executor is not primero
    await h.close()


@pytest.mark.asyncio
async def test_open_is_idempotent_when_already_opened():
    h = _harvester(FakePage())
    try:
        with mock.patch.object(LinkedInHarvester, "_open_sync") as abrir:
            await h.open()
        abrir.assert_not_called()
    finally:
        await h.close()


def test_default_profile_dir_is_data_linkedin_profile():
    h = LinkedInHarvester()
    assert h._profile_dir.parts[-2:] == ("data", "linkedin_profile")


def test_module_import_does_not_require_playwright():
    # En un proceso aparte: reimportar el módulo acá dejaba otro objeto en sys.modules y los tests que
    # parchean "core.linkedin_harvester.<algo>" tocaban una copia distinta (fallaban según el orden).
    import subprocess
    codigo = (
        "import sys; sys.modules['playwright'] = None; sys.modules['playwright.sync_api'] = None; "
        "import core.linkedin_harvester"
    )
    raiz = Path(__file__).resolve().parent.parent
    r = subprocess.run([sys.executable, "-c", codigo], cwd=raiz, capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr


@pytest.mark.asyncio
async def test_open_relaunches_after_context_close_event(tmp_path):
    api = FakeSyncApi()
    h = LinkedInHarvester(profile_dir=tmp_path / "perfil", sleep_fn=lambda s: None)
    with _patch_playwright(api):
        try:
            await h.open()
            await h.open()  # idempotente mientras siga abierto
            assert len(api.pws) == 1
            primero = api.pws[0]
            primero.contexts[0].fire_close()  # el usuario cerro Edge
            assert h._opened is False
            await h.open()
            assert len(api.pws) == 2
            assert primero.stopped is True  # el pw viejo no se filtra
            assert h._opened is True and h._context is api.pws[1].contexts[0]
        finally:
            await h.close()
    assert api.pws[1].stopped is True and api.pws[1].contexts[0].closed is True


@pytest.mark.asyncio
async def test_target_closed_error_heals_and_next_call_relaunches(tmp_path):
    api = FakeSyncApi()
    h = LinkedInHarvester(profile_dir=tmp_path / "perfil", sleep_fn=lambda s: None)
    with _patch_playwright(api):
        try:
            await h.open()

            def roto(url, wait_until=None):
                raise RuntimeError("Target page, context or browser has been closed")

            h._page.goto = roto
            with pytest.raises(RuntimeError):
                await h.harvest("x")
            assert api.pws[0].stopped is True
            assert h._opened is False and h._context is None
            r = await h.harvest("x", max_posts=1)  # relanza solo
            assert len(api.pws) == 2 and isinstance(r, HarvestResult)
        finally:
            await h.close()


@pytest.mark.asyncio
async def test_unrelated_error_does_not_close_browser(tmp_path):
    api = FakeSyncApi()
    h = LinkedInHarvester(profile_dir=tmp_path / "perfil", sleep_fn=lambda s: None)
    with _patch_playwright(api):
        try:
            await h.open()

            def lento(url, wait_until=None):
                raise TimeoutError("Timeout 30000ms exceeded")

            h._page.goto = lento
            with pytest.raises(TimeoutError):
                await h.harvest("x")
            assert h._opened is True and api.pws[0].stopped is False
        finally:
            await h.close()


@pytest.mark.asyncio
async def test_page_is_resolved_from_context_when_cached_page_closed(tmp_path):
    api = FakeSyncApi()
    h = LinkedInHarvester(profile_dir=tmp_path / "perfil", sleep_fn=lambda s: None)
    with _patch_playwright(api):
        try:
            await h.open()
            ctx = api.pws[0].contexts[0]
            viejo = h._page
            viejo.closed = True
            ctx.pages = []  # el usuario cerro todas las pestanas
            await h.harvest("x", max_posts=1)
            assert ctx.new_pages == 1 and h._page is not viejo
            assert h._page.goto_calls == [build_search_url("x")]
        finally:
            await h.close()


@pytest.mark.asyncio
async def test_open_failure_after_launch_does_not_leak_context(tmp_path):
    api = FakeSyncApi(lambda: BrokenPagesContext(FakePage()))
    h = LinkedInHarvester(profile_dir=tmp_path / "perfil", sleep_fn=lambda s: None)
    with _patch_playwright(api):
        try:
            with pytest.raises(RuntimeError):
                await h.open()
            assert api.pws[0].stopped is True
            assert api.pws[0].contexts[0].closed is True
            assert h._opened is False and h._pw is None and h._context is None
        finally:
            await h.close()


# ------------------------------------------------- link a la publicación por menú

@pytest.mark.asyncio
async def test_feed_asks_menu_link_only_for_new_possible_offers_without_url(monkeypatch):
    page = FakePage(url_after_goto=FEED_URL, html=FEED_FIXTURE_HTML)
    h = _harvester(page)
    pedidos = []
    monkeypatch.setattr(h, "_link_por_menu", lambda pg, urn: pedidos.append(urn) or f"https://www.linkedin.com/feed/update/{urn}/")
    try:
        r = await h.harvest_feed(max_posts=40, max_scrolls=0, conocidos={"urn:li:card:CCCC3333"})
        por_urn = {p["urn"]: p for p in r.posts}
        # AAAA1111 es una búsqueda nueva; CCCC3333 ya era conocida; BBBB2222 no parece búsqueda
        assert "urn:li:card:AAAA1111" in pedidos
        assert "urn:li:card:CCCC3333" not in pedidos and "urn:li:card:BBBB2222" not in pedidos
        assert por_urn["urn:li:card:AAAA1111"]["post_url"].endswith("urn:li:card:AAAA1111/")
        assert por_urn["urn:li:card:BBBB2222"]["post_url"] == ""
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_menu_links_are_capped_per_read(monkeypatch):
    # Por los globals de la clase: otro test recarga el módulo
    monkeypatch.setitem(LinkedInHarvester._scroll_and_parse.__globals__, "MAX_LINKS_POR_MENU", 0)
    h = _harvester(FakePage(url_after_goto=FEED_URL, html=FEED_FIXTURE_HTML))
    pedidos = []
    monkeypatch.setattr(h, "_link_por_menu", lambda pg, urn: pedidos.append(urn) or "")
    try:
        await h.harvest_feed(max_posts=40, max_scrolls=0, conocidos=set())
        assert pedidos == []
    finally:
        await h.close()


class _Resp:
    def __init__(self, location):
        self.headers = {"location": location} if location else {}


class _Req:
    def __init__(self, location=None, error=None):
        self.location, self.error, self.calls = location, error, []

    def get(self, url, **kw):
        self.calls.append((url, kw))
        if self.error:
            raise self.error
        return _Resp(self.location)


def test_short_link_resolves_to_linkedin_url_without_tracking_query():
    h = LinkedInHarvester(sleep_fn=lambda s: None)
    h._context = mock.Mock(request=_Req(
        "https://www.linkedin.com/posts/acme_busqueda-share-7000000000000000001-_yaH/?utm_source=share&rcm=ACoAAX"))
    assert h._resolver_link("https://lnkd.in/p/dFfHGXHA") ==         "https://www.linkedin.com/posts/acme_busqueda-share-7000000000000000001-_yaH/"
    assert h._context.request.calls[0][1]["max_redirects"] == 0


@pytest.mark.parametrize("req", [_Req(error=RuntimeError("red")), _Req(None), _Req("https://evil.example/x")])
def test_short_link_is_kept_when_it_cannot_be_resolved_to_linkedin(req):
    h = LinkedInHarvester(sleep_fn=lambda s: None)
    h._context = mock.Mock(request=req)
    assert h._resolver_link("https://lnkd.in/p/dFfHGXHA?x=1") == "https://lnkd.in/p/dFfHGXHA"


def test_menu_link_is_empty_for_non_card_urns():
    h = LinkedInHarvester(sleep_fn=lambda s: None)
    assert h._link_por_menu(object(), "urn:li:activity:1") == ""
