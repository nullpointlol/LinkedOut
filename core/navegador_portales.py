"""Navegador de Portales: Edge con su propio perfil, como el de LinkedIn, para los portales que solo se leen
desde un navegador (Bumeran). Lee páginas públicas de listado.

Si el sitio muestra un desafío o un captcha, `ErrorPortal` con la página: el radar abre un ticket (se
investiga después) y sigue con su ritmo.
"""
from __future__ import annotations

import asyncio
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional

import core.paths
from core.portales import ErrorPortal

logger = logging.getLogger(__name__)

PERFIL = "portales_navegador"
# Señales de una verificación (Cloudflare, captcha): se informa en un ticket
_RE_DESAFIO = re.compile(r"just a moment|attention required|verific(?:á|a) que sos (?:un )?humano|captcha|"
                         r"checking your browser|cf-challenge", re.IGNORECASE)

# Las tarjetas de un listado de Bumeran: cada aviso es un link «/empleos/<slug>-<id>.html» con su encabezado (fecha,
# título, empresa y aviso) y sus datos (ubicación, modalidad) en elementos con ese id
_JS_TARJETAS_BUMERAN = """() => {
  const vistos = new Set();
  const salida = [];
  for (const a of document.querySelectorAll('a[href*="/empleos/"]')) {
    const href = a.getAttribute('href') || '';
    const m = href.match(/-(\\d+)\\.html/);
    if (!m || vistos.has(m[1])) continue;
    vistos.add(m[1]);
    const texto = (id) => { const e = document.getElementById(id + m[1]); return e ? e.innerText : ''; };
    salida.push({id: m[1], href: href.split('?')[0], encabezado: texto('header-col-job-posting-'),
                 datos: texto('data-col-job-posting-')});
  }
  return salida;
}"""


class LectorPortales:
    """Un Edge minimizado con su perfil, en su propio hilo (Playwright sync no se mezcla con el event loop)."""

    def __init__(self, perfil: Optional[Path] = None):
        self._perfil = Path(perfil) if perfil else core.paths.DATA_DIR / PERFIL
        self._executor: Optional[ThreadPoolExecutor] = None
        self._pw: Any = None
        self._contexto: Any = None
        self._pagina: Any = None

    async def _run(self, fn, *args):
        if self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="portales")
        return await asyncio.get_running_loop().run_in_executor(self._executor, fn, *args)

    async def tarjetas_bumeran(self, url: str, plazo_s: int) -> List[Dict[str, str]]:
        """Las tarjetas del listado; `plazo_s` (del perfil): cuánto se espera la página."""
        return await self._run(self._tarjetas_sync, url, plazo_s * 1000)

    async def cerrar(self) -> None:
        if self._executor is None:
            return
        try:
            await self._run(self._cerrar_sync)
        finally:
            self._executor.shutdown(wait=False)
            self._executor = None

    # ------------------------------------------------------------------ hilo del navegador

    def _abrir_sync(self) -> None:
        if self._pagina is not None:
            return
        from playwright.sync_api import sync_playwright
        self._perfil.mkdir(parents=True, exist_ok=True)
        self._pw = sync_playwright().start()
        self._contexto = self._pw.chromium.launch_persistent_context(
            user_data_dir=str(self._perfil), channel="msedge", headless=False, chromium_sandbox=True,
            args=["--start-minimized"], viewport={"width": 1280, "height": 900})
        self._pagina = self._contexto.pages[0] if self._contexto.pages else self._contexto.new_page()

    def _tarjetas_sync(self, url: str, plazo_ms: int) -> List[Dict[str, str]]:
        self._abrir_sync()
        try:
            self._pagina.goto(url, wait_until="domcontentloaded", timeout=plazo_ms)
            # El listado se arma con JavaScript: se espera a que estén las tarjetas (o a que la página termine de cargar)
            try:
                self._pagina.wait_for_selector('a[href*="/empleos/"]', timeout=plazo_ms)
            except Exception:  # noqa: BLE001 — sin tarjetas: un listado vacío o una verificación (se mira abajo)
                pass
            titulo = self._pagina.title() or ""
            cuerpo = (self._pagina.inner_text("body") or "")[:3000]
        except Exception as e:  # noqa: BLE001 — se informa como error del portal
            self._cerrar_sync()
            raise ErrorPortal(f"No se pudo leer la página: {type(e).__name__}") from None
        if _RE_DESAFIO.search(titulo) or _RE_DESAFIO.search(cuerpo):
            raise ErrorPortal(f"El sitio pidió una verificación (captcha o desafío) en {url}: «{titulo or cuerpo[:80]}»")
        return list(self._pagina.evaluate(_JS_TARJETAS_BUMERAN) or [])

    def _cerrar_sync(self) -> None:
        for cosa, metodo in ((self._contexto, "close"), (self._pw, "stop")):
            try:
                if cosa is not None:
                    getattr(cosa, metodo)()
            except Exception:  # noqa: BLE001
                pass
        self._pw = self._contexto = self._pagina = None


_LECTOR: Optional[LectorPortales] = None


def lector() -> LectorPortales:
    global _LECTOR
    if _LECTOR is None:
        _LECTOR = LectorPortales()
    return _LECTOR


async def cerrar() -> None:
    if _LECTOR is not None:
        await _LECTOR.cerrar()
