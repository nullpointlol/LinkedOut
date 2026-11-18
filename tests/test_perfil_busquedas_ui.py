"""Mi perfil: tarjeta «Búsquedas del Radar» (12/11). Con lecturas, el promedio por lectura; sin lecturas, nada.

Chromium real con la API simulada de test_perfil_ui (sin puertos ni datos reales), a 375 y 1200 px.
"""
import json

import pytest

from test_perfil_navegacion import pagina  # noqa: F401  (fixture)
from test_perfil_ui import iniciar, tarjeta

TITULO = "Búsquedas del Radar"
DATOS = {
    "busquedas": [
        {"nombre": "Análisis", "activa": True,
         "consulta": '("cv a" OR "enviá tu cv") (funcional OR "business analyst" OR "product owner")',
         "resultados": {"lecturas": 2, "dias": 14, "promedio": {
             "leidos": 15.0, "nuevos": 2.5, "con_mail": 1.0, "ofertas": 1.0, "matches": 0.5,
             "descartados": 1.0, "no_oferta": 0.5}}},
        {"nombre": "Turno noche", "activa": False, "consulta": '"night shift" (Argentina OR LATAM)'},
    ],
    "errores": [],
}


def _preparar(datos):
    return f"() => {{ window.busquedas = {json.dumps(datos)}; }}"


def _item(page, nombre):
    return tarjeta(page, TITULO).locator(f"[data-busqueda='{nombre}']")


@pytest.mark.parametrize("ancho", [375, 1200])
def test_con_lecturas_muestra_el_promedio_y_sin_lecturas_nada(pagina, ancho):
    if not iniciar(pagina, _preparar(DATOS)):
        return
    pagina.set_viewport_size({"width": ancho, "height": 812})
    analisis = _item(pagina, "Análisis")
    assert analisis.count() == 1
    assert analisis.locator(".perfil-resultados").inner_text() == (
        "Promedio por lectura (2 lecturas, últimos 14 días): 15 leídos · 2,5 nuevos · 1 con mail · 1 oferta · "
        "0,5 matches · 1 descartado · 0,5 no eran oferta")
    noche = _item(pagina, "Turno noche")
    assert noche.locator(".perfil-resultados").count() == 0
    assert "Promedio" not in noche.inner_text()
    assert [linea for linea in noche.inner_text().split("\n") if linea.strip()] == ["Turno noche · pausada", '"night shift" (Argentina OR LATAM)']
    assert pagina.evaluate("() => document.documentElement.scrollWidth <= innerWidth")
    assert pagina.locator("#vista-perfil").evaluate("el => el.scrollWidth <= el.clientWidth")
    caja = analisis.bounding_box()
    assert caja and caja["x"] >= 0 and caja["x"] + caja["width"] <= ancho
    assert tarjeta(pagina, TITULO).get_by_role("button").count() == 0


def test_sin_busquedas_y_con_errores(pagina):
    datos = {"busquedas": [], "errores": ["«Mal»: tiene un operador en minúscula."]}
    if not iniciar(pagina, _preparar(datos)):
        return
    texto = tarjeta(pagina, TITULO).inner_text()
    assert "No hay búsquedas configuradas." in texto
    assert "«Mal»: tiene un operador en minúscula." in texto


def test_resultados_raros_no_se_muestran_y_no_hay_html(pagina):
    datos = {"busquedas": [
        {"nombre": "<img id=ataque onerror=1>", "activa": True, "consulta": "a",
         "resultados": {"lecturas": 0, "dias": 14, "promedio": {"leidos": 3}}},
        {"nombre": "b", "activa": True, "consulta": "b",
         "resultados": {"lecturas": 1, "dias": 14, "promedio": {"leidos": "mucho", "nuevos": 2}}},
    ], "errores": []}
    if not iniciar(pagina, _preparar(datos)):
        return
    assert pagina.locator("#ataque").count() == 0
    assert tarjeta(pagina, TITULO).locator(".perfil-resultados").all_inner_texts() == [
        "Promedio por lectura (1 lectura, últimos 14 días): 2 nuevos"]


def test_falla_la_carga_avisa_sin_numeros(pagina):
    if not iniciar(pagina, "() => { failures['GET /api/candidato/busquedas'] = 500; }"):
        return
    card = tarjeta(pagina, TITULO)
    assert card.get_by_role("status").inner_text() == "Falló el servidor."
    assert card.locator(".perfil-resultados").count() == 0
