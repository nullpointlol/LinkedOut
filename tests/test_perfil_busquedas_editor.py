"""Rediseño E6: las búsquedas del Radar se editan en Mi perfil › Qué busco › Búsquedas del Radar.

Cada búsqueda: nombre, activa, orden y la consulta en bloques «Contiene / y / sin» (términos con OR dentro de un
bloque, bloques con AND, «sin» = NOT), o una consulta libre. Se ve la consulta que va a ejecutar el Radar mientras se
edita (mismo armado que core/radar_consultas.armar_consulta) y se guarda con la barra única (PUT
/api/candidato/busquedas con la versión). Chromium real con la API simulada de test_perfil_ui.

Medidas: lo que se ve es lo del archivo; cada edición viaja tal cual (las claves que no se editan, también); el
armado de la consulta coincide con el del Radar; un 422 deja la edición y muestra el error; sin scroll horizontal.
"""
import json
import shutil
import subprocess

import pytest

from core import radar_consultas
from test_perfil_navegacion import pagina  # noqa: F401  (fixture)
from test_perfil_ui import iniciar, tarjeta, mutaciones, RAIZ

TITULO = "Búsquedas del Radar"
CRUDAS = [
    {"nombre": "Frontend", "activa": True, "bloques": [
        [{"texto": "buscamos", "exacta": True}, {"texto": "estamos buscando", "exacta": True}],
        [{"texto": "react"}, {"texto": "frontend"}],
        [{"texto": "pasantía", "excluir": True}]],
     "portales": ["jooble"], "palabras": "desarrolladora frontend", "extra": "fila"},
    {"nombre": "Backend", "activa": False, "consulta": "\"buscamos\" node"},
]
DATOS = {
    "busquedas": [dict(nombre=b["nombre"], activa=b["activa"], consulta=radar_consultas.armar_consulta(b),
                       portales=b.get("portales", []), palabras=b.get("palabras", "")) for b in CRUDAS],
    "errores": [], "portales": ["jooble", "indeed"], "version": "v1", "crudas": CRUDAS,
}
DATOS["busquedas"][0]["resultados"] = {"lecturas": 2, "dias": 14, "promedio": {"leidos": 15.0, "nuevos": 2.5}}


def _preparar(datos=DATOS, estado_put=200):
    # El PUT de la lista entera: se guarda lo enviado y vuelve con versión y respaldo (o con el error pedido)
    return f"""() => {{
        window.busquedas = {json.dumps(datos)};
        const original = window.fetch;
        window.fetch = async (url, options = {{}}) => {{
            if (url === '/api/candidato/busquedas' && options.method === 'PUT') {{
                const payload = JSON.parse(options.body);
                calls.push({{url, method: 'PUT', payload}});
                if ({estado_put} !== 200) return {{ok: false, status: {estado_put},
                    json: async () => ({{detail: 'Búsquedas inválidas: «Frontend»: el bloque 2 está vacío.'}})}};
                window.busquedas = {{...window.busquedas, crudas: payload.busquedas, version: 'v2'}};
                return {{ok: true, status: 200, json: async () => ({{ok: true, version: 'v2', backup: 'candidato-busq'}})}};
            }}
            return original(url, options);
        }};
    }}"""


def _item(page, nombre):
    return tarjeta(page, TITULO).locator(f"[data-busqueda='{nombre}']")


def _guardar(page):
    page.get_by_role("button", name="Guardar cambios", exact=True).click()
    page.wait_for_function("() => !document.querySelector('#vista-perfil fieldset.editada')", timeout=3000)
    return [c for c in mutaciones(page) if c["url"] == "/api/candidato/busquedas"][-1]["payload"]


@pytest.mark.parametrize("ancho", [360, 375, 1200])
def test_muestra_cada_busqueda_en_bloques_con_su_consulta(pagina, ancho):
    if not iniciar(pagina, _preparar()):
        return
    pagina.set_viewport_size({"width": ancho, "height": 812})
    front = _item(pagina, "Frontend")
    assert front.count() == 1
    assert front.get_by_label("Nombre de la búsqueda", exact=True).input_value() == "Frontend"
    assert front.get_by_label("Activa", exact=True).is_checked()
    bloques = front.locator(".perfil-bloque")
    assert bloques.count() == 3
    assert [b.locator(".perfil-conector").inner_text() for b in bloques.all()] == ["Contiene", "y", "y"]
    terminos = front.get_by_role("textbox", name="Término")
    assert [t.input_value() for t in terminos.all()] == ["buscamos", "estamos buscando", "react", "frontend", "pasantía"]
    assert [c.is_checked() for c in front.get_by_role("checkbox", name="Frase exacta").all()] == [True, True, False, False, False]
    assert [c.is_checked() for c in front.get_by_role("checkbox", name="Sin (excluir)").all()] == [False, False, False, False, True]
    assert front.locator(".perfil-consulta-previa code").inner_text() == DATOS["busquedas"][0]["consulta"]
    assert "Promedio por lectura (2 lecturas" in front.locator(".perfil-resultados").inner_text()
    assert front.get_by_label("Palabras para portales (sin comillas ni AND/OR)", exact=True).input_value() == "desarrolladora frontend"
    back = _item(pagina, "Backend")
    assert not back.get_by_label("Activa", exact=True).is_checked()
    assert back.get_by_label("Consulta libre (sintaxis de LinkedIn)", exact=True).input_value() == '"buscamos" node'
    assert pagina.evaluate("() => document.documentElement.scrollWidth <= innerWidth")
    for control in tarjeta(pagina, TITULO).locator("input, textarea, button").all():
        if control.is_visible():
            caja = control.bounding_box()
            assert caja and caja["x"] >= 0 and caja["x"] + caja["width"] <= ancho


def test_editar_bloques_orden_y_activa_y_guardar_con_la_barra(pagina):
    if not iniciar(pagina, _preparar()):
        return
    pagina.set_viewport_size({"width": 1200, "height": 900})
    front = tarjeta(pagina, TITULO).locator(".perfil-busqueda").nth(0)  # por posición: se renombra
    front.get_by_label("Nombre de la búsqueda", exact=True).fill("Front")
    bloque2 = front.locator(".perfil-bloque").nth(1)
    bloque2.get_by_role("button", name="Agregar término", exact=True).click()
    bloque2.get_by_role("textbox", name="Término").last.fill("ui developer")
    bloque2.get_by_role("checkbox", name="Frase exacta").last.check()
    front.locator(".perfil-bloque").nth(2).get_by_role("button", name="Quitar bloque", exact=True).click()
    assert front.locator(".perfil-consulta-previa code").inner_text() == \
        '("buscamos" OR "estamos buscando") (react OR frontend OR "ui developer")'
    back = tarjeta(pagina, TITULO).locator(".perfil-busqueda").nth(1)
    back.get_by_label("Activa", exact=True).check()
    back.get_by_role("button", name="Subir", exact=True).click()
    tarjeta(pagina, TITULO).get_by_role("button", name="Agregar búsqueda", exact=True).click()
    nueva = tarjeta(pagina, TITULO).locator(".perfil-busqueda").last
    nueva.get_by_label("Nombre de la búsqueda", exact=True).fill("Diseño")
    nueva.get_by_role("textbox", name="Término").first.fill("diseñadora ux")
    enviado = _guardar(pagina)
    assert enviado["version"] == "v1"
    assert [b["nombre"] for b in enviado["busquedas"]] == ["Backend", "Front", "Diseño"]
    assert enviado["busquedas"][0] == {"nombre": "Backend", "activa": True, "consulta": "\"buscamos\" node"}
    assert enviado["busquedas"][1] == {
        "nombre": "Front", "activa": True, "bloques": [
            [{"texto": "buscamos", "exacta": True}, {"texto": "estamos buscando", "exacta": True}],
            [{"texto": "react"}, {"texto": "frontend"}, {"texto": "ui developer", "exacta": True}]],
        "portales": ["jooble"], "palabras": "desarrolladora frontend", "extra": "fila"}
    assert enviado["busquedas"][2] == {"nombre": "Diseño", "activa": True, "bloques": [[{"texto": "diseñadora ux"}]]}
    assert "Guardado" in tarjeta(pagina, TITULO).get_by_role("status").inner_text()


def test_un_422_deja_la_edicion_y_muestra_el_error(pagina):
    if not iniciar(pagina, _preparar(estado_put=422)):
        return
    front = tarjeta(pagina, TITULO).locator(".perfil-busqueda").nth(0)  # por posición: se renombra
    front.get_by_label("Nombre de la búsqueda", exact=True).fill("Front editada")
    pagina.get_by_role("button", name="Guardar cambios", exact=True).click()
    pagina.wait_for_function("() => document.querySelector('#vista-perfil .perfil-busquedas [role=status]').textContent.includes('inválidas')")
    assert "el bloque 2 está vacío" in tarjeta(pagina, TITULO).get_by_role("status").inner_text()
    assert front.get_by_label("Nombre de la búsqueda", exact=True).input_value() == "Front editada"
    assert pagina.get_by_role("button", name="Guardar cambios", exact=True).is_visible()


def test_escribir_la_consulta_a_mano_y_quitar_una_busqueda(pagina):
    if not iniciar(pagina, _preparar()):
        return
    front = _item(pagina, "Frontend")
    front.get_by_role("button", name="Escribir la consulta a mano", exact=True).click()
    libre = front.get_by_label("Consulta libre (sintaxis de LinkedIn)", exact=True)
    assert libre.input_value() == DATOS["busquedas"][0]["consulta"]
    _item(pagina, "Backend").get_by_role("button", name="Quitar búsqueda", exact=True).click()
    enviado = _guardar(pagina)
    assert [b["nombre"] for b in enviado["busquedas"]] == ["Frontend"]
    assert enviado["busquedas"][0]["consulta"] == DATOS["busquedas"][0]["consulta"]
    assert "bloques" not in enviado["busquedas"][0] and enviado["busquedas"][0]["extra"] == "fila"


def test_descartar_vuelve_a_lo_guardado(pagina):
    if not iniciar(pagina, _preparar()):
        return
    pagina.evaluate("() => { window.confirm = () => true; }")
    _item(pagina, "Frontend").get_by_label("Nombre de la búsqueda", exact=True).fill("Otro nombre")
    pagina.get_by_role("region", name="Cambios sin guardar").get_by_role("button", name="Descartar", exact=True).click()
    pagina.wait_for_function("() => !document.querySelector('#vista-perfil fieldset.editada')")
    assert _item(pagina, "Frontend").get_by_label("Nombre de la búsqueda", exact=True).input_value() == "Frontend"


def test_nombres_y_terminos_con_html_quedan_como_texto(pagina):
    datos = dict(DATOS, crudas=[{"nombre": "<img id=ataque onerror=1>", "activa": True,
                                 "bloques": [[{"texto": "<b id=negrita>x</b>"}]]}])
    if not iniciar(pagina, _preparar(datos)):
        return
    assert pagina.locator("#ataque").count() == 0 and pagina.locator("#negrita").count() == 0
    assert tarjeta(pagina, TITULO).get_by_label("Nombre de la búsqueda", exact=True).input_value() == "<img id=ataque onerror=1>"


CASOS_ARMADO = [
    {"bloques": [[{"texto": "cv a", "exacta": True}, {"texto": "enviar cv", "exacta": True}],
                 [{"texto": "funcional"}, {"texto": "business analyst", "exacta": True}],
                 [{"texto": "senior", "excluir": True}]]},
    {"bloques": [[{"texto": "diseñadora  ux"}], [{"texto": "remoto"}, {"texto": "home office"}]]},
    {"bloques": [[{"texto": "react"}, {"texto": "no quiero esto", "excluir": True}]]},
    {"consulta": "  \"cv a\"   funcional  "},
    {"consulta": "x", "bloques": [[{"texto": "y"}]]},
]


@pytest.mark.skipif(shutil.which("node") is None, reason="node no está instalado")
def test_el_armado_de_la_consulta_es_el_mismo_que_el_del_radar():
    script = f"""
      global.window = global; global.document = {{ addEventListener() {{}}, getElementById() {{ return null; }} }};
      require({json.dumps(str(RAIZ / "static" / "perfil.js"))});
      console.log(JSON.stringify({json.dumps(CASOS_ARMADO)}.map(b => window.Perfil._armarConsulta(b))));
    """
    salida = subprocess.run(["node", "-e", script], capture_output=True, text=True, encoding="utf-8", check=True)
    assert json.loads(salida.stdout.strip().splitlines()[-1]) == [radar_consultas.armar_consulta(b) for b in CASOS_ARMADO]
