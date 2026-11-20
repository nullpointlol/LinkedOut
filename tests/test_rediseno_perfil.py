"""Rediseño E3, Mi perfil.

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
    pagina.get_by_role("region", name="Cambios sin guardar").get_by_role("button", name="Descartar", exact=True).click()
    pagina.wait_for_function("() => document.querySelector('#vista-perfil .perfil-barra').hidden", timeout=3000)
    assert tarjeta(pagina, "Experiencia").get_by_role("textbox").input_value() != "Se descarta"
    assert not mutaciones(pagina)


def test_ningun_css_propio_usa_has():
    """:has() encarecía la invalidación de estilos con las 2760 tarjetas del Radar (+500 ms en el test de la base
    grande). Los CSS del rediseño no lo usan."""
    for css in (RAIZ / "static" / "css").glob("*.css"):
        texto = re.sub(r"/\*.*?\*/", "", css.read_text(encoding="utf-8"), flags=re.S)
        assert ":has(" not in texto, css.name


# ---------------------------------------------------------------- E3c: experiencia sin perder el formato del motor

import json  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402

import pytest  # noqa: E402

requiere_node = pytest.mark.skipif(shutil.which("node") is None, reason="node no está instalado")
CASOS_RAROS = [
    "",
    "\n",
    "# Experiencia\r\n\r\n## Empresa\r\n### Cargo | 2020 - 2021\r\n• Viñeta\r\n",  # CRLF
    "## Sin salto final\n### Cargo | 2019 - Presente\n• Última viñeta",
    "Texto antes de la primera empresa\n\n## Empresa  \n###  Raro|2020-2021\n- guion en vez de viñeta\n•sin espacio\n",
    "<!-- comentario\n     de dos líneas -->\n## Empresa\n\n\n### Cargo | 2018 - 2020\n• Una\n\n• Dos con espacio final \n",
    "## HABILIDADES TÉCNICAS\n• **Grupo:** uno, dos.\n## OTRA SECCIÓN RARA\nlínea libre\n",
]


def _node_exp(cuerpo: str):
    script = ("global.window = {}; global.document = {};\n"
              f"require({json.dumps(str(PERFIL_JS))});\n"
              "const E = window.Perfil._experiencia;\n" + cuerpo)
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True, encoding="utf-8", check=True)
    return json.loads(r.stdout)


@requiere_node
@pytest.mark.parametrize("archivo", [RAIZ / "config/ejemplo/candidato/experiencia.txt",
                                     RAIZ / "tests/fixtures/candidato/experiencia.txt"])
def test_experiencia_ida_y_vuelta_byte_a_byte_con_archivos_reales(archivo):
    texto = archivo.read_bytes().decode("utf-8")
    assert _node_exp(f"console.log(JSON.stringify(E.serializar(E.leer({json.dumps(texto)}))));") == texto


@requiere_node
@pytest.mark.parametrize("texto", CASOS_RAROS)
def test_experiencia_ida_y_vuelta_byte_a_byte_con_casos_raros(texto):
    assert _node_exp(f"console.log(JSON.stringify(E.serializar(E.leer({json.dumps(texto)}))));") == texto


@requiere_node
def test_experiencia_estructura_y_edicion_de_un_cargo_cambia_solo_su_linea():
    texto = (RAIZ / "config/ejemplo/candidato/experiencia.txt").read_text(encoding="utf-8")
    out = _node_exp(f"""
const m = E.leer({json.dumps(texto)});
const secciones = E.secciones(m);
const resumen = secciones.map(s => [s.nombre, s.cargos.length]);
const cargo = secciones[0].cargos[0];
cargo.nodo.titulo = 'Desarrolladora Frontend Sr.';
console.log(JSON.stringify({{resumen, nuevo: E.serializar(m)}}));
""")
    assert out["resumen"][:2] == [["Estudio Ejemplo", 2], ["Agencia Demo", 1]]
    viejo, nuevo = texto.splitlines(), out["nuevo"].splitlines()
    distintas = [(a, b) for a, b in zip(viejo, nuevo) if a != b]
    assert distintas == [("### Desarrolladora Frontend | 2021 - 2025", "### Desarrolladora Frontend Sr. | 2021 - 2025")]


@requiere_node
def test_texto_con_fines_de_linea_mezclados_va_a_modo_texto():
    mezclado, parejo = "## A\r\n### B | 2020 - 2021\n", "## A\n### B | 2020 - 2021\n"
    assert _node_exp(f"console.log(JSON.stringify(E.seguro({json.dumps(mezclado)})));") is False
    assert _node_exp(f"console.log(JSON.stringify(E.seguro({json.dumps(parejo)})));") is True


# ---------------------------------------------------------------- E3c: editor de Experiencia en la página

EJEMPLO_EXP = (RAIZ / "config/ejemplo/candidato/experiencia.txt").read_text(encoding="utf-8")


def _iniciar_con_experiencia(pagina, texto):
    return iniciar(pagina, "() => { contents.experiencia = " + json.dumps(texto) + "; }")


def test_editor_de_experiencia_edita_un_cargo_y_guarda_el_resto_intacto(pagina):
    if not _iniciar_con_experiencia(pagina, EJEMPLO_EXP):
        return
    card = tarjeta(pagina, "Experiencia")
    assert card.get_by_label("Empresa", exact=True).first.input_value() == "Estudio Ejemplo"
    card.get_by_label("Cargo", exact=True).first.fill("Desarrolladora Frontend Sr.")
    pagina.get_by_role("button", name="Guardar cambios", exact=True).click()
    pagina.wait_for_function("() => calls.some(c => c.method === 'PUT')", timeout=3000)
    guardado = mutaciones(pagina)[0]["payload"]["contenido"]
    esperado = EJEMPLO_EXP.replace("### Desarrolladora Frontend | 2021 - 2025", "### Desarrolladora Frontend Sr. | 2021 - 2025", 1)
    assert guardado == esperado


def test_editor_agrega_y_quita_vinetas_y_pasa_a_texto_sin_perder_nada(pagina):
    if not _iniciar_con_experiencia(pagina, EJEMPLO_EXP):
        return
    card = tarjeta(pagina, "Experiencia")
    antes = card.get_by_label("Viñeta", exact=True).count()
    card.get_by_role("button", name="Agregar viñeta", exact=True).first.click()
    assert card.get_by_label("Viñeta", exact=True).count() == antes + 1
    card.get_by_role("button", name="Quitar viñeta", exact=True).first.click()
    assert card.get_by_label("Viñeta", exact=True).count() == antes
    card.get_by_role("button", name="Editar como texto", exact=True).click()
    texto = card.get_by_role("textbox").input_value()
    # Agregar una vacía al primer cargo y quitar su primera viñeta: el resto del archivo queda igual
    assert texto.count("\n") == EJEMPLO_EXP.count("\n") and "## HABILIDADES TÉCNICAS" in texto
    card.get_by_role("button", name="Volver al editor", exact=True).click()
    assert card.get_by_label("Empresa", exact=True).count() >= 2


def test_archivo_con_fines_de_linea_mezclados_abre_como_texto_con_aviso(pagina):
    if not _iniciar_con_experiencia(pagina, "## Empresa\r\n### Cargo | 2020 - 2021\n• Viñeta\n"):
        return
    card = tarjeta(pagina, "Experiencia")
    assert card.get_by_role("textbox").count() == 1
    assert "no reconoce entero" in card.inner_text()
    assert card.get_by_role("button", name="Volver al editor", exact=True).count() == 0


# ---------------------------------------------------------------- E3c: datos personales sin perder el formato

@requiere_node
@pytest.mark.parametrize("archivo", [RAIZ / "config/ejemplo/candidato/personales.txt",
                                     RAIZ / "tests/fixtures/candidato/personales.txt"])
def test_personales_ida_y_vuelta_byte_a_byte(archivo):
    texto = archivo.read_bytes().decode("utf-8")
    out = _node_exp("const P = window.Perfil._personales;\n"
                    f"console.log(JSON.stringify(P.serializar(P.leer({json.dumps(texto)}))));")
    assert out == texto


@requiere_node
@pytest.mark.parametrize("texto", ["", "Solo un nombre", "Nombre\r\nPuesto: X\r\n\r\nCiudad | a@b.test\r\n",
                                   "Nombre\nPuesto: X\nOtra línea libre\n\nEDUCACIÓN\n• Título\nlibre\n"])
def test_personales_ida_y_vuelta_con_casos_raros(texto):
    out = _node_exp("const P = window.Perfil._personales;\n"
                    f"console.log(JSON.stringify(P.serializar(P.leer({json.dumps(texto)}))));")
    assert out == texto


@requiere_node
def test_personales_campos_y_edicion_del_titular_cambia_solo_su_linea():
    texto = (RAIZ / "config/ejemplo/candidato/personales.txt").read_text(encoding="utf-8")
    out = _node_exp("const P = window.Perfil._personales;\n" f"""
const m = P.leer({json.dumps(texto)});
const c = P.campos(m);
c.puesto.texto = 'Desarrolladora Web Full Stack';
console.log(JSON.stringify({{nombre: c.nombre.texto, contacto: c.contacto.partes.map(p => p.tipo),
  educacion: c.secciones.EDUCACIÓN.length, idiomas: c.secciones.IDIOMAS.length, nuevo: P.serializar(m)}}));
""")
    assert out["nombre"] == "Sam Ortega"
    assert out["contacto"] == ["ciudad", "telefono", "whatsapp", "email", "linkedin"]
    assert (out["educacion"], out["idiomas"]) == (1, 2)
    distintas = [(a, b) for a, b in zip(texto.splitlines(), out["nuevo"].splitlines()) if a != b]
    assert distintas == [("Puesto: Desarrolladora Web", "Puesto: Desarrolladora Web Full Stack")]


def test_formulario_de_datos_personales_guarda_el_resto_intacto(pagina):
    texto = (RAIZ / "config/ejemplo/candidato/personales.txt").read_text(encoding="utf-8")
    if not iniciar(pagina, "() => { contents.personales = " + json.dumps(texto) + "; }"):
        return
    card = tarjeta(pagina, "Datos personales")
    assert card.get_by_label("Nombre completo", exact=True).input_value() == "Sam Ortega"
    card.get_by_label("Titular", exact=True).fill("Desarrolladora Web Full Stack")
    card.get_by_label("Email", exact=True).fill("sam@example.test")
    pagina.get_by_role("button", name="Guardar cambios", exact=True).click()
    pagina.wait_for_function("() => calls.some(c => c.method === 'PUT')", timeout=3000)
    guardado = mutaciones(pagina)[0]["payload"]["contenido"]
    assert guardado == texto.replace("Puesto: Desarrolladora Web\n", "Puesto: Desarrolladora Web Full Stack\n").replace(
        "sam.ortega@example.com", "sam@example.test")


# ---------------------------------------------------------------- E3d: ubicación de cada portal en Qué busco

def test_ubicacion_de_cada_portal_se_edita_en_que_busco_y_se_guarda_con_portales(pagina):
    preparar = "() => { contents.reglas_portales = JSON.stringify({jooble: {ubicacion: 'Antes'}}); versions.reglas_portales = 'v1'; }"
    if not iniciar(pagina, preparar):
        return
    bloque = pagina.locator("#perfil-busco .perfil-ubicaciones")
    assert bloque.count() == 1
    campo = pagina.get_by_label("Jooble: Ubicación", exact=True)
    assert campo.count() == 1 and bloque.locator("input").count() >= 1
    assert campo.evaluate("el => !!el.closest('#perfil-busco')")
    assert tarjeta(pagina, "Portales").get_by_label("Jooble: Ubicación", exact=True).count() == 0
    campo.fill("Ciudad Ejemplo")
    assert "Portales" in pagina.locator("#vista-perfil .perfil-barra").inner_text()
    pagina.get_by_role("button", name="Guardar cambios", exact=True).click()
    pagina.wait_for_function("() => calls.some(c => c.method === 'PUT' && c.url.endsWith('/reglas_portales'))", timeout=3000)
    dato = json.loads(mutaciones(pagina)[-1]["payload"]["contenido"])
    assert dato["jooble"]["ubicacion"] == "Ciudad Ejemplo"


# ---------------------------------------------------------------- E3e: resumen en el índice

def test_indice_resume_cada_seccion_con_los_datos_cargados(pagina):
    preparar = ("() => { contents.experiencia = " + json.dumps(EJEMPLO_EXP) + "; contents.personales = " + json.dumps("Sam Ortega" + chr(10) + "Puesto: X" + chr(10)) + "; }")
    if not iniciar(pagina, preparar):
        return
    pagina.set_viewport_size({"width": 1200, "height": 900})
    res = pagina.locator('.perfil-ir[data-seccion="datos"] .perfil-ir-res').inner_text()
    assert res == "Sam Ortega · 2 empresas · 3 cargos"
    # Se actualiza con los cambios guardados
    pagina.locator('.perfil-ir[data-seccion="cv"] .perfil-ir-res').wait_for()


# ---------------------------------------------------------------- E3e: Cargos en el CV con selector

def test_cargos_en_el_cv_se_elige_con_selector_y_conserva_valores_desconocidos(pagina):
    dato = {"cargos_relevantes_entre": 10, "empresas": [
        {"empresa": "Estudio Ejemplo", "cargos": "todos"}, {"empresa": "Agencia Demo", "cargos": "valor_futuro"}]}
    if not iniciar(pagina, "() => { contents.estructura_cv = " + json.dumps(json.dumps(dato)) + "; versions.estructura_cv = 'v1'; }"):
        return
    card = tarjeta(pagina, "Cargos en el CV")
    selectores = card.get_by_label("Cargos en el CV", exact=True)
    assert selectores.count() == 2
    assert selectores.nth(1).input_value() == "valor_futuro"  # no se pierde
    selectores.nth(0).select_option("segun_oferta")
    pagina.get_by_role("button", name="Guardar cambios", exact=True).click()
    pagina.wait_for_function("() => calls.some(c => c.method === 'PUT' && c.url.endsWith('/estructura_cv'))", timeout=3000)
    guardado = json.loads(mutaciones(pagina)[-1]["payload"]["contenido"])
    assert [e["cargos"] for e in guardado["empresas"]] == ["segun_oferta", "valor_futuro"]


# ---------------------------------------------------------------- E3e: listas como chips

def test_listas_como_chips_agregan_con_enter_quitan_y_guardan(pagina):
    dato = {"conocimientos": ["Kotlin", "Swift"], "nota": "conservar"}
    if not iniciar(pagina, "() => { contents.conocimientos_ausentes = " + json.dumps(json.dumps(dato)) + "; versions.conocimientos_ausentes = 'v1'; }"):
        return
    card = tarjeta(pagina, "Lo que no sabés")
    grupo = card.get_by_role("group", name="Lista: Conocimientos que no tenés (uno por renglón)", exact=True)
    assert [c.inner_text().replace("×", "").strip() for c in grupo.locator(".tag").all()] == ["Kotlin", "Swift"]
    entrada = card.get_by_label("Conocimientos que no tenés (uno por renglón)", exact=True)
    entrada.fill("Kubernetes")
    entrada.press("Enter")
    assert grupo.locator(".tag").count() == 3 and entrada.input_value() == ""
    grupo.get_by_role("button", name="Quitar Swift", exact=True).click()
    pagina.get_by_role("button", name="Guardar cambios", exact=True).click()
    pagina.wait_for_function("() => calls.some(c => c.method === 'PUT' && c.url.endsWith('/conocimientos_ausentes'))", timeout=3000)
    assert json.loads(mutaciones(pagina)[-1]["payload"]["contenido"]) == {"conocimientos": ["Kotlin", "Kubernetes"], "nota": "conservar"}


def test_pausas_del_radar_como_desde_a_hasta(pagina):
    """§6: cada pausa es una fila «desde [x] a [y] min»; cada número conserva su nombre accesible y se guarda igual."""
    if not iniciar(pagina):
        return
    card = tarjeta(pagina, "Radar de LinkedIn")
    for nombre, desde, hasta in (("Pausa entre lecturas", "3", "6"), ("Pausa entre vueltas", "25", "35")):
        rango = card.get_by_role("group", name=f"{nombre} (minutos)", exact=True)
        assert rango.count() == 1, nombre
        numeros = rango.get_by_role("spinbutton")
        assert numeros.count() == 2
        assert numeros.nth(0).input_value() == desde and numeros.nth(1).input_value() == hasta
        assert re.sub(r"\s+", " ", rango.inner_text()).strip() == f"{nombre} (minutos) desde a min"
        # Sin las cuatro etiquetas sueltas de antes
        assert card.locator("label", has_text=f"{nombre}: desde").count() == 0
        caja_desde, caja_hasta = numeros.nth(0).bounding_box(), numeros.nth(1).bounding_box()
        assert abs(caja_desde["y"] - caja_hasta["y"]) < 2, "desde y hasta en la misma línea"
    card.get_by_label("Pausa entre lecturas: hasta (min)", exact=True).fill("9")
    pagina.get_by_role("button", name="Guardar cambios").click()
    pagina.wait_for_function("() => !document.querySelector('#vista-perfil fieldset.editada')")
    put = [c for c in mutaciones(pagina) if c["url"].endswith("/reglas_radar")][-1]
    nuevo = json.loads(put["payload"]["contenido"])
    assert nuevo["pausa_entre_lecturas_min"] == {"desde": 3, "hasta": 9}
    assert nuevo["pausa_entre_vueltas_min"] == {"desde": 25, "hasta": 35}


def test_stack_en_tres_tarjetas_que_se_guardan_juntas(pagina):
    """§6: Lo que sé › Habilidades que tenés, Habilidades transferibles y Habilidades del CV de respaldo
    (un solo archivo, stack.json, con su versión y su respaldo)."""
    if not iniciar(pagina):
        return
    sabe = pagina.locator("#perfil-sabe")
    titulos = sabe.locator("fieldset > legend, section > h3").all_inner_texts()
    orden = ["Habilidades que tenés", "Habilidades transferibles", "Habilidades del CV de respaldo", "Lo que no sabés"]
    assert [x for x in titulos if x in orden] == orden
    tenes = tarjeta(pagina, "Habilidades que tenés")
    transferibles = pagina.get_by_role("region", name="Habilidades transferibles", exact=True)
    respaldo = pagina.get_by_role("region", name="Habilidades del CV de respaldo", exact=True)
    assert tenes.get_by_role("button", name="Agregar coincidencia", exact=True).count() == 1
    assert tenes.get_by_role("button", name="Agregar transferible", exact=True).count() == 0
    assert tenes.get_by_label("Técnicas (una por renglón)", exact=True).count() == 0
    transferibles.get_by_role("button", name="Agregar transferible", exact=True).click()
    transferibles.get_by_label("Nombre", exact=True).last.fill("Acuarela")
    assert tenes.evaluate("el => el.classList.contains('editada')")
    respaldo.get_by_label("Técnicas (una por renglón)", exact=True).fill("Óleo\n")
    pagina.get_by_role("button", name="Guardar cambios", exact=True).click()
    pagina.wait_for_function("() => !document.querySelector('#vista-perfil fieldset.editada')")
    puts = [c for c in mutaciones(pagina) if c["url"].endswith("/stack")]
    assert len(puts) == 1
    nuevo = json.loads(puts[0]["payload"]["contenido"])
    assert nuevo["transferibles"][-1]["nombre"] == "Acuarela"
    assert nuevo["habilidades_base"]["tecnicas"][-1] == "Óleo"



def test_celular_indice_primero_entra_a_una_seccion_y_vuelve(pagina):
    """En el celular Mi perfil abre en el índice; cada fila entra a su
    sección sola, con «‹ Mi perfil» para volver. En escritorio, índice y secciones a la vez (sin cambios)."""
    if not iniciar(pagina):
        return
    pagina.set_viewport_size({"width": 360, "height": 812})
    indice = pagina.locator(".perfil-indice")
    assert indice.is_visible()
    assert not pagina.locator("#perfil-datos").is_visible() and not pagina.locator("#perfil-sabe").is_visible()
    volver = pagina.get_by_role("button", name="Volver a Mi perfil", exact=True)
    assert not volver.is_visible()
    # Cada fila del índice muestra su resumen y lleva a su sección
    fila = pagina.locator(".perfil-ir[data-seccion=sabe]")
    assert fila.locator(".perfil-ir-res").is_visible() and fila.locator(".perfil-ir-res").inner_text() != ""
    fila.click()
    assert pagina.locator("#perfil-sabe").is_visible() and not indice.is_visible()
    assert not pagina.locator("#perfil-datos").is_visible()
    assert tarjeta(pagina, "Habilidades que tenés").is_visible()
    assert pagina.evaluate("() => scrollY + document.querySelector('#vista-perfil').scrollTop") < 5
    # Un cambio sigue marcado en el índice al volver, y la barra de guardado queda visible
    pagina.get_by_label("Técnicas (una por renglón)", exact=True).fill("Óleo\n")
    volver.click()
    assert indice.is_visible() and not pagina.locator("#perfil-sabe").is_visible()
    assert "con-cambios" in fila.get_attribute("class")
    assert pagina.get_by_role("button", name="Guardar cambios", exact=True).is_visible()
    assert pagina.evaluate("() => document.documentElement.scrollWidth <= innerWidth")
    # Escritorio: índice y todas las secciones a la vez, sin «‹ Mi perfil»
    pagina.set_viewport_size({"width": 1200, "height": 900})
    assert indice.is_visible() and pagina.locator("#perfil-datos").is_visible() and pagina.locator("#perfil-sabe").is_visible()
    assert not volver.is_visible()
