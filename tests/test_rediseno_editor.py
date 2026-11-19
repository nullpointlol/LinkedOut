"""Rediseño E4, el editor de una postulación (docs/rediseno/plan.md §6): en el celular, 4 pasos (Ofertas → Oferta →
Requisitos → CV) con el paso activo marcado; en escritorio, 3 columnas. Estados con atributos ARIA, no con clases.

Con el DOM real de index.html y solo los CSS propios (sin Tailwind, Font Awesome ni red).
Medidas: ningún id ni handler perdido (los de antes siguen); sin clases de Tailwind ni íconos de Font Awesome en el
marcado del editor; sin scroll horizontal a 360/375/1200; un solo paso visible en el celular; las 3 columnas lado a
lado en escritorio; filtros, pestañas y desplegables con aria-pressed / aria-selected / aria-expanded.
"""
import re

import pytest
from playwright.sync_api import sync_playwright

from tests.test_rediseno_marco import RAIZ, documento

CSS = [RAIZ / "static" / "css" / n for n in ("tokens.css", "componentes.css", "marco.css", "editor.css")]
EDITOR = ("#navMobilePostulaciones", "main", "#dropdownTelemetria", "#modalNueva")  # E4b: Ajustes y Nueva oferta
# Ids y handlers que el editor tenía antes del rediseño (regla del plan §9: todo id y onclick sigue existiendo)
IDS = ["tabBtn-ofertas", "tabBtn-vacante", "tabBtn-tags", "tabBtn-cv", "panel-ofertas", "panel-izquierdo",
       "contadorOfertasBadge", "btnNuevaOfertaLista", "inputBuscarOfertas", "tabsLifecycleFiltros", "tabLifecycleTodas",
       "tabLifecyclePorPostular", "tabLifecyclePostuladas", "tabLifecycleEnProceso", "tabLifecycleFinalizadas",
       "chipFiltroTodas", "chipFiltroSinCV", "chipFiltroListas", "selectOrdenOfertas", "listaOfertasCards", "panel-medio",
       "subpanel-vacante", "ofertaTitulo", "ofertaEmpresa", "ofertaStatusBadge", "btnVerEnRadar", "btnDescartarOferta",
       "lblDescartar", "lifecycleControlBox", "selectEstadoPostulacion", "badgeDiasTranscurridos", "btnAbrirSeguimiento",
       "lblToggleSeguimiento", "iconToggleSeguimiento", "seccionSeguimientoDetalle", "inputLinkPostulacion",
       "btnIrLinkPostulacion", "inputContactoRecruiter", "inputNotasSeguimiento", "fechaPostulacionDisplay",
       "labelGuardadoLifecycle", "btnGuardarLifecycle", "listaDolores", "btnModoVacanteLimpio", "btnModoVacanteCrudo",
       "btnToggleTextoOferta", "labelToggleTexto", "iconToggleTexto", "ofertaContenido", "subpanel-tags",
       "badgeAprendizajesHeader", "cantAprendizajesHeader", "wrapperScoreMatch", "scoreText", "scoreVerdictBadge",
       "btnRecalcularTags", "scoreBar", "caminoAl100Container", "viewCentralMatriz", "contenedorMatrizOferta",
       "viewCentralAfinidad", "filter-TODOS", "filter-MATCH_DIRECTO", "filter-TRANSFERIBLE", "filter-BRECHA_REAL",
       "filter-RESPONSABILIDAD", "filter-REQUISITO_EXCLUYENTE", "filter-REQUISITO_DESEABLE", "contenedorTags",
       "panel-cv", "rightTab-cv", "rightTab-audit", "cv-actions", "badgeModoFallback", "btnDescargarDocx",
       "btnDescargarPdf", "btnFormatoCv", "btnFormatoCvTexto", "subtab-cv", "cvCharCount", "cvContent", "subtab-audit",
       "auditRemediationBox", "btnCorregirCV", "auditFindingsList", "auditVerdict", "auditAfinidadScore",
       "atsScoreValue", "aiScoreValue", "auditDeductionsContainer", "auditDeductionsTotal", "auditDeductionsList",
       "keywordCoveragePct", "keywordCoverageTags", "auditDetectedAI"]


# ---------------------------------------------------------------- marcado

def test_editor_conserva_ids_y_handlers():
    soup = documento()
    for i in IDS:
        assert soup.find(id=i), i
    handlers = {b["id"]: b.get("onclick") for b in soup.select("main button[id], #navMobilePostulaciones button")}
    assert handlers["tabBtn-tags"] == "setMobileTab('tags')" and handlers["filter-BRECHA_REAL"] == "filtrarTags('BRECHA_REAL')"
    assert handlers["btnDescartarOferta"] == "descartarOfertaClick(this)" and handlers["rightTab-audit"] == "setRightTab('audit')"


def test_editor_sin_tailwind_ni_font_awesome():
    """Cada clase del marcado del editor está definida en los CSS propios (nada de Tailwind ni Font Awesome)."""
    propias = set()
    for archivo in (RAIZ / "static" / "css").glob("*.css"):
        propias |= set(re.findall(r"\.([a-zA-Z][\w-]*)", re.sub(r"/\*.*?\*/", "", archivo.read_text(encoding="utf-8"), flags=re.S)))
    propias |= {"hidden", "prose-cv"}  # «hidden» la define editor.css bajo main.editor; prose-cv, el <style> del CV
    soup = documento()
    for sel in EDITOR:
        raiz = soup.select_one(sel)
        assert not raiz.select("[class*=fa-]"), sel
        for el in [raiz, *raiz.find_all(class_=True)]:
            ajenas = set(el.get("class", [])) - propias
            assert not ajenas, (el.get("id"), ajenas)


def test_editor_css_solo_con_tokens():
    tokens = set(re.findall(r"--([a-z0-9-]+)\s*:", CSS[0].read_text(encoding="utf-8")))
    css = re.sub(r"/\*.*?\*/", "", CSS[-1].read_text(encoding="utf-8"), flags=re.S)
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css)
    assert set(re.findall(r"var\(--([a-z0-9-]+)", css)) <= tokens
    assert ":has(" not in css


# ---------------------------------------------------------------- navegador (sin servidor)

@pytest.fixture(scope="module")
def navegador():
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        yield b
        b.close()


@pytest.fixture
def pagina(navegador):
    soup = documento()
    for nodo in soup.select("script, link[rel=stylesheet]"):
        nodo.decompose()
    page = navegador.new_page(viewport={"width": 375, "height": 812})
    sprite = (RAIZ / "static" / "iconos.svg").read_text(encoding="utf-8")

    def ruta(r):
        url = r.request.url
        if url.endswith("/static/iconos.svg"):
            r.fulfill(body=sprite, content_type="image/svg+xml")
        elif url == "http://editor.test/":
            r.fulfill(body=str(soup), content_type="text/html; charset=utf-8")
        else:
            r.abort()

    page.route("**/*", ruta)
    page.goto("http://editor.test/")
    for archivo in CSS:
        page.add_style_tag(content=archivo.read_text(encoding="utf-8"))
    page.add_script_tag(content=(RAIZ / "static" / "iconos.js").read_text(encoding="utf-8"))
    page.add_script_tag(content=(RAIZ / "static" / "app.js").read_text(encoding="utf-8"))
    page.evaluate("() => { window.Radar = {mostrar() {}}; window.Perfil = {mostrar() {}}; cambiarVista('editor'); }")
    yield page
    page.close()


def _visibles(page):
    return {i for i in ("panel-ofertas", "panel-medio", "subpanel-vacante", "subpanel-tags", "panel-cv")
            if page.locator(f"#{i}").is_visible()}


@pytest.mark.parametrize("ancho", [360, 375])
def test_celular_un_paso_por_vez_con_el_activo_marcado(pagina, ancho):
    pagina.set_viewport_size({"width": ancho, "height": 812})
    assert pagina.locator("#navMobilePostulaciones").is_visible()
    pasos = {"ofertas": {"panel-ofertas"}, "vacante": {"panel-medio", "subpanel-vacante"},
             "tags": {"panel-medio", "subpanel-tags"}, "cv": {"panel-cv"}}
    assert _visibles(pagina) == pasos["ofertas"]
    for paso, visibles in pasos.items():
        pagina.click(f"#tabBtn-{paso}")
        assert _visibles(pagina) == visibles, paso
        actuales = pagina.locator("#navMobilePostulaciones [aria-current=step]")
        assert actuales.count() == 1 and actuales.get_attribute("id") == f"tabBtn-{paso}"
        assert pagina.evaluate("() => document.documentElement.scrollWidth <= innerWidth"), paso
        alto = pagina.locator(f"#tabBtn-{paso}").bounding_box()["height"]
        assert alto >= 44, f"paso {paso}: {alto}px"
    assert [pagina.locator(f"#tabBtn-{p}").inner_text().strip() for p in pasos] == ["Ofertas", "Oferta", "Requisitos", "CV"]


def test_escritorio_tres_columnas_sin_pasos(pagina):
    pagina.set_viewport_size({"width": 1200, "height": 900})
    assert not pagina.locator("#navMobilePostulaciones").is_visible()
    assert _visibles(pagina) == {"panel-ofertas", "panel-medio", "subpanel-vacante", "subpanel-tags", "panel-cv"}
    cajas = [pagina.locator(f"#{i}").bounding_box() for i in ("panel-ofertas", "panel-medio", "panel-cv")]
    assert cajas[0]["x"] < cajas[1]["x"] < cajas[2]["x"] and abs(cajas[0]["y"] - cajas[2]["y"]) < 2
    assert pagina.evaluate("() => document.documentElement.scrollWidth <= innerWidth")


def test_filtros_pestanas_y_desplegables_con_aria(pagina):
    pagina.set_viewport_size({"width": 1200, "height": 900})
    pagina.evaluate("() => { window.renderizarListaOfertas = () => {}; window.renderizarListaTags = () => {}; }")
    pagina.click("#tabLifecyclePostuladas")
    assert pagina.get_attribute("#tabLifecyclePostuladas", "aria-pressed") == "true"
    assert pagina.get_attribute("#tabLifecycleTodas", "aria-pressed") == "false"
    pagina.click("#chipFiltroSinCV")
    assert pagina.get_attribute("#chipFiltroSinCV", "aria-pressed") == "true"
    pagina.click("#filter-BRECHA_REAL")
    assert pagina.get_attribute("#filter-BRECHA_REAL", "aria-pressed") == "true"
    assert pagina.get_attribute("#filter-TODOS", "aria-pressed") == "false"
    pagina.click("#rightTab-audit")
    assert pagina.get_attribute("#rightTab-audit", "aria-selected") == "true"
    assert pagina.locator("#subtab-audit").is_visible() and not pagina.locator("#subtab-cv").is_visible()
    pagina.click("#btnAbrirSeguimiento")
    assert pagina.get_attribute("#btnAbrirSeguimiento", "aria-expanded") == "true"
    assert pagina.locator("#seccionSeguimientoDetalle").is_visible()
    pagina.click("#btnToggleTextoOferta")
    assert pagina.get_attribute("#btnToggleTextoOferta", "aria-expanded") == "true"
    assert pagina.locator("#labelToggleTexto").inner_text() == "Ver menos"


OFERTA = {"id": 7, "titulo": "Desarrolladora frontend", "empresa": "Tienda Ejemplo", "status": "READY", "match_score": 72,
          "estado_postulacion": "POSTULADA", "created_at": "2026-11-18 10:00:00", "contenido_oferta": "Buscamos:\n- React\n- Testing",
          "dolores_empresa": ["Lanzar la tienda nueva"], "cv_markdown": "# Sam Ortega",
          "requisitos": [{"nombre": "React", "estado": "MATCH_DIRECTO", "tipo": "REQUISITO_EXCLUYENTE"},
                         {"nombre": "Testing", "estado": "TRANSFERIBLE", "tipo": "REQUISITO_DESEABLE"},
                         {"nombre": "Kotlin", "estado": "BRECHA_REAL", "tipo": "REQUISITO_DESEABLE"}],
          "reporte_auditoria": {"verdict": "PASE A ENTREVISTA", "ats": {"keyword_coverage": 0.8, "found_keywords": ["React"],
                                                                      "missing_keywords": ["Kotlin"]}}}


def _clases_propias():
    propias = set()
    for archivo in (RAIZ / "static" / "css").glob("*.css"):
        propias |= set(re.findall(r"\.([a-zA-Z][\w-]*)", re.sub(r"/\*.*?\*/", "", archivo.read_text(encoding="utf-8"), flags=re.S)))
    return propias | {"hidden", "prose-cv"}


@pytest.mark.parametrize("ancho", [375, 1200])
def test_contenido_dibujado_con_clases_propias_y_accesible(pagina, ancho):
    pagina.set_viewport_size({"width": ancho, "height": 900})
    pagina.evaluate("""o => {
        window.fetch = async () => ({ ok: true, json: async () => o });
        listaOfertasMemoria = [o];
        renderizarListaOfertas([o], null);
        ofertaActual = o;
        renderOferta(o);
    }""", OFERTA)
    tarjeta = pagina.locator("#card-oferta-7")
    assert tarjeta.get_attribute("role") == "button" and tarjeta.get_attribute("tabindex") == "0"
    tarjeta.focus()
    pagina.keyboard.press("Enter")
    pagina.wait_for_function("() => document.querySelector('#card-oferta-7').getAttribute('aria-current') === 'true'")
    if ancho < 1024:
        pagina.click("#tabBtn-tags")
    chips = pagina.locator("#contenedorMatrizOferta [data-ciclar-tag]")
    assert chips.count() >= 3
    assert all(t == "button" for t in chips.evaluate_all("els => els.map(e => e.tagName.toLowerCase())"))
    filas = pagina.locator("#contenedorTags > button")
    assert filas.count() == 3
    ajenas = pagina.evaluate("""() => [...document.querySelectorAll('main *')]
        .flatMap(e => typeof e.className === 'string' ? e.className.split(/\s+/) : [...e.classList]).filter(Boolean)""")
    assert not (set(ajenas) - _clases_propias()), set(ajenas) - _clases_propias()
    assert pagina.evaluate("() => document.documentElement.scrollWidth <= innerWidth")


def test_index_sin_ids_repetidos():
    """Un id repetido rompe getElementById y las etiquetas (E4b: quedó un resto del panel viejo con un id duplicado)."""
    from collections import Counter
    ids = Counter(el["id"] for el in documento().find_all(id=True))
    assert not [i for i, n in ids.items() if n > 1], [i for i, n in ids.items() if n > 1]
