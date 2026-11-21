"""Navegador de Portales (`core/navegador_portales.py`) con un Playwright falso: nunca abre Edge."""
import sys
import types

import pytest

from core import navegador_portales as np
from core.portales import ErrorPortal


class _Pagina:
    def __init__(self, titulo="Empleos", cuerpo="listado", tarjetas=None, falla_goto=False, sin_tarjetas=False):
        self.titulo, self.cuerpo, self.tarjetas = titulo, cuerpo, tarjetas or []
        self.falla_goto, self.sin_tarjetas = falla_goto, sin_tarjetas
        self.visitadas = []

    def goto(self, url, wait_until, timeout):
        if self.falla_goto:
            raise TimeoutError("sin respuesta")
        self.visitadas.append((url, timeout))

    def wait_for_selector(self, selector, timeout):
        if self.sin_tarjetas:
            raise TimeoutError("sin tarjetas")

    def title(self):
        return self.titulo

    def inner_text(self, selector):
        return self.cuerpo

    def evaluate(self, js):
        return self.tarjetas

    def content(self):
        return f"<html>{self.cuerpo}</html>"


class _Contexto:
    def __init__(self, pagina, con_paginas=True, falla_cerrar=False):
        self.pages = [pagina] if con_paginas else []
        self._pagina, self.cerrado, self.falla_cerrar = pagina, False, falla_cerrar

    def new_page(self):
        return self._pagina

    def close(self):
        if self.falla_cerrar:
            raise RuntimeError("ya cerrado")
        self.cerrado = True


@pytest.fixture
def playwright_falso(monkeypatch):
    estado = {"lanzados": [], "detenido": 0}

    def armar(pagina, **kw):
        contexto = _Contexto(pagina, **kw)

        class _PW:
            class chromium:  # noqa: N801
                @staticmethod
                def launch_persistent_context(**args):
                    estado["lanzados"].append(args)
                    return contexto

            def stop(self):
                estado["detenido"] += 1

        modulo = types.ModuleType("playwright.sync_api")
        modulo.sync_playwright = lambda: types.SimpleNamespace(start=lambda: _PW())
        monkeypatch.setitem(sys.modules, "playwright.sync_api", modulo)
        estado["contexto"] = contexto
        return estado

    return armar


@pytest.mark.asyncio
async def test_lee_las_tarjetas_con_su_perfil_y_el_plazo(tmp_path, playwright_falso):
    pagina = _Pagina(tarjetas=[{"id": "1", "href": "/empleos/a-1.html", "encabezado": "x", "datos": "y"}])
    estado = playwright_falso(pagina)
    lector = np.LectorPortales(tmp_path / "perfil")
    try:
        tarjetas = await lector.tarjetas_bumeran("https://www.bumeran.com.ar/empleos.html", 7)
        assert tarjetas[0]["id"] == "1"
        assert pagina.visitadas == [("https://www.bumeran.com.ar/empleos.html", 7000)]
        assert estado["lanzados"][0]["user_data_dir"] == str(tmp_path / "perfil")
        assert (tmp_path / "perfil").is_dir()
        await lector.tarjetas_bumeran("https://www.bumeran.com.ar/otro.html", 7)
        assert len(estado["lanzados"]) == 1  # el navegador queda abierto entre lecturas
    finally:
        await lector.cerrar()
    assert estado["contexto"].cerrado and estado["detenido"] == 1
    await lector.cerrar()  # cerrar dos veces no falla


@pytest.mark.asyncio
async def test_sin_tarjetas_devuelve_lista_vacia_y_crea_la_pagina(tmp_path, playwright_falso):
    playwright_falso(_Pagina(sin_tarjetas=True, tarjetas=None), con_paginas=False)
    lector = np.LectorPortales(tmp_path / "perfil")
    try:
        assert await lector.tarjetas_bumeran("https://x", 1) == []
    finally:
        await lector.cerrar()


@pytest.mark.parametrize("titulo,cuerpo", [("Just a moment...", ""), ("Empleos", "Verificá que sos un humano")])
@pytest.mark.asyncio
async def test_una_verificacion_es_error_del_portal(tmp_path, playwright_falso, titulo, cuerpo):
    playwright_falso(_Pagina(titulo=titulo, cuerpo=cuerpo))
    lector = np.LectorPortales(tmp_path / "perfil")
    try:
        with pytest.raises(ErrorPortal, match="verificación"):
            await lector.tarjetas_bumeran("https://x", 1)
    finally:
        await lector.cerrar()


@pytest.mark.asyncio
async def test_si_la_pagina_falla_cierra_el_navegador_y_avisa(tmp_path, playwright_falso):
    estado = playwright_falso(_Pagina(falla_goto=True), falla_cerrar=True)
    lector = np.LectorPortales(tmp_path / "perfil")
    try:
        with pytest.raises(ErrorPortal, match="TimeoutError"):
            await lector.tarjetas_bumeran("https://x", 1)
        assert estado["detenido"] == 1 and lector._pagina is None  # el cierre sigue aunque el contexto falle
    finally:
        await lector.cerrar()


@pytest.mark.asyncio
async def test_lector_unico_y_cierre_del_modulo(monkeypatch, tmp_path):
    monkeypatch.setattr(np, "_LECTOR", None)
    await np.cerrar()  # sin lector, no hace nada
    monkeypatch.setattr(np.core.paths, "DATA_DIR", tmp_path)
    primero = np.lector()
    assert np.lector() is primero and primero._perfil == tmp_path / np.PERFIL
    await np.cerrar()


@pytest.mark.asyncio
async def test_lee_el_html_de_un_aviso(tmp_path, playwright_falso):
    pagina = _Pagina(cuerpo="Analista de datos")
    playwright_falso(pagina)
    lector = np.LectorPortales(tmp_path / "perfil")
    try:
        assert await lector.html("https://ar.indeed.com/viewjob?jk=1", 30) == "<html>Analista de datos</html>"
        assert pagina.visitadas == [("https://ar.indeed.com/viewjob?jk=1", 30000)]
        playwright_falso(_Pagina(titulo="Just a moment..."))
        with pytest.raises(ErrorPortal, match="verificación"):
            await np.LectorPortales(tmp_path / "otro").html("https://x", 1)
    finally:
        await lector.cerrar()
