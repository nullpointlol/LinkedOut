"""DOM real en Chromium, con API simulada y sin red/servidor."""
import json
import re
from pathlib import Path

import pytest
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright


def test_estructura_y_codigo_seguro():
    html = Path("static/index.html").read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")
    boton = soup.select_one("#selectorVista #btnVistaPerfil")
    assert boton is not None, "Falta Mi perfil en el selector"
    assert boton.get("aria-label") == "Mi perfil"
    # Rediseño E1: ícono del sprite local y rótulo siempre visible (abajo en el celular)
    assert boton.select_one('svg use[href="/static/iconos.svg#user"]') is not None
    assert boton.find("span").get_text(strip=True) == "Mi perfil"
    assert soup.select_one("section#vista-perfil[hidden]") is not None
    assert any("/static/perfil.js" in s.get("src", "") for s in soup.find_all("script"))
    ruta = Path("static/perfil.js")
    assert ruta.is_file(), "Falta el módulo de perfil"
    js = ruta.read_text(encoding="utf-8")
    assert "innerHTML" not in js
    assert not re.search(r"text-zinc-(?:500|600)\b|text-\[(?:[0-9]|10|11)px\]", js)


@pytest.fixture
def pagina():
    html = BeautifulSoup(Path("static/index.html").read_text(encoding="utf-8"), "html.parser")
    for script in html.find_all("script"):
        script.decompose()
    for link in html.find_all("link"):
        link.decompose()
    ruta = Path("static/perfil.js")
    if not ruta.is_file():
        yield None
        return
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 375, "height": 812})
        page.route("**/*", lambda r: r.abort())
        page.set_content(str(html))
        page.add_style_tag(content="[hidden]{display:none!important} body{font:12px sans-serif} button,input,textarea{font:inherit}")
        page.evaluate("""() => {
          window.llamadas = []; window.confirmaciones = []; window.confirmar = true;
          window.confirm = texto => { confirmaciones.push(texto); return confirmar; };
          window.datos = {
            experiencia: 'Experiencia anterior', personales: 'Datos anteriores', voz_email: 'Hola <img id="ataque">',
            restricciones: JSON.stringify({base:'AMBA',presencial_hibrido_permitido:'CABA',remoto_desde:'Argentina',reubicacion:false,nota:'conservar',edad:{actual:36,al:'2020-01-01',extra:'edad'}}),
            anios_por_area_creador: JSON.stringify({nota:'conservar',extra:42,anios_por_area:[{area:'Soporte',anios:'6',reemplaza:['incidentes'],extra:'fila'}]})
          };
          window.versiones = Object.fromEntries(Object.keys(datos).map(k=>[k,'v1']));
          window.respuestaError = null;
          window.fetch = async (url, opciones={}) => {
            const method = opciones.method || 'GET';
            llamadas.push({url:String(url),method,body:opciones.body ? JSON.parse(opciones.body) : null});
            let data = {}, status = 200;
            if (String(url).includes('/perfil/')) {
              const clave = String(url).split('/').pop();
              if(method === 'PUT') {
                if(respuestaError) {status=respuestaError.status;data={detail:respuestaError.detail};}
                else {datos[clave]=JSON.parse(opciones.body).contenido;versiones[clave]='v2';data={ok:true,version:'v2',backup:'candidato-20261109-120000',aviso:clave==='experiencia'?'Los años por área se recalculan con IA en el próximo CV.':null};}
              } else data={clave,archivo:clave+'.txt',contenido:datos[clave],version:versiones[clave],existe:true};
            } else if(String(url).includes('/anios-por-area')) data={areas:[],vigente:false};
            else if(String(url).includes('/aprendizajes')) data={aprendizajes:{sql:{tag:'SQL',estado:'TRANSFERIBLE'}}};
            else if(String(url).includes('/posts?')) data=[{id:1,estado:'LISTO',aprobado_at:null},{id:2,estado:'LISTO',aprobado_at:'ayer'}];
            else if(String(url).includes('/regenerar-listos')) data={regenerados:1,ids:[1]};
            return {ok:status===200,status,json:async()=>data};
          };
        }""")
        page.add_script_tag(content=ruta.read_text(encoding="utf-8"))
        # Contrato público de la vista; no llama a funciones internas de renderizado.
        page.evaluate("async () => { await window.Perfil.mostrar(); document.getElementById('vista-perfil').hidden=false; }")
        yield page
        browser.close()


def tarjeta(page, nombre):
    return page.get_by_role("group", name=nombre, exact=True)


def test_textos_campos_y_guardado_sin_regenerar(pagina):
    page = pagina
    assert page is not None, "Falta static/perfil.js"
    assert page.get_by_text("Los cambios rigen para los próximos CV. Los CV ya generados no cambian.", exact=True).count() == 1
    for nombre in ("Experiencia", "Datos personales", "Voz del mail", "Dónde y cómo trabajás", "Años de experiencia por área", "Lo que marcaste en las ofertas"):
        assert tarjeta(page, nombre).count() == 1
    card = tarjeta(page, "Experiencia")
    campo = card.get_by_role("textbox")
    campo.fill("Nueva experiencia ñ")
    page.get_by_role("button", name="Guardar cambios", exact=True).click()  # rediseño E3: barra única
    page.wait_for_function("llamadas.some(x=>x.method==='PUT')")
    assert "Guardado. Copia de respaldo: candidato-20261109-120000." in card.get_by_role("status").inner_text()
    assert "Los años por área se recalculan con IA en el próximo CV." in card.get_by_role("status").inner_text()
    puts = page.evaluate("llamadas.filter(x=>x.method==='PUT')")
    assert puts[-1]["body"] == {"contenido": "Nueva experiencia ñ", "version": "v1"}
    assert not page.evaluate("llamadas.some(x=>x.url.includes('regenerar-listos'))")
    assert page.evaluate("llamadas.filter(x=>x.url.includes('/anios-por-area')).length") >= 2
    assert page.get_by_text("Se calculan con IA en el próximo CV.", exact=True).count() == 1
    assert page.locator("#ataque").count() == 0


@pytest.mark.parametrize("status", [409, 422])
def test_detail_visible_sin_falso_exito(pagina, status):
    page = pagina
    assert page is not None, "Falta static/perfil.js"
    page.evaluate("s => respuestaError={status:s,detail:'Detalle de prueba ñ'}", status)
    card = tarjeta(page, "Datos personales")
    card.get_by_role("textbox").dispatch_event("input")  # rediseño E3: guardar tal cual = un cambio sin cambiar el valor
    page.get_by_role("button", name="Guardar cambios", exact=True).click()  # rediseño E3: barra única
    page.wait_for_function("llamadas.some(x=>x.method==='PUT')")
    assert card.get_by_role("status").inner_text() == "Detalle de prueba ñ"


def test_formularios_conservan_claves_no_visibles(pagina):
    page = pagina
    assert page is not None, "Falta static/perfil.js"
    card = tarjeta(page, "Dónde y cómo trabajás")
    # no editor JSON: 3 campos de texto + «Edad al» (input date, que Playwright cuenta como textbox)
    # + la lista de localidades (13/11: la zona salió del código), un renglón por localidad, y dónde vive (15/11:
    # una residencia exigida se compara con eso)
    listas = card.locator("textarea")
    assert listas.count() == 3 and card.get_by_role("textbox").count() == 7
    assert card.get_by_label(re.compile("^Localidades donde aceptás presencial")).count() == 1
    assert card.get_by_label(re.compile("^Dónde vivís")).count() == 1
    card.get_by_label("Base", exact=True).fill("CABA")
    card.get_by_label("Acepta reubicación", exact=True).check()
    page.get_by_role("button", name="Guardar cambios", exact=True).click()  # rediseño E3: barra única
    page.wait_for_function("llamadas.some(x=>x.method==='PUT')")
    body = page.evaluate("llamadas.filter(x=>x.method==='PUT').at(-1).body")
    dato = json.loads(body["contenido"])
    assert dato["base"] == "CABA" and dato["reubicacion"] is True
    assert dato["nota"] == "conservar" and dato["edad"]["extra"] == "edad"
    areas = tarjeta(page, "Años de experiencia por área")
    areas.get_by_label("Años", exact=True).fill("5")
    areas.get_by_label("Reemplaza", exact=True).fill("incidentes, mesa de ayuda")
    page.get_by_role("button", name="Guardar cambios", exact=True).click()  # rediseño E3: barra única
    page.wait_for_function("llamadas.filter(x=>x.method==='PUT').length===2")
    dato = json.loads(page.evaluate("llamadas.filter(x=>x.method==='PUT').at(-1).body.contenido"))
    assert dato["nota"] == "conservar" and dato["extra"] == 42
    assert dato["anios_por_area"][0] == {"area": "Soporte", "anios": "5", "reemplaza": ["incidentes", "mesa de ayuda"], "extra": "fila"}
    areas.get_by_role("button", name="Agregar área", exact=True).click()
    assert areas.get_by_label("Área", exact=True).count() == 2
    areas.get_by_role("button", name="Quitar", exact=True).last.click()
    assert areas.get_by_label("Área", exact=True).count() == 1


def test_confirmaciones_regenerar_y_borrar(pagina):
    page = pagina
    assert page is not None, "Falta static/perfil.js"
    boton = page.get_by_role("button", name="Regenerar CV listos sin aprobar", exact=True)
    page.evaluate("confirmar=false")
    boton.click()
    page.wait_for_function("confirmaciones.length>0")
    assert page.evaluate("confirmaciones.at(-1)") == "Se van a regenerar 1 CV listos sin aprobar. ¿Seguir?"
    assert not page.evaluate("llamadas.some(x=>x.method==='POST')")
    page.evaluate("confirmar=true")
    boton.click()
    page.wait_for_function("llamadas.some(x=>x.method==='POST')")
    assert page.get_by_text("Se regeneran 1 CV.", exact=True).count() == 1
    card = tarjeta(page, "Lo que marcaste en las ofertas")
    assert "SQL" in card.inner_text() and "Transferible" in card.inner_text()  # misión 09: etiqueta como en el Radar
    page.evaluate("confirmar=false")
    card.get_by_role("button", name="Borrar", exact=True).click()
    assert not page.evaluate("llamadas.some(x=>x.method==='DELETE')")
    page.evaluate("confirmar=true")
    card.get_by_role("button", name="Borrar", exact=True).click()
    page.wait_for_function("llamadas.some(x=>x.method==='DELETE')")
    assert page.evaluate("llamadas.find(x=>x.method==='DELETE').url").endswith("/aprendizajes/sql")


def test_transiciones_anteriores_y_perfil(pagina):
    page = pagina
    assert page is not None, "Falta static/perfil.js"
    js = Path("static/app.js").read_text(encoding="utf-8")
    inicio = js.index("function cambiarVista(")
    fin = js.index("\n}", inicio) + 2
    page.add_script_tag(content="window.modos=[];window.Radar={mostrar:v=>modos.push(v)};" + js[inicio:fin])
    page.evaluate("cambiarVista('perfil')")
    assert page.locator("#vista-perfil").get_attribute("hidden") is None
    assert page.locator("#vista-radar").get_attribute("hidden") is not None
    assert page.locator("main").evaluate("e=>e.style.display") == "none"
    for modo in ("manual", "radar", "editor"):
        page.evaluate("v=>cambiarVista(v)", modo)
        assert page.locator("#vista-perfil").get_attribute("hidden") is not None
        assert page.locator("#vista-radar").evaluate("e=>e.hidden") == (modo == "editor")
        assert page.locator("main").evaluate("e=>e.style.display") == ("" if modo == "editor" else "none")
    assert page.evaluate("modos") == ["manual", "radar"]
