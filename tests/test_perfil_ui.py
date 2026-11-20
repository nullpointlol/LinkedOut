"""M07: formularios reales en Chromium, API simulada, sin puertos ni datos reales.

Plan: rojo DOM → formularios/estados → errores/concurrencia → QA 360/375/1200.
Las mutaciones que cubrimos son pérdida de extras, versión vieja, éxito sin
respaldo, doble escritura, HTML ejecutable y regeneración sin confirmación.
"""
import json
import os
from pathlib import Path

import pytest

from test_perfil_navegacion import pagina, comprobar_header, comprobar_vista


RAIZ = Path(__file__).resolve().parents[1]
AVISO = "Los años por área se recalculan con IA en el próximo CV."
FAKE = r"""() => {
    window.calls = []; window.failures = {}; window.holds = {};
    window.confirmations = []; window.accept = false;
    window.confirm = text => { confirmations.push(text); return accept; };
    window.contents = {
        experiencia: 'Experiencia <img id="ataque" onerror="window.ataque=1">\nFin',
        personales: 'Nombre: Persona de prueba', voz_email: 'Hola, buenas tardes.',
        restricciones: JSON.stringify({base:'CABA', presencial_hibrido_permitido:'CABA y Norte',
            remoto_desde:'Argentina', reubicacion:false,
            edad:{actual:30,al:'2026-01-01',extra:'conservar'}, nota:'privada', otro:{x:1}}),
        anios_por_area_creador: JSON.stringify({nota:'no borrar',extra:{a:1},
            anios_por_area:[{area:'Sistemas',anios:'5',reemplaza:['QA','Soporte'],fuente:'creador'}]}),
        reglas_radar: JSON.stringify({busquedas_por_hora:6,busquedas_por_dia:60,
            porcentaje_si_las_dos_cuentas_se_pausaron:67,pausa_entre_lecturas_min:{desde:3,hasta:6},
            pausa_entre_vueltas_min:{desde:25,hasta:35},lectura_busqueda:{posts:300,scrolls:15,pantallas_sin_nuevos:2},
            lectura_feed:{posts:80,scrolls:20},pausa_bloqueo_horas:{primera:6,maxima:24},tope_envios_dia:40,
            cuenta_feed:'principal',cuenta_busquedas:'secundaria'}),
        roles_objetivo: JSON.stringify({nota:'conservar',roles_objetivo:['domador de leones','=equilibrista'],
            areas:[{area:'circo',titulos:['domador'],sinonimos:['\\bpista\\b'],perfil_actual:true,extra:'fila'}]}),
        stack: JSON.stringify({nota:'conservar',coincidencias:[{nombre:'Cobol',palabras:['cobol'],
            justificacion:'Lotes en Cobol',extra:'fila'}],transferibles:[],
            habilidades_base:{gestion:['Kanban'],tecnicas:['Cobol'],extra:'base'}}),
        conocimientos_ausentes: JSON.stringify({conocimientos:['Trapecio'],nota:'conservar'})
    };
    window.versions = Object.fromEntries(Object.keys(contents).map(k => [k, 'v1-'+k]));
    window.learning = {'ci/cd %? á':{tag:'CI <b>CD</b>',estado:'TRANSFERIBLE'},
        otro:{tag:'Otro',estado:'MATCH_DIRECTO'}};
    window.radar = [{id:1,estado:'LISTO',aprobado_at:null},
        {id:2,estado:'LISTO',aprobado_at:'fecha'}, {id:5,estado:'ERROR',aprobado_at:null}];
    window.manual = [{id:1,estado:'LISTO',aprobado_at:null},
        {id:3,estado:'LISTO',aprobado_at:null}];
    window.preview = {areas:[],vigente:false};
    window.fetch = async (url, options={}) => {
        const method = options.method || 'GET';
        const request = {url,method,headers:options.headers,
            payload:options.body ? JSON.parse(options.body) : null};
        calls.push(request);
        const key = method+' '+url;
        if (holds[key]) await new Promise(resolve => { holds[key] = resolve; });
        if (failures[key] === 'network') throw new Error('sin red');
        let status = failures[key] || 200, data;
        if (status !== 200) data = {detail: status === 409 ?
            'El archivo cambió desde que lo abriste. Recargá antes de guardar.' :
            status === 422 ? 'Restricciones inválidas: prueba.' : 'Falló el servidor.'};
        else if (url.startsWith('/api/candidato/perfil/')) {
            const field = url.split('/').pop();
            if (method === 'GET') data = {clave:field,archivo:field,contenido:contents[field],
                version:versions[field],existe:contents[field] !== ''};
            else {
                contents[field] = request.payload.contenido;
                versions[field] += '-nueva';
                data = {ok:true,version:versions[field],backup:'candidato-prueba',
                    aviso:['experiencia','personales'].includes(field) ?
                        'Los años por área se recalculan con IA en el próximo CV.' : null};
                if (window.withoutBackup) delete data.backup;
            }
        } else if (url === '/api/candidato/anios-por-area') data = preview;
        else if (url === '/api/candidato/busquedas') data = window.busquedas || {busquedas:[],errores:[]};
        else if (method === 'DELETE') {
            delete learning[decodeURIComponent(url.split('/').pop())];
            data = {ok:true,backup:'candidato-borrado'};
        } else if (url === '/api/candidato/aprendizajes') data = {aprendizajes:learning};
        else if (url === '/api/radar/posts?estado=LISTO') data = radar;
        else if (url === '/api/radar/posts?estado=LISTO&fuente=manual') data = manual;
        else if (url === '/api/radar/regenerar-listos') data = {regenerados:1,ids:[1]};
        else throw new Error('URL inesperada: '+url);
        return {ok:status===200,status,json:async()=>structuredClone(data)};
    };
}"""


def iniciar(page, preparar=None):
    # invariante: M06 aprobada. Shell real y transiciones antes de cargar el módulo
    # nuevo (ausente en la base). Header medido por M06: título 73.03125px a 360.
    for vista in ("manual", "radar", "editor"):
        comprobar_vista(page, vista)
    for ancho in (360, 375, 1200):
        comprobar_header(page, ancho, perfil=True)
    if os.environ.get("INVARIANT_CHECK") == "1":
        return False
    fuente = RAIZ / "static/perfil.js"
    assert fuente.exists(), "Falta implementar el módulo de tarjetas del perfil"
    page.evaluate(FAKE)
    if preparar:
        page.evaluate(preparar)
    page.add_script_tag(content=fuente.read_text(encoding="utf-8"))
    page.evaluate("() => { document.getElementById('vista-perfil').hidden=false; }")
    page.evaluate("async () => { await Perfil.mostrar(); }")
    return True


def tarjeta(page, titulo):
    # Una sección cerrada (display: none) no expone sus roles: se busca incluyendo lo oculto para saber a cuál entrar
    oculta = page.get_by_role("group", name=titulo, exact=True, include_hidden=True)
    if oculta.count() == 1:
        entrar(page, oculta)
    return page.get_by_role("group", name=titulo, exact=True)


def entrar(page, elemento):
    """Rediseño E3 (celular): Mi perfil abre en el índice; se entra a la sección del elemento desde su fila del
    índice, como una persona («‹ Mi perfil» primero si hay otra abierta). En escritorio no hace nada."""
    seccion = elemento.evaluate("el => (el.closest('.perfil-seccion') || {}).id || ''")
    if not seccion or page.locator(f"#{seccion}").is_visible():
        return
    volver = page.get_by_role("button", name="Volver a Mi perfil", exact=True)
    if volver.is_visible():
        volver.click()
    page.locator(f".perfil-ir[data-seccion={seccion.removeprefix('perfil-')}]").click()
    assert page.locator(f"#{seccion}").is_visible(), seccion


def guardar(page, titulo):
    # Rediseño E3: un solo «Guardar cambios» en la barra; guarda cada tarjeta con cambios, en orden.
    # «Guardar tal cual» (sin editar): un input sin cambiar el valor marca la tarjeta, como un cambio.
    card = tarjeta(page, titulo)
    assert card.count() == 1
    if not card.evaluate("el => el.classList.contains('editada')"):
        card.locator("input:not([type=checkbox]), textarea").first.dispatch_event("input")
    page.get_by_role("button", name="Guardar cambios", exact=True).click()
    page.wait_for_function("() => ![...document.querySelectorAll('fieldset')].some(f => f.getAttribute('aria-busy') === 'true')", timeout=3000)


def vaciar_lista(page, etiqueta):
    """Rediseño E3: las listas son chips; escribir agrega, así que para reemplazar primero se quitan los chips."""
    grupo = page.get_by_role("group", name=f"Lista: {etiqueta}", exact=True).first
    while grupo.get_by_role("button").count():
        grupo.get_by_role("button").first.click()


def mutaciones(page, method="PUT"):
    return page.evaluate("m => calls.filter(c => c.method===m)", method)


def test_carga_seis_tarjetas_y_textos(pagina):
    if not iniciar(pagina):
        return
    for nombre in ("Experiencia", "Datos personales", "Voz del mail", "Dónde y cómo trabajás",
                   "Años de experiencia por área", "Lo que marcaste en las ofertas"):
        card = tarjeta(pagina, nombre)
        assert card.count() == 1
        assert card.get_by_role("status").count() == 1
        assert card.get_by_role("button", name="Guardar", exact=True).count() == 0  # rediseño E3: barra única
    assert pagina.get_by_text("Los cambios rigen para los próximos CV. Los CV ya generados no cambian.", exact=True).count() == 1
    assert pagina.get_by_role("button", name="Regenerar CV listos sin aprobar", exact=True).count() == 1
    assert pagina.get_by_text("Se calculan con IA en el próximo CV.", exact=True).count() == 1
    assert pagina.get_by_text("Años por área que se usan hoy", exact=True).count() == 1
    for label in ("Base", "Presencial o híbrido en", "Remoto desde", "Acepta viajes frecuentes o al interior",
                  "Acepta reubicación", "Postularte aunque pidan experiencia en un rubro o función que no tenés", "Edad", "Edad al",
                  "Área", "Años", "Reemplaza"):
        assert pagina.get_by_label(label, exact=True).count() == 1
    assert tarjeta(pagina, "Experiencia").get_by_role("textbox").input_value().endswith("\nFin")
    assert "· Transferible" in tarjeta(pagina, "Lo que marcaste en las ofertas").inner_text()
    assert pagina.locator("#ataque").count() == 0


def test_estado_aprendizaje_en_castellano(pagina):
    if not iniciar(pagina, """() => { learning = {
        sql: {tag:'SQL', estado:'MATCH_DIRECTO'},
        soporte: {tag:'Soporte', estado:'TRANSFERIBLE'},
        ingles: {tag:'Inglés', estado:'BRECHA_REAL'},
        otro: {tag:'Otro', estado:'DESCONOCIDO'},
        vacio: {tag:'Vacío', estado:''},
        ausente: {tag:'Sin estado'}
    }; }"""):
        return
    card = tarjeta(pagina, "Lo que marcaste en las ofertas")
    assert card.locator(".perfil-aprendizaje > span").all_text_contents() == [
        "SQL · Match", "Soporte · Transferible", "Inglés · Brecha",
        "Otro · DESCONOCIDO", "Vacío", "Sin estado"]
    assert "MATCH_DIRECTO" not in card.inner_text()


def test_guardado_version_estado_preview(pagina):
    if not iniciar(pagina):
        return
    card = tarjeta(pagina, "Experiencia")
    card.get_by_role("textbox").fill("Nuevo perfil")
    pagina.evaluate("() => { preview={areas:[{area:'Sistemas',anios:'6'}],vigente:true}; }")
    guardar(pagina, "Experiencia")
    assert card.get_by_role("status").inner_text() == "Guardado. Copia de respaldo: candidato-prueba. " + AVISO
    pagina.wait_for_function("() => document.getElementById('vista-perfil').textContent.includes('Sistemas: 6')", timeout=3000)
    card.get_by_role("textbox").fill("Segundo perfil")
    guardar(pagina, "Experiencia")
    requests = mutaciones(pagina)
    assert [c["payload"] for c in requests] == [
        {"contenido":"Nuevo perfil", "version":"v1-experiencia"},
        {"contenido":"Segundo perfil", "version":"v1-experiencia-nueva"}]
    assert all(c["headers"] == {"Content-Type":"application/json"} for c in requests)
    assert not mutaciones(pagina, "POST")
    assert pagina.evaluate("() => calls.filter(c => c.url==='/api/candidato/anios-por-area').length") == 3


@pytest.mark.parametrize("lista", [False, True])
def test_formularios_conservan_datos(pagina, lista):
    preparar = "() => { contents.anios_por_area_creador=JSON.stringify(JSON.parse(contents.anios_por_area_creador).anios_por_area); }" if lista else None
    if not iniciar(pagina, preparar):
        return
    pagina.get_by_label("Base", exact=True).fill("Zona Norte")
    pagina.get_by_label("Edad", exact=True).fill("31")
    guardar(pagina, "Dónde y cómo trabajás")
    dato = json.loads(mutaciones(pagina)[-1]["payload"]["contenido"])
    assert dato == {"base":"Zona Norte", "presencial_hibrido_permitido":"CABA y Norte",
                    "remoto_desde":"Argentina", "reubicacion":False,
                    "edad":{"actual":31,"al":"2026-01-01","extra":"conservar"},
                    "nota":"privada", "otro":{"x":1}}
    pagina.get_by_label("Área", exact=True).fill("Análisis")
    pagina.get_by_label("Reemplaza", exact=True).fill(" QA , Soporte,, Diseño ")
    guardar(pagina, "Años de experiencia por área")
    dato = json.loads(mutaciones(pagina)[-1]["payload"]["contenido"])
    filas = dato if lista else dato["anios_por_area"]
    assert filas == [{"area":"Análisis","anios":"5","reemplaza":["QA","Soporte","Diseño"],"fuente":"creador"}]
    if not lista:
        assert dato["nota"] == "no borrar" and dato["extra"] == {"a":1}
    pagina.get_by_role("button", name="Agregar área", exact=True).click()
    assert pagina.get_by_label("Área", exact=True).count() == 2
    pagina.get_by_label("Área", exact=True).nth(1).fill("Ventas")
    pagina.get_by_label("Años", exact=True).nth(1).fill("2")
    pagina.get_by_role("button", name="Quitar", exact=True).first.click()
    guardar(pagina, "Años de experiencia por área")
    dato = json.loads(mutaciones(pagina)[-1]["payload"]["contenido"])
    assert (dato if lista else dato["anios_por_area"])[0]["area"] == "Ventas"


@pytest.mark.parametrize("error", [409, 422, 500, "network"])
def test_errores_409_422_conservan_edicion(pagina, error):
    if not iniciar(pagina):
        return
    card = tarjeta(pagina, "Experiencia")
    card.get_by_role("textbox").fill("Borrador intacto")
    pagina.evaluate("error => { failures['PUT /api/candidato/perfil/experiencia']=error; }", error)
    guardar(pagina, "Experiencia")
    estado = card.get_by_role("status").inner_text()
    assert estado and "Guardado." not in estado
    assert card.get_by_role("textbox").input_value() == "Borrador intacto"
    assert pagina.get_by_role("button", name="Guardar cambios", exact=True).is_enabled()  # el borrador sigue pendiente
    if error == 409:
        assert estado == "El archivo cambió desde que lo abriste. Recargá antes de guardar."
    if error == 422:
        assert estado == "Restricciones inválidas: prueba."
    pagina.evaluate("() => { failures={}; }")
    guardar(pagina, "Experiencia")
    assert mutaciones(pagina)[-1]["payload"]["version"] == "v1-experiencia"
    assert "Guardado." in card.get_by_role("status").inner_text()


def test_confirmar_y_cancelar_borrado(pagina):
    if not iniciar(pagina):
        return
    card = tarjeta(pagina, "Lo que marcaste en las ofertas")
    card.get_by_role("button", name="Borrar", exact=True).first.click()
    assert not mutaciones(pagina, "DELETE")
    assert "CI <b>CD</b>" in card.inner_text()
    pagina.evaluate("() => { accept=true; }")
    card.get_by_role("button", name="Borrar", exact=True).first.click()
    pagina.wait_for_function("() => calls.some(c => c.method==='DELETE')", timeout=3000)
    assert mutaciones(pagina, "DELETE")[0]["url"] == "/api/candidato/aprendizajes/ci%2Fcd%20%25%3F%20%C3%A1"
    pagina.wait_for_function("() => ![...document.querySelectorAll('fieldset')].find(f => f.querySelector('legend').textContent === 'Lo que marcaste en las ofertas').textContent.includes('CI <b>CD</b>')", timeout=3000)
    assert "Otro" in card.inner_text()
    assert "candidato-borrado" in card.get_by_role("status").inner_text()
    assert pagina.evaluate("() => calls.filter(c=>c.url==='/api/candidato/anios-por-area').length") == 2


def test_regenerar_confirmar_cancelar_y_cero(pagina):
    if not iniciar(pagina):
        return
    boton = pagina.get_by_role("button", name="Regenerar CV listos sin aprobar", exact=True)
    boton.click()
    pagina.wait_for_function("() => confirmations.length===1", timeout=3000)
    assert pagina.evaluate("() => confirmations[0]") == "Se van a regenerar 2 CV listos sin aprobar. ¿Seguir?"
    assert not mutaciones(pagina, "POST")
    pagina.evaluate("() => { accept=true; }")
    boton.click()
    pagina.wait_for_function("() => document.getElementById('vista-perfil').textContent.includes('Se regeneran 1 CV.')", timeout=3000)
    assert len(mutaciones(pagina, "POST")) == 1
    pagina.evaluate("() => { radar=[]; manual=[]; }")
    boton.click()
    pagina.wait_for_function("() => document.getElementById('vista-perfil').textContent.includes('No hay CV listos sin aprobar.')", timeout=3000)
    assert len(mutaciones(pagina, "POST")) == 1
    assert pagina.evaluate("() => confirmations.length") == 2


def test_sin_html_ni_ia_ni_autoregeneracion(pagina):
    if not iniciar(pagina):
        return
    assert "innerHTML" not in (RAIZ / "static/perfil.js").read_text(encoding="utf-8")
    assert pagina.locator("#vista-perfil img, #vista-perfil script, #vista-perfil b").count() == 0
    for titulo in ("Datos personales", "Voz del mail"):
        guardar(pagina, titulo)
    assert not mutaciones(pagina, "POST")
    assert pagina.evaluate("() => calls.every(c => c.url.startsWith('/api/candidato/'))")


@pytest.mark.parametrize("ancho", [360, 375, 1200])
def test_legibilidad_360_375(pagina, ancho):
    if not iniciar(pagina):
        return
    pagina.set_viewport_size({"width":ancho,"height":812})
    tarjeta(pagina, "Años de experiencia por área")  # celular: se entra a Mis datos desde el índice
    pagina.get_by_label("Área", exact=True).fill("Área" * 150)
    pagina.evaluate("() => { preview={areas:[{area:'L'.repeat(500),anios:'5'}],vigente:true}; }")
    guardar(pagina, "Años de experiencia por área")

    def legible(donde):
        assert pagina.evaluate("() => document.documentElement.scrollWidth <= innerWidth"), donde
        assert pagina.locator("#vista-perfil").evaluate("el => el.scrollWidth <= el.clientWidth"), donde
        assert pagina.locator("#vista-perfil").evaluate("""el => [...el.querySelectorAll('*')]
            .filter(n => n.getClientRects().length && n.textContent.trim())
            .every(n => parseFloat(getComputedStyle(n).fontSize)>=12)"""), donde
        for campo in pagina.locator("#vista-perfil input, #vista-perfil textarea, #vista-perfil button").all():
            if not campo.is_visible():
                continue
            rect = campo.bounding_box()
            assert rect and rect["x"] >= 0 and rect["x"]+rect["width"] <= ancho, donde

    # Rediseño E3: en el celular se ve una sección por vez; los mismos chequeos en el índice y en cada sección
    legible("Mis datos")
    volver = pagina.get_by_role("button", name="Volver a Mi perfil", exact=True)
    if volver.is_visible():
        volver.click()
        legible("índice")
        for fila in pagina.locator(".perfil-ir").all():
            seccion = fila.get_attribute("data-seccion")
            fila.click()
            legible(seccion)
            volver.click()


# Rediseño E3: las tarjetas se ubican por su título (el orden cambió con las secciones de §6)
def test_pendiente_doble_click_y_borrador_al_volver(pagina):
    if not iniciar(pagina):
        return
    card = tarjeta(pagina, "Experiencia")
    card.get_by_role("textbox").fill("Mi borrador")
    pagina.evaluate("() => { holds['PUT /api/candidato/perfil/experiencia']=true; }")
    pagina.get_by_role("button", name="Guardar cambios", exact=True).evaluate("el => { el.click(); el.click(); }")
    assert len(mutaciones(pagina)) == 1
    assert pagina.get_by_role("button", name="Guardar cambios", exact=True).is_disabled()
    assert "Guardado." not in card.get_by_role("status").inner_text()
    pagina.evaluate("() => { holds['PUT /api/candidato/perfil/experiencia'](); delete holds['PUT /api/candidato/perfil/experiencia']; }")
    pagina.wait_for_function("() => [...document.querySelectorAll('fieldset')].find(f => f.querySelector('legend').textContent === 'Experiencia').textContent.includes('Guardado.')", timeout=3000)
    card.get_by_role("textbox").fill("No perder al volver")
    pagina.evaluate("async () => { await Promise.all([Perfil.mostrar(),Perfil.mostrar()]); }")
    assert card.get_by_role("textbox").input_value() == "No perder al volver"
    assert tarjeta(pagina, "Experiencia").count() == 1


@pytest.mark.parametrize("contenido", ["", "{}"])
def test_restricciones_ausentes_no_agregan_permisos(pagina, contenido):
    if not iniciar(pagina, "() => { contents.restricciones=" + json.dumps(contenido) + "; }"):
        return
    pagina.get_by_label("Base", exact=True).fill("CABA")
    guardar(pagina, "Dónde y cómo trabajás")
    assert json.loads(mutaciones(pagina)[0]["payload"]["contenido"]) == {"base":"CABA"}
    pagina.get_by_label("Acepta viajes frecuentes o al interior", exact=True).check()
    guardar(pagina, "Dónde y cómo trabajás")
    assert json.loads(mutaciones(pagina)[1]["payload"]["contenido"]) == {"base":"CABA","viajes_frecuentes_o_interior":True}


@pytest.mark.parametrize("caso", ["json", "http", "network", "forma"])
def test_carga_fallida_bloquea_solo_su_tarjeta(pagina, caso):
    preparar = {
        "json":"() => { contents.restricciones='{roto'; }",
        "forma":"() => { contents.restricciones='[]'; }",
        "http":"() => { failures['GET /api/candidato/perfil/restricciones']=500; }",
        "network":"() => { failures['GET /api/candidato/perfil/restricciones']='network'; }",
    }[caso]
    if not iniciar(pagina, preparar):
        return
    card = tarjeta(pagina, "Dónde y cómo trabajás")
    # Rediseño E3: sin «Guardar» por tarjeta; la que no cargó queda bloqueada y las demás se editan
    assert all(c.is_disabled() for c in card.locator("input, textarea, select").all())
    assert card.get_by_role("status").inner_text()
    assert tarjeta(pagina, "Experiencia").get_by_role("textbox").is_enabled()
    assert not mutaciones(pagina)


def test_sin_respaldo_no_declara_exito(pagina):
    if not iniciar(pagina):
        return
    pagina.evaluate("() => { window.withoutBackup=true; }")
    guardar(pagina, "Experiencia")
    assert "Guardado." not in tarjeta(pagina, "Experiencia").get_by_role("status").inner_text()


@pytest.mark.parametrize("guardar_antes", [False, True])
def test_get_demorado_no_pisa_borrador_ni_version(pagina, guardar_antes):
    if not iniciar(pagina):
        return
    pagina.evaluate("""() => {
        const original=window.fetch;
        window.fetch=async (url,opts) => {
            const response=await original(url,opts);
            if (url==='/api/candidato/perfil/experiencia' && (!opts || opts.method==='GET')) {
                const snapshot=await response.json();
                await new Promise(resolve => { window.entregar=resolve; });
                return {ok:true,status:200,json:async()=>snapshot};
            }
            return response;
        };
        window.refresco=Perfil.mostrar();
    }""")
    pagina.wait_for_function("() => typeof entregar==='function'", timeout=3000)
    card = tarjeta(pagina, "Experiencia")
    card.get_by_role("textbox").fill("Edición posterior al GET")
    if guardar_antes:
        guardar(pagina, "Experiencia")
    pagina.evaluate("async () => { entregar(); await refresco; }")
    assert card.get_by_role("textbox").input_value() == "Edición posterior al GET"
    guardar(pagina, "Experiencia")
    assert mutaciones(pagina)[-1]["payload"]["version"] == (
        "v1-experiencia-nueva" if guardar_antes else "v1-experiencia")


def test_carga_unica_y_version_pendiente(pagina):
    if not iniciar(pagina):
        return
    # Una sola carga inicial por dato. Entradas superpuestas comparten el GET.
    # 12 datos del perfil: los 5 originales, las reglas del Radar, roles objetivo, stack y conocimientos
    # ausentes, las reglas de Portales, cargos en el CV y ofertas con puntaje bajo
    assert pagina.evaluate("() => calls.filter(c=>c.method==='GET' && c.url.startsWith('/api/candidato/perfil/')).length") == 12
    pagina.evaluate("""() => {
        holds['GET /api/candidato/perfil/experiencia']=true;
        window.entrada=Promise.all([Perfil.mostrar(),Perfil.mostrar()]);
    }""")
    assert pagina.evaluate("() => calls.filter(c=>c.url==='/api/candidato/perfil/experiencia').length") == 2
    pagina.evaluate("async () => { holds['GET /api/candidato/perfil/experiencia'](); delete holds['GET /api/candidato/perfil/experiencia']; await entrada; }")
    tarjeta(pagina, "Experiencia").get_by_role("textbox").fill("Una sola escritura")
    guardar(pagina, "Experiencia")
    assert len(mutaciones(pagina)) == 1


@pytest.mark.parametrize("tipo", ["DELETE", "POST"])
def test_doble_click_y_error_de_accion(pagina, tipo):
    if not iniciar(pagina):
        return
    url = "/api/candidato/aprendizajes/ci%2Fcd%20%25%3F%20%C3%A1" if tipo == "DELETE" else "/api/radar/regenerar-listos"
    pagina.evaluate("key => { accept=true; failures[key]=500; holds[key]=true; }", tipo + " " + url)
    btn = tarjeta(pagina, "Lo que marcaste en las ofertas").get_by_role("button", name="Borrar", exact=True).first if tipo == "DELETE" else pagina.get_by_role("button", name="Regenerar CV listos sin aprobar", exact=True)
    btn.evaluate("el => { el.click(); el.click(); }")
    pagina.wait_for_function("m => calls.some(c=>c.method===m)", arg=tipo, timeout=3000)
    assert btn.is_disabled()
    assert len(mutaciones(pagina, tipo)) == 1
    pagina.evaluate("key => { holds[key](); delete holds[key]; }", tipo + " " + url)
    pagina.wait_for_function("() => document.getElementById('vista-perfil').textContent.includes('Falló el servidor.')", timeout=3000)
    assert btn.is_enabled()
    if tipo == "DELETE":
        assert "CI <b>CD</b>" in tarjeta(pagina, "Lo que marcaste en las ofertas").inner_text()


def test_conflicto_recarga_explicita_conserva_o_descarta(pagina):
    if not iniciar(pagina):
        return
    card = tarjeta(pagina, "Experiencia")
    card.get_by_role("textbox").fill("Borrador en conflicto")
    pagina.evaluate("() => { failures['PUT /api/candidato/perfil/experiencia']=409; }")
    guardar(pagina, "Experiencia")
    recargar = card.get_by_role("button", name="Recargar", exact=True)
    recargar.click()
    assert card.get_by_role("textbox").input_value() == "Borrador en conflicto"
    pagina.evaluate("() => { accept=true; contents.experiencia='Versión externa'; versions.experiencia='externa'; }")
    recargar.click()
    pagina.wait_for_function("() => [...document.querySelectorAll('fieldset')].find(f => f.querySelector('legend').textContent === 'Experiencia').querySelector('textarea').value==='Versión externa'", timeout=3000)
    pagina.evaluate("() => { failures={}; }")
    guardar(pagina, "Experiencia")
    assert mutaciones(pagina)[-1]["payload"]["version"] == "externa"


def test_archivo_areas_ausente_puede_iniciarse(pagina):
    if not iniciar(pagina, "() => { contents.anios_por_area_creador=''; }"):
        return
    pagina.get_by_role("button", name="Agregar área", exact=True).click()
    pagina.get_by_label("Área", exact=True).fill("Sistemas")
    pagina.get_by_label("Años", exact=True).fill("2")
    guardar(pagina, "Años de experiencia por área")
    assert json.loads(mutaciones(pagina)[0]["payload"]["contenido"]) == {"anios_por_area":[{"area":"Sistemas","anios":"2"}]}


def _ultimo_guardado(pagina, clave):
    puts = [m for m in mutaciones(pagina) if m["url"].endswith("/" + clave)]
    return json.loads(puts[-1]["payload"]["contenido"])


def test_editores_nuevos_a_1200(pagina):
    """Roles objetivo, stack, conocimientos ausentes y localidades: se editan a 1200 px y conservan
    las claves que no muestran."""
    if not iniciar(pagina):
        return
    pagina.set_viewport_size({"width": 1200, "height": 900})
    roles = tarjeta(pagina, "Puestos que buscás y áreas de tu trayectoria")
    vaciar_lista(roles, "Roles que buscás")
    roles.get_by_label("Roles que buscás", exact=True).fill("domador de leones\n\n=equilibrista\nmalabarista ")
    roles.get_by_role("button", name="Agregar área de trayectoria", exact=True).click()
    roles.get_by_label("Nombre del área", exact=True).last.fill("magia")
    roles.get_by_label("Títulos de oferta que la detectan (uno por renglón)", exact=True).last.fill("mago\nmaga")
    roles.get_by_label("Es tu perfil actual (si no, sus roles no van al titular)", exact=True).last.uncheck()
    guardar(pagina, "Puestos que buscás y áreas de tu trayectoria")
    dato = _ultimo_guardado(pagina, "roles_objetivo")
    assert dato["nota"] == "conservar"
    assert dato["roles_objetivo"] == ["domador de leones", "=equilibrista", "malabarista"]
    assert dato["areas"][0]["extra"] == "fila" and dato["areas"][0]["sinonimos"] == ["\\bpista\\b"]
    assert dato["areas"][1] == {"area": "magia", "titulos": ["mago", "maga"], "sinonimos": [], "nombres": [],
                                "perfil_actual": False}

    stack = tarjeta(pagina, "Habilidades que tenés")
    # Rediseño E3: transferibles y habilidades base en sus propias tarjetas (mismo archivo, mismo guardado)
    transferibles = pagina.get_by_role("region", name="Habilidades transferibles", exact=True)
    respaldo = pagina.get_by_role("region", name="Habilidades del CV de respaldo", exact=True)
    transferibles.get_by_role("button", name="Agregar transferible", exact=True).click()
    transferibles.get_by_label("Nombre", exact=True).last.fill("Fortran")
    transferibles.get_by_label("Palabras que la detectan (una por renglón)", exact=True).last.fill("fortran")
    transferibles.get_by_label("Lo que tenés equivalente", exact=True).fill("Lotes en Cobol")
    vaciar_lista(respaldo, "Técnicas (una por renglón)")
    respaldo.get_by_label("Técnicas (una por renglón)", exact=True).fill("Cobol\nJCL")
    guardar(pagina, "Habilidades que tenés")
    dato = _ultimo_guardado(pagina, "stack")
    assert dato["nota"] == "conservar" and dato["coincidencias"][0]["extra"] == "fila"
    assert dato["transferibles"] == [{"nombre": "Fortran", "palabras": ["fortran"], "equivalente": "Lotes en Cobol"}]
    assert dato["habilidades_base"] == {"gestion": ["Kanban"], "tecnicas": ["Cobol", "JCL"], "extra": "base"}

    stack.get_by_role("button", name="Agregar coincidencia", exact=True).click()
    stack.get_by_role("button", name="Quitar", exact=True).first.click()
    guardar(pagina, "Habilidades que tenés")
    assert [c["nombre"] for c in _ultimo_guardado(pagina, "stack")["coincidencias"]] == [""]

    ausentes = tarjeta(pagina, "Lo que no sabés")
    vaciar_lista(ausentes, "Conocimientos que no tenés (uno por renglón)")
    ausentes.get_by_role("textbox").fill("Trapecio\nMonociclo")
    guardar(pagina, "Lo que no sabés")
    assert _ultimo_guardado(pagina, "conocimientos_ausentes") == {"conocimientos": ["Trapecio", "Monociclo"],
                                                                  "nota": "conservar"}

    etiqueta = ("Localidades donde aceptás presencial o híbrido (una por renglón; "
                "sin ninguna no se descarta por ubicación)")
    vaciar_lista(pagina, etiqueta)
    pagina.get_by_label(etiqueta, exact=True).fill("caba\npilar")
    guardar(pagina, "Dónde y cómo trabajás")
    dato = _ultimo_guardado(pagina, "restricciones")
    assert dato["localidades_permitidas"] == ["caba", "pilar"] and dato["nota"] == "privada"

    assert pagina.evaluate("() => document.documentElement.scrollWidth <= innerWidth")
    for nombre in ("Puestos que buscás y áreas de tu trayectoria", "Habilidades que tenés"):
        caja = tarjeta(pagina, nombre).bounding_box()
        # Rediseño E3: ocupan todo el ancho de su sección (con el índice a la izquierda, la grilla mide ~890 px)
        grilla = tarjeta(pagina, nombre).locator("xpath=..").bounding_box()
        assert caja["width"] >= grilla["width"] - 1 and caja["width"] > 800, nombre
    for campo in pagina.locator("#vista-perfil input, #vista-perfil textarea, #vista-perfil button").all():
        if campo.is_visible():
            rect = campo.bounding_box()
            assert rect and rect["x"] >= 0 and rect["x"] + rect["width"] <= 1200

