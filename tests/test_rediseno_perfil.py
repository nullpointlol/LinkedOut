"""Rediseño E3, Mi perfil (docs/rediseno/plan.md §6).

E3a: seis secciones por tarea con un índice; cada tarjeta conserva su borrador, versión y respaldo (sin cambios de
comportamiento). Medidas: las 6 secciones en orden, cada tarjeta en su sección, estilos solo con tokens y fuera del JS.
"""
import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
PERFIL_JS = RAIZ / "static" / "perfil.js"
PERFIL_CSS = RAIZ / "static" / "css" / "perfil.css"
SECCIONES = ["datos", "sabe", "busco", "cv", "aprendio", "automatizacion"]


def test_seis_secciones_en_el_orden_del_plan():
    js = PERFIL_JS.read_text(encoding="utf-8")
    bloque = js[js.index("const SECCIONES = ["):js.index("];", js.index("const SECCIONES = ["))]
    # Filas de sección: [id, título, ayuda, ícono, [tarjetas]]
    assert re.findall(r"^\s+\['([a-z]+)', '[^']+', '[^']*', '[a-z-]+',$", bloque, re.M) == SECCIONES


def test_cada_tarjeta_tiene_su_seccion():
    js = PERFIL_JS.read_text(encoding="utf-8")
    definidas = set(re.findall(r"\['([a-z_]+)', '[^']+'\]", js[:js.index("const JSON_FORMULARIO")]))
    bloque = js[js.index("const SECCIONES = ["):js.index("];", js.index("const SECCIONES = ["))]
    ubicadas = set(re.findall(r"'([a-z_]+)'", "".join(re.findall(r"\[([^\[\]]*)\]\]", bloque))))
    assert definidas and definidas <= ubicadas, definidas - ubicadas
    assert {"busquedas", "regenerar"} <= ubicadas


def test_estilos_de_mi_perfil_en_perfil_css_con_tokens():
    js = PERFIL_JS.read_text(encoding="utf-8")
    assert "nodo('style'" not in js and not re.search(r"#[0-9a-fA-F]{6}\b", js)
    tokens = set(re.findall(r"--([a-z0-9-]+)\s*:", (RAIZ / "static/css/tokens.css").read_text(encoding="utf-8")))
    css = re.sub(r"/\*.*?\*/", "", PERFIL_CSS.read_text(encoding="utf-8"), flags=re.S)
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b(?![\w-])(?!\s*[{,])", css.replace("#vista-perfil", ""))
    assert set(re.findall(r"var\(--([a-z0-9-]+)", css)) <= tokens
    html = (RAIZ / "static/index.html").read_text(encoding="utf-8")
    assert 0 < html.find('href="/static/css/radar.css') < html.find('href="/static/css/perfil.css')


# ---------------------------------------------------------------- E3b: guardado único y aviso al salir

from tests.test_perfil_navegacion import pagina  # noqa: E402,F401
from tests.test_perfil_ui import iniciar, mutaciones, tarjeta  # noqa: E402


def _barra(pagina):
    return pagina.locator("#vista-perfil .perfil-barra")


def test_barra_aparece_con_cambios_y_guarda_cada_tarjeta_en_orden(pagina):
    if not iniciar(pagina):
        return
    assert _barra(pagina).is_hidden()
    tarjeta(pagina, "Voz del mail").get_by_role("textbox").fill("Tono cercano")
    tarjeta(pagina, "Experiencia").get_by_role("textbox").fill("Nueva experiencia")
    assert _barra(pagina).is_visible()
    texto = _barra(pagina).inner_text()
    assert "2 tarjetas sin guardar" in texto and "Experiencia" in texto and "Voz del mail" in texto
    pagina.get_by_role("button", name="Guardar cambios", exact=True).click()
    pagina.wait_for_function("() => document.querySelector('#vista-perfil .perfil-barra').hidden", timeout=3000)
    # En el orden de las tarjetas (Experiencia antes que Voz del mail), cada una con su versión
    assert [m["url"].split("/")[-1] for m in mutaciones(pagina)] == ["experiencia", "voz_email"]


def test_si_una_falla_las_demas_quedan_guardadas_y_esa_conserva_su_borrador(pagina):
    if not iniciar(pagina):
        return
    pagina.evaluate("() => { failures['PUT /api/candidato/perfil/voz_email']=409; }")
    tarjeta(pagina, "Experiencia").get_by_role("textbox").fill("Guardada")
    tarjeta(pagina, "Voz del mail").get_by_role("textbox").fill("Queda pendiente")
    pagina.get_by_role("button", name="Guardar cambios", exact=True).click()
    pagina.wait_for_function("() => !document.querySelector('#vista-perfil .perfil-barra').textContent.includes('Experiencia')", timeout=3000)
    assert "Guardado." in tarjeta(pagina, "Experiencia").get_by_role("status").inner_text()
    voz = tarjeta(pagina, "Voz del mail")
    assert voz.get_by_role("textbox").input_value() == "Queda pendiente"
    assert voz.get_by_role("button", name="Recargar", exact=True).is_visible()
    assert "Voz del mail" in _barra(pagina).inner_text()


def test_aviso_al_salir_seguir_salir_y_guardar(pagina):
    if not iniciar(pagina):
        return
    pagina.evaluate("() => { window.vistaActual = 'perfil'; }")
    tarjeta(pagina, "Experiencia").get_by_role("textbox").fill("Borrador")
    pagina.evaluate("() => cambiarVista('manual')")
    dialogo = pagina.get_by_role("dialog", name="Tenés cambios sin guardar")
    assert dialogo.is_visible() and not pagina.locator("#vista-perfil").evaluate("el => el.hidden")
    dialogo.get_by_role("button", name="Seguir editando", exact=True).click()
    assert dialogo.is_hidden() and pagina.evaluate("() => window.vistaActual") == "perfil"
    pagina.evaluate("() => cambiarVista('manual')")
    dialogo.get_by_role("button", name="Salir sin guardar", exact=True).click()
    assert pagina.evaluate("() => window.vistaActual") == "manual" and not mutaciones(pagina)
    # Queda como borrador (la vista está oculta: se lee del DOM)
    assert pagina.evaluate("() => [...document.querySelectorAll('fieldset')].find(f => f.querySelector('legend').textContent === 'Experiencia').querySelector('textarea').value") == "Borrador"
    pagina.evaluate("() => { cambiarVista('perfil'); cambiarVista('radar'); }")
    dialogo.get_by_role("button", name="Guardar y salir", exact=True).click()
    pagina.wait_for_function("() => window.vistaActual === 'radar'", timeout=3000)
    assert [m["url"].split("/")[-1] for m in mutaciones(pagina)] == ["experiencia"]


def test_descartar_recarga_las_tarjetas_cambiadas(pagina):
    if not iniciar(pagina):
        return
    pagina.evaluate("() => { window.accept = true; }")  # el fixture responde window.confirm con `accept`
    tarjeta(pagina, "Experiencia").get_by_role("textbox").fill("Se descarta")
    pagina.get_by_role("button", name="Descartar", exact=True).click()
    pagina.wait_for_function("() => document.querySelector('#vista-perfil .perfil-barra').hidden", timeout=3000)
    assert tarjeta(pagina, "Experiencia").get_by_role("textbox").input_value() != "Se descarta"
    assert not mutaciones(pagina)


def test_ningun_css_propio_usa_has():
    """:has() encarecía la invalidación de estilos con las 2760 tarjetas del Radar (+500 ms en el test de la base
    grande). Los CSS del rediseño no lo usan."""
    for css in (RAIZ / "static" / "css").glob("*.css"):
        texto = re.sub(r"/\*.*?\*/", "", css.read_text(encoding="utf-8"), flags=re.S)
        assert ":has(" not in texto, css.name
