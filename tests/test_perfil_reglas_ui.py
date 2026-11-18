"""Mi perfil: tarjeta «Reglas del Radar» (12/11), con campos visibles y editables. Chromium real, API simulada."""
import json

import pytest

from test_perfil_navegacion import pagina  # noqa: F401  (fixture)
from test_perfil_ui import iniciar, tarjeta, guardar, mutaciones

TITULO = "Reglas del Radar"
ETIQUETAS = {
    "Búsquedas por hora (cada cuenta)": "6",
    "Búsquedas por día (cada cuenta)": "60",
    "Búsquedas que quedan (en %) si LinkedIn frenó a las dos cuentas en las últimas 24 h": "67",
    "Pausa entre lecturas: desde (min)": "3",
    "Pausa entre lecturas: hasta (min)": "6",
    "Pausa entre vueltas: desde (min)": "25",
    "Pausa entre vueltas: hasta (min)": "35",
    "Búsqueda: posts por lectura": "300",
    "Búsqueda: scrolls por lectura": "15",
    "Búsqueda: pantallas seguidas sin nada nuevo antes de cortar": "2",
    "Feed: posts por lectura": "80",
    "Feed: scrolls por lectura": "20",
    "Pausa por bloqueo: primera (horas)": "6",
    "Pausa por bloqueo: máxima (horas)": "24",
    "Envíos por día": "40",
}


@pytest.mark.parametrize("ancho", [375, 1200])
def test_muestra_edita_y_guarda_las_reglas(pagina, ancho):
    if not iniciar(pagina):
        return
    pagina.set_viewport_size({"width": ancho, "height": 812})
    card = tarjeta(pagina, TITULO)
    assert card.count() == 1
    for etiqueta, valor in ETIQUETAS.items():
        assert card.get_by_label(etiqueta, exact=True).input_value() == valor, etiqueta
    card.get_by_label("Búsquedas por hora (cada cuenta)", exact=True).fill("4")
    card.get_by_label("Pausa entre vueltas: hasta (min)", exact=True).fill("40")
    guardar(pagina, TITULO)
    put = [c for c in mutaciones(pagina) if c["url"].endswith("/reglas_radar")][-1]
    nuevo = json.loads(put["payload"]["contenido"])
    assert nuevo["busquedas_por_hora"] == 4 and nuevo["pausa_entre_vueltas_min"] == {"desde": 25, "hasta": 40}
    assert nuevo["lectura_feed"] == {"posts": 80, "scrolls": 20} and nuevo["tope_envios_dia"] == 40
    assert "Guardado" in card.get_by_role("status").inner_text()
    assert pagina.evaluate("() => document.documentElement.scrollWidth <= innerWidth")
    for control in card.locator("input, button").all():
        if control.is_visible():
            caja = control.bounding_box()
            assert caja and caja["x"] >= 0 and caja["x"] + caja["width"] <= ancho


def test_error_del_servidor_conserva_la_edicion(pagina):
    if not iniciar(pagina, "() => { failures['PUT /api/candidato/perfil/reglas_radar'] = 422; }"):
        return
    card = tarjeta(pagina, TITULO)
    card.get_by_label("Envíos por día", exact=True).fill("0")
    guardar(pagina, TITULO)
    assert card.get_by_role("status").inner_text() != ""
    assert card.get_by_label("Envíos por día", exact=True).input_value() == "0"


def test_campo_vacio_viaja_tal_cual_para_que_el_servidor_lo_rechace(pagina):
    if not iniciar(pagina):
        return
    card = tarjeta(pagina, TITULO)
    card.get_by_label("Búsquedas por día (cada cuenta)", exact=True).fill("")
    guardar(pagina, TITULO)
    put = [c for c in mutaciones(pagina) if c["url"].endswith("/reglas_radar")][-1]
    assert json.loads(put["payload"]["contenido"])["busquedas_por_dia"] == ""


def test_cuentas_de_linkedin_a_1200(pagina):
    """Creador, 13/11: feed con la principal y búsquedas con la secundaria, elegibles en la tarjeta."""
    if not iniciar(pagina):
        return
    pagina.set_viewport_size({"width": 1200, "height": 900})
    card = tarjeta(pagina, TITULO)
    feed = card.get_by_label("Cuenta para el feed", exact=True)
    busquedas = card.get_by_label("Cuenta para las búsquedas", exact=True)
    assert feed.input_value() == "principal" and busquedas.input_value() == "secundaria"
    busquedas.select_option("principal")
    guardar(pagina, TITULO)
    nuevo = json.loads([c for c in mutaciones(pagina) if c["url"].endswith("/reglas_radar")][-1]["payload"]["contenido"])
    assert (nuevo["cuenta_feed"], nuevo["cuenta_busquedas"]) == ("principal", "principal")
    busquedas.select_option("")  # «Ninguna»: el campo se saca y no se busca
    guardar(pagina, TITULO)
    nuevo = json.loads([c for c in mutaciones(pagina) if c["url"].endswith("/reglas_radar")][-1]["payload"]["contenido"])
    assert "cuenta_busquedas" not in nuevo and nuevo["cuenta_feed"] == "principal"
    assert pagina.evaluate("() => document.documentElement.scrollWidth <= innerWidth")
    caja = busquedas.bounding_box()
    assert caja and caja["x"] >= 0 and caja["x"] + caja["width"] <= 1200
