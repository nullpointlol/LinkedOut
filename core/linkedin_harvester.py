"""Harvester de LinkedIn con Playwright (API sync) en un hilo dedicado.

Toda la interacción con Playwright ocurre en un `ThreadPoolExecutor(max_workers=1)` propio:
así se evita el requisito de event loop Proactor en Windows y el resto del sistema (y los
tests) no necesitan `playwright` instalado. El import de `playwright` es local al hilo.

NUNCA automatiza login, checkpoints ni CAPTCHAs: solo los detecta y devuelve `needs_login`.
"""
import asyncio
import logging
import random
import re
import unicodedata
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import AbstractSet, Any, Callable, Dict, List, Optional
from urllib.parse import urlparse, urlunparse

from core.linkedin_parser import (
    RawPost,
    build_search_url,
    es_posible_busqueda,
    is_login_or_checkpoint_url,
    parse_posts_html,
)
from core.paths import DATA_DIR
from bs4 import BeautifulSoup

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_PROFILE_DIR = DATA_DIR / "linkedin_profile"

FEED_URL = "https://www.linkedin.com/feed/"
_SCROLL_MIN_PX = 1600
_SCROLL_MAX_PX = 2600
_SCROLL_WAIT = (3.0, 7.0)      # segundos entre scrolls
_JITTER = 0.25
_INITIAL_WAIT = (2.0, 4.0)     # segundos tras cargar la búsqueda (LinkedIn es SPA)
# La rueda se gira con el mouse sobre la lista (viewport 1280x900): en el feed de 2026 la lista
# scrollea dentro de <main> y en (0,0) el mouse queda sobre la barra superior, que no scrollea
_MOUSE_X = (480, 800)
_MOUSE_Y = (300, 600)
# Con `conocidos`, lecturas seguidas sin posts nuevos antes de cortar: la búsqueda va ordenada
# por fecha (si arriba ya está todo leído no hay nada nuevo debajo); el feed no, se le da otra
_PACIENCIA_BUSQUEDA = 1
_PACIENCIA_FEED = 2

# Link a la publicación de los posts que no lo traen en el DOM: menú "…" → "Copiar enlace a la
# publicación". Solo para posts nuevos que parecen búsquedas, con tope por lectura. El script
# intercepta la copia (queda en `window.__radarCopiado`): el portapapeles del usuario no se toca.
MAX_LINKS_POR_MENU = 25
_ESPERA_MENU = (0.6, 1.3)
_SEL_BOTON_MENU = ('button[aria-label^="Abrir el menú de controles"], '
                   'button[aria-label^="Open control menu"]')
_RE_ITEM_COPIAR = re.compile(r"Copiar enlace|Copy link", re.IGNORECASE)
_PREFIJO_URN_TARJETA = "urn:li:card:"
_SCRIPT_COPIA = """
(() => {
  window.__radarCopiado = null;
  try {
    const cb = navigator.clipboard;
    if (cb) {
      cb.writeText = async (t) => { window.__radarCopiado = String(t); };
      cb.write = async (items) => {
        try {
          for (const it of items) {
            if (it.types.includes('text/plain')) {
              window.__radarCopiado = await (await it.getType('text/plain')).text();
            }
          }
        } catch (e) {}
      };
    }
  } catch (e) {}
  document.addEventListener('copy', (e) => {
    const s = String(document.getSelection() || '');
    if (s) window.__radarCopiado = s;
    e.preventDefault();
  }, true);
})();
"""


def _sin_query(url: str) -> str:
    """Sin query ni fragmento: el link de "copiar" trae `rcm` (identifica al usuario) y utm."""
    partes = urlparse(url)
    return urlunparse(partes._replace(query="", fragment=""))


_AUTH_PATH_PREFIXES = ("/feed", "/search", "/in/", "/mynetwork", "/jobs", "/messaging", "/notifications")


def _is_authenticated_linkedin_url(url: str) -> bool:
    """True si la URL es una página interna de LinkedIn (solo accesible con sesión iniciada)."""
    try:
        partes = urlparse(url)
    except ValueError:
        return False
    host = (partes.hostname or "").lower()
    if host != "linkedin.com" and not host.endswith(".linkedin.com"):
        return False
    return (partes.path or "").lower().startswith(_AUTH_PATH_PREFIXES)


def _is_target_closed(exc: BaseException) -> bool:
    """True si el error indica que la pestaña, el contexto o el navegador se cerraron."""
    if any(c.__name__ == "TargetClosedError" for c in type(exc).__mro__):
        return True
    msg = str(exc).lower()
    return "has been closed" in msg or "target closed" in msg


@dataclass
class HarvestResult:
    posts: List[RawPost] = field(default_factory=list)
    needs_login: bool = False
    html: str = ""  # último HTML leído, para depuración
    lectura: dict = field(default_factory=dict)


logger = logging.getLogger(__name__)

# Segundos que se espera a que Edge cierre (12/11): un Edge colgado no traba el stop ni el apagado
PLAZO_CIERRE_NAVEGADOR_S = 20


class LinkedInHarvester:
    def __init__(
        self,
        profile_dir: Path = DEFAULT_PROFILE_DIR,
        rng: Optional[random.Random] = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ):
        self._profile_dir = Path(profile_dir)
        self._rng = rng or random.Random()
        self._sleep = sleep_fn
        self._executor: Optional[ThreadPoolExecutor] = None
        # Estado que solo se toca desde el hilo del executor
        self._pw: Any = None
        self._context: Any = None
        self._page: Any = None
        self._opened = False

    # ------------------------------------------------------------------
    # Infraestructura: delegación al hilo dedicado
    # ------------------------------------------------------------------

    def _get_executor(self) -> ThreadPoolExecutor:
        if self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="linkedin")
        return self._executor

    async def _run(self, fn: Callable[..., Any], *args: Any) -> Any:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._get_executor(), self._guarded, fn, *args)

    def _guarded(self, fn: Callable[..., Any], *args: Any) -> Any:
        """Ejecuta `fn` en el hilo; si el navegador/pestaña se cerró, limpia el estado y re-lanza.

        Así la siguiente llamada a `open()` vuelve a lanzar Edge en vez de fallar para siempre.
        """
        try:
            return fn(*args)
        except Exception as exc:
            if _is_target_closed(exc):
                self._close_sync()
            raise

    # ------------------------------------------------------------------
    # API async
    # ------------------------------------------------------------------

    async def open(self) -> None:
        """Abre el perfil persistente de Edge. Idempotente."""
        if self._opened:
            return
        await self._run(self._open_sync)

    async def harvest(
        self, query: str, max_posts: int = 30, max_scrolls: int = 8,
        conocidos: Optional[AbstractSet[str]] = None, paciencia: int = _PACIENCIA_BUSQUEDA,
        orden: str = "date_posted",
    ) -> HarvestResult:
        """Busca posts de `query`. Cualquier error de Playwright se propaga al llamador.

        Con `conocidos` (urns ya leídos) deja de scrollear tras `paciencia` lecturas seguidas sin
        nada nuevo."""
        await self.open()
        return await self._run(
            self._scroll_and_parse, build_search_url(query, orden=orden), max_posts, max_scrolls,
            conocidos, paciencia,
        )

    async def harvest_feed(
        self, max_posts: int = 40, max_scrolls: int = 10,
        conocidos: Optional[AbstractSet[str]] = None,
    ) -> HarvestResult:
        """Lee el feed de inicio del usuario (mismo ritmo y mismas reglas que `harvest`)."""
        await self.open()
        return await self._run(
            self._scroll_and_parse, FEED_URL, max_posts, max_scrolls, conocidos, _PACIENCIA_FEED
        )

    async def recapturar_post(self, post_url: str) -> Optional[RawPost]:
        """Vuelve a abrir una publicación (rescate de capturas incompletas) y devuelve el post con
        el texto completo, sus imágenes y links; None si no se pudo leer o pide login. Corre en el
        mismo hilo que la cosecha: nunca usan la pestaña a la vez."""
        if not post_url.startswith("https://www.linkedin.com/"):
            return None
        await self.open()
        return await self._run(self._recapturar_sync, post_url)

    async def check_logged_in(self) -> bool:
        """Navega al feed y verifica que la URL resultante no sea de login/checkpoint."""
        await self.open()
        return await self._run(self._check_logged_in_sync)

    async def show(self) -> None:
        """Restaura y trae al frente la ventana (para que el usuario inicie sesión)."""
        await self.open()
        await self._run(self._show_sync)

    async def dump_debug(self, target_dir: Path) -> None:
        """Guarda `screenshot.png` y `page.html` de la página actual."""
        await self.open()
        await self._run(self._dump_debug_sync, Path(target_dir))

    async def close(self) -> None:
        """Cierra el navegador y apaga el executor. Idempotente; el executor se recrea al reusar."""
        executor = self._executor
        if executor is None:
            return
        self._executor = None
        try:
            loop = asyncio.get_running_loop()
            cierre = loop.run_in_executor(executor, self._close_sync)
            try:
                # Shield: un stop que cancela esta espera no corta el cierre de Edge a mitad
                await asyncio.wait_for(asyncio.shield(cierre), PLAZO_CIERRE_NAVEGADOR_S)
            except asyncio.TimeoutError:
                logger.error(f"[Radar] Edge no se cerró en {PLAZO_CIERRE_NAVEGADOR_S} s: se sigue sin esperarlo "
                             "(el hilo del navegador lo termina de cerrar cuando responda)")
        finally:
            # Nunca se espera al hilo de Playwright adentro del event loop (12/11: el portal quedó 6 min
            # frenado con la CPU en 0). El hilo termina solo cuando vuelve el cierre.
            executor.shutdown(wait=False)

    # ------------------------------------------------------------------
    # Implementación sync (se ejecuta SIEMPRE en el hilo "linkedin")
    # ------------------------------------------------------------------

    def _open_sync(self) -> None:
        if self._opened:
            return
        if self._pw is not None or self._context is not None:
            # Restos de una sesión muerta (el usuario cerró Edge): liberar antes de relanzar
            self._close_sync()
        from playwright.sync_api import sync_playwright  # import local: solo en este hilo

        self._profile_dir.mkdir(parents=True, exist_ok=True)
        pw = context = None
        try:
            pw = sync_playwright().start()
            context = pw.chromium.launch_persistent_context(
                user_data_dir=str(self._profile_dir),
                channel="msedge",
                headless=False,
                # Sin esto Playwright desactiva el sandbox y Edge muestra el aviso amarillo de flag no soportado.
                chromium_sandbox=True,
                args=["--start-minimized"],
                viewport={"width": 1280, "height": 900},
            )
            context.add_init_script(_SCRIPT_COPIA)
            context.on("close", lambda *_a, ctx=context: self._on_context_closed(ctx))
            page = context.pages[0] if context.pages else context.new_page()
        except Exception:
            if context is not None:
                try:
                    context.close()
                except Exception:
                    pass
            if pw is not None:
                try:
                    pw.stop()
                except Exception:
                    pass
            raise
        self._pw = pw
        self._context = context
        self._page = page
        self._opened = True

    def _on_context_closed(self, ctx: Any) -> None:
        """El usuario cerró Edge: marca el harvester como no abierto para que open() relance."""
        if ctx is self._context:
            self._opened = False

    def _current_page(self) -> Any:
        """Pestaña viva del contexto (crea una si el usuario cerró todas)."""
        if self._context is None:
            return self._page
        page = self._page
        if page is not None and not page.is_closed():
            return page
        pages = [p for p in self._context.pages if not p.is_closed()]
        self._page = pages[0] if pages else self._context.new_page()
        return self._page

    def _scroll_and_parse(
        self, url: str, max_posts: int, max_scrolls: int,
        conocidos: Optional[AbstractSet[str]] = None, paciencia: int = 1,
    ) -> HarvestResult:
        """Bucle común de búsqueda y feed: navega a `url`, detecta login/checkpoint, scrollea y parsea.

        Los posts se acumulan por urn entre lecturas (la lista puede descartar tarjetas ya pasadas).
        Con `conocidos`, corta tras `paciencia` lecturas seguidas sin ningún urn desconocido."""
        page = self._current_page()
        lectura = {"url": url, "pantallas": [], "scrolls": 0, "motivo_fin": ""}

        def terminar(motivo, posts, html):
            lectura.update(scrolls=scrolls, motivo_fin=motivo)
            # Conserva también los URN que el máximo de retorno recorta, solo para medir.
            lectura["urns_parseados"] = [p["urn"] for p in posts]
            return HarvestResult(posts=posts[:max_posts], needs_login=motivo == "login",
                                 html=html, lectura=lectura)

        scrolls = 0
        page.goto(url, wait_until="domcontentloaded")
        if is_login_or_checkpoint_url(page.url):
            return terminar("login", [], self._safe_content(page))

        self._sleep(self._rng.uniform(*_INITIAL_WAIT))
        acumulados: Dict[str, RawPost] = {}
        sin_novedad = 0
        links_por_menu = 0
        sin_crecer = 0
        while True:
            try:
                html = page.content()
            except Exception as exc:
                if scrolls != 0 or "page is navigating" not in str(exc).lower():
                    raise
                page.wait_for_load_state("domcontentloaded")
                html = page.content()
            alto = int(page.evaluate("document.scrollingElement.scrollHeight") or 0)
            parseados = parse_posts_html(html)
            cantidad_antes = len(acumulados)
            hay_desconocidos = False
            for post in parseados:
                if post["urn"] in acumulados:
                    continue
                acumulados[post["urn"]] = post
                nuevo = conocidos is None or post["urn"] not in conocidos
                if conocidos is not None and nuevo:
                    hay_desconocidos = True
                if (nuevo and not post.get("post_url") and links_por_menu < MAX_LINKS_POR_MENU
                        and es_posible_busqueda(post["texto"])):
                    links_por_menu += 1
                    post["post_url"] = self._link_por_menu(page, post["urn"])
            posts = list(acumulados.values())
            nuevos = len(acumulados) - cantidad_antes
            if scrolls:
                anterior = lectura["pantallas"][-1]
                anterior["alto_despues"] = alto
                sin_crecer = sin_crecer + 1 if alto <= anterior["alto_antes"] and not nuevos else 0
            soup = BeautifulSoup(html, "html.parser")
            tarjetas = len(soup.select('[role="listitem"][componentkey^="update-card"]'))
            tarjetas += len(soup.select('[data-urn^="urn:li:activity:"]'))
            lectura["pantallas"].append(dict(tarjetas_dom=tarjetas, parseados=len(parseados),
                                            nuevos=nuevos, alto_antes=alto, alto_despues=alto))
            normalizado = "".join(c for c in unicodedata.normalize("NFD", soup.get_text(" ").lower())
                                  if not unicodedata.combining(c))
            if "trafico de busqueda inusual" in normalizado or "unusual search traffic" in normalizado:
                return terminar("bloqueo", posts, html)
            if "no se han encontrado resultados" in normalizado or "no results found" in normalizado:
                return terminar("sin_resultados", posts, html)
            sin_novedad = 0 if hay_desconocidos else sin_novedad + 1
            agotado = conocidos is not None and sin_novedad >= paciencia
            motivo = ("max_posts" if len(posts) >= max_posts else
                      "no_crece" if sin_crecer >= 2 else
                      "max_scrolls" if scrolls >= max_scrolls else
                      "paciencia" if agotado else "")
            if motivo:
                return terminar(motivo, posts, html)
            page.mouse.move(self._rng.randint(*_MOUSE_X), self._rng.randint(*_MOUSE_Y))
            page.mouse.wheel(0, self._rng.randint(_SCROLL_MIN_PX, _SCROLL_MAX_PX))
            scrolls += 1
            self._sleep(self._rng.uniform(*_SCROLL_WAIT) * self._rng.uniform(1 - _JITTER, 1 + _JITTER))
            if is_login_or_checkpoint_url(page.url):
                # Checkpoint a mitad de lectura: se conservan los posts ya leídos
                lectura["pantallas"][-1]["alto_despues"] = int(
                    page.evaluate("document.scrollingElement.scrollHeight") or 0)
                return terminar("login", posts, html)

    def _recapturar_sync(self, post_url: str) -> Optional[RawPost]:
        page = self._current_page()
        page.goto(post_url, wait_until="domcontentloaded")
        if is_login_or_checkpoint_url(page.url):
            return None
        self._sleep(self._rng.uniform(*_INITIAL_WAIT))
        posts = parse_posts_html(page.content())
        if not posts:
            return None
        # En la página de una publicación el post principal es el de más contenido
        return max(posts, key=lambda p: (len(p.get("texto") or ""), len(p.get("imagenes") or [])))

    def _link_por_menu(self, page: Any, urn: str) -> str:
        """Link a la publicación vía "Copiar enlace a la publicación" (vacío si no se pudo).
        El link corto (lnkd.in) se resuelve a la URL de LinkedIn sin seguir la redirección."""
        if not urn.startswith(_PREFIJO_URN_TARJETA):
            return ""
        clave = urn[len(_PREFIJO_URN_TARJETA):]
        try:
            tarjeta = page.locator(f'[role="listitem"][componentkey*="{clave}"]').first
            boton = tarjeta.locator(_SEL_BOTON_MENU).first
            if not boton.count():
                return ""
            page.evaluate("window.__radarCopiado = null")
            item = page.locator('[role="menuitem"]').filter(has_text=_RE_ITEM_COPIAR).first
            # A veces el menú no abre al primer click: un reintento
            for intento in range(2):
                boton.click(timeout=5000)
                try:
                    item.wait_for(state="visible", timeout=3000)
                    break
                except Exception:
                    page.keyboard.press("Escape")
                    if intento:
                        return ""
                    self._sleep(self._rng.uniform(*_ESPERA_MENU))
            self._sleep(self._rng.uniform(*_ESPERA_MENU))
            item.click(timeout=5000)
            self._sleep(self._rng.uniform(*_ESPERA_MENU))
            corto = str(page.evaluate("window.__radarCopiado") or "").strip()
            # Sin esto el menú del post siguiente no abre (queda el foco en el anterior)
            page.keyboard.press("Escape")
        except Exception:
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass
            return ""
        if not corto.startswith("https://"):
            return ""
        return self._resolver_link(corto)

    def _resolver_link(self, corto: str) -> str:
        try:
            r = self._context.request.get(corto, max_redirects=0, timeout=10000)
            destino = r.headers.get("location") or ""
        except Exception:
            destino = ""
        host = (urlparse(destino).hostname or "").lower()
        if host == "www.linkedin.com":
            return _sin_query(destino)
        return _sin_query(corto)

    def _check_logged_in_sync(self) -> bool:
        page = self._current_page()
        # Se sondea cada ~30 s mientras el usuario inicia sesión: no navegar si la pestaña ya
        # está en login/checkpoint (borraría el formulario/2FA) ni si ya está autenticada.
        page.wait_for_timeout(250)  # procesa eventos pendientes para que page.url esté al día
        url = page.url
        if is_login_or_checkpoint_url(url):
            return False
        if _is_authenticated_linkedin_url(url):
            return True
        page.goto(FEED_URL, wait_until="domcontentloaded")
        if is_login_or_checkpoint_url(page.url):
            return False
        # Sin sesión, LinkedIn puede redirigir a la portada pública: solo /feed cuenta
        return _is_authenticated_linkedin_url(page.url)

    def _show_sync(self) -> None:
        page = self._current_page()
        try:
            sesion = self._context.new_cdp_session(page)
            try:
                ventana = sesion.send("Browser.getWindowForTarget")
                sesion.send("Browser.setWindowBounds", {
                    "windowId": ventana["windowId"],
                    "bounds": {"windowState": "normal"},
                })
            finally:
                sesion.detach()
        except Exception:
            # Si CDP falla igual intentamos traer la pestaña al frente
            pass
        page.bring_to_front()

    def _dump_debug_sync(self, target_dir: Path) -> None:
        target_dir.mkdir(parents=True, exist_ok=True)
        page = self._current_page()
        page.screenshot(path=str(target_dir / "screenshot.png"))
        (target_dir / "page.html").write_text(page.content(), encoding="utf-8")

    def _close_sync(self) -> None:
        context, pw = self._context, self._pw
        self._context = self._page = self._pw = None
        self._opened = False
        try:
            if context is not None:
                try:
                    context.close()
                except Exception:
                    pass  # ya cerrado por el usuario
        finally:
            if pw is not None:
                try:
                    pw.stop()
                except Exception:
                    pass

    @staticmethod
    def _safe_content(page: Any) -> str:
        try:
            return page.content()
        except Exception:
            return ""
