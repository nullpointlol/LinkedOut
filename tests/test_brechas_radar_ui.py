"""Contrato de descarte: DOM sintético, sin portal, proveedor ni datos reales."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from bs4 import BeautifulSoup
from test_radar_ui import NODE_DOM_FALSO, NODE_PRELUDE


def node(body):
    executable = shutil.which("node")
    assert executable, "Node es obligatorio para verificar la UI de descartes"
    script = NODE_PRELUDE % json.dumps(str(Path("static/radar.js").resolve()))
    script += NODE_DOM_FALSO + """
const originalCreate = document.createElement;
const events = el => {
  el.dataset = {}; el.append = (...cs) => cs.forEach(c => el.appendChild(c));
  el.listeners = {}; el.addEventListener = (event, fn) => { el.listeners[event] = fn; };
  let html = '';
  Object.defineProperty(el, 'innerHTML', {get: () => html, set: value => {html=String(value); el.textContent='';}});
  return el;
};
document.createElement = tag => events(originalCreate(tag));
const originalGet = document.getElementById;
document.getElementById = id => { const el = originalGet(id); return el.listeners ? el : events(el); };
const all = root => [root, ...(root.children || []).flatMap(all)];
const find = (root, attr) => all(root).filter(n => n.getAttribute && n.getAttribute(attr) !== undefined);
const reasons = [{requisito: 'Lugar', categoria: 'UBICACION', texto: 'Presencial en Córdoba'},
  {requisito: 'Viajes', categoria: 'VIAJES', texto: '<img src=x onerror=alert(1)>'}];
const post = {id: 7, fuente: 'busqueda', estado: 'ANALIZADA', updated_at: 'v1', match_score: 80,
  puesto: 'Analista con un título muy largo '.repeat(5),
  no_apto: {motivos: reasons}, analisis: {no_apto: {motivos: reasons}, requisitos: [
    {nombre: 'Lugar', estado: 'BRECHA_REAL', tipo: 'REQUISITO_EXCLUYENTE', eliminatorio: true},
    {nombre: 'Docker', estado: 'BRECHA_REAL', tipo: 'REQUISITO_EXCLUYENTE', eliminatorio: true}
  ]}};
const reply = data => ({ok: true, status: 200, json: async () => data});
""" + body
    result = subprocess.run([executable, "-e", script], capture_output=True, text=True,
                            encoding="utf-8", check=True, timeout=20)
    return json.loads(result.stdout)


def test_tarjeta_sello_y_marca():
    out = node("""
const card = T.crearCard(post);
T.S.detalle = post; T.S.selId = post.id; T.renderDetalle();
const detail = document.getElementById('radarDetallePost');
console.log(JSON.stringify({html: card.innerHTML, text: card.textContent,
  detail: detail.textContent, marks: find(detail, 'data-descarte-marca').map(n => n.textContent),
  buttons: find(detail, 'data-descarte-req').map(n => n.getAttribute('aria-label')),
  seal: find(card, 'data-no-apto').map(n => n.textContent)}));
""")
    # invariante: base medida crearCard conserva el título completo en su contenido.
    assert "Analista con un título muy largo" in out["html"]
    if os.getenv("INVARIANT_CHECK") == "1":
        return
    assert out["seal"] == ["No apto: Presencial en Córdoba"]
    assert 'data-accion="postular-igual"' in out["html"] and 'data-accion="descartar"' in out["html"]
    assert 'data-accion="generar-cv"' not in out["html"]
    assert "<img src=x onerror=alert(1)>" in out["detail"]
    assert out["marks"] == ["Me descarta"]  # Docker marcado por IA no es motivo confirmado.
    assert "Me descarta: Lugar" in out["buttons"]
    # Rediseño E5: el título completo envuelve (sin recorte) y el sello «No apto» parte palabras largas (radar.css)
    css = Path("static/css/radar.css").read_text(encoding="utf-8")
    titulo = css[css.index(".titulo-entero {"):]
    titulo = titulo[:titulo.index("}")]
    assert "white-space: normal" in titulo and "overflow-wrap" in titulo and "ellipsis" not in titulo
    sello = css[css.index("#vista-radar [data-no-apto] {"):]
    assert "overflow-wrap: anywhere" in sello[:sello.index("}")]


def test_titulo_tarjeta_sin_truncate():
    titulo = "Analista funcional de operaciones y gestión de incidentes"
    out = node("""
post.puesto = TITULO;
const card = T.crearCard(post);
console.log(JSON.stringify({html: card.innerHTML,
  seal: find(card, 'data-no-apto').map(n => n.textContent)}));
""".replace("TITULO", json.dumps(titulo)))
    soup = BeautifulSoup(out["html"], "html.parser")
    puesto = soup.find(string=titulo).parent
    # invariante: título íntegro, sello y ambas acciones conservados.
    assert puesto.get_text() == titulo
    assert out["seal"] == ["No apto: Presencial en Córdoba"]
    assert soup.find("button", attrs={"data-accion": "postular-igual"})
    assert soup.find("button", attrs={"data-accion": "descartar"})
    if os.getenv("INVARIANT_CHECK") == "1":
        return
    clases = puesto.get("class", [])
    # Rediseño E2: título entero con sello (radar.css: .titulo-entero), en una línea sin él (.titulo-linea)
    assert "titulo-linea" not in clases
    assert "titulo-entero" in clases


def test_filtro_no_apto():
    soup = BeautifulSoup(Path("static/index.html").read_text(encoding="utf-8"), "html.parser")
    # invariante: base medida, 10 estados existentes sin un estado SQL nuevo.
    assert len(soup.find(id="radarFiltros").find_all("button")) == 10
    if os.getenv("INVARIANT_CHECK") == "1":
        return
    control = soup.find(id="radarFiltroNoApto")
    assert control and not control.find_parent(id="radarFiltros")
    assert control.get("aria-pressed") == "false"
    out = node("""
console.log(JSON.stringify([post, {...post, no_apto: null, analisis: {}},
 {...post, no_apto: {motivos: []}, analisis: {}}].map(p => U.coincideFiltro(p, 'NO_APTO'))));
""")
    assert out == [True, False, False]


@pytest.mark.parametrize("estado", ["ANALIZADA", "LISTO"])
def test_postular_igual_refresca_sin_aprobar(estado):
    out = node("""
(async () => {
post.estado = ESTADO; T.S.posts.set(7, post); T.S.selId = 7; T.S.detalle = post;
T.S.estado = {...T.S.estado, analisis_pausado:true, generacion_pausada:true, envios_pausados:true};
const calls = [];
global.fetch = async (url, opts) => { calls.push({url, method: opts.method});
 return reply({...post, estado: ESTADO === 'LISTO' ? 'LISTO' : 'GENERANDO',
   no_apto: null, analisis: {requisitos: []}, updated_at: 'v2', aprobado_at:null}); };
if (T.postularIgual) await T.postularIgual(7);
console.log(JSON.stringify({calls, seal: T.crearCard(T.S.posts.get(7)).textContent,
  detail: T.S.detalle, pauses: T.S.estado})); process.exit(0);
})();
""".replace("ESTADO", json.dumps(estado)))
    # invariante: base medida, ninguna acción implícita de aprobación/envío ni cambio de pausas.
    assert not any("approve" in c["url"] or "send" in c["url"] for c in out["calls"])
    assert out["pauses"]["analisis_pausado"] and out["pauses"]["generacion_pausada"] and out["pauses"]["envios_pausados"]
    if os.getenv("INVARIANT_CHECK") == "1":
        return
    assert out["calls"] == [{"url": "/api/radar/posts/7/force", "method": "POST"}]
    assert "No apto" not in out["seal"]
    assert out["detail"]["updated_at"] == "v2" and out["detail"]["aprobado_at"] is None


def test_marcar_desmarcar_descarte():
    out = node("""
(async () => {
T.S.selId = 7; T.S.detalle = post; T.S.posts.set(7, post);
const calls = []; global.fetch = async (url, opts) => {calls.push(JSON.parse(opts.body)); return reply(post);};
await T.ciclarRequisito('Lugar');
if (T.marcarDescarte) { await T.marcarDescarte('Lugar', true); await T.marcarDescarte('Lugar', false); }
console.log(JSON.stringify(calls)); process.exit(0);
})();
""")
    # invariante: base medida, clic histórico solo manda nombre y conserva el ciclo.
    assert out[0] == {"nombre": "Lugar"}
    if os.getenv("INVARIANT_CHECK") == "1":
        return
    assert out[1:] == [{"nombre": "Lugar", "eliminatorio": True}, {"nombre": "Lugar", "eliminatorio": False}]


def test_pausas_y_version_se_conservan():
    out = node("""
T.S.selId = 7; T.S.detalle = {...post, estado: 'LISTO', email_to:'a@b.test', aprobado_at:null};
T.renderEmail(true);
console.log(JSON.stringify({version:T.S.revisionVista, approve:document.getElementById('radarAprobarBtn').hidden,
  actions: U.accionesPara(T.S.detalle)}));
""")
    # invariante: base medida, revisión del contenido vigente y aprobación individual LISTO conservadas.
    assert out["version"] == {"id": 7, "updated_at": "v1"}
    assert out["approve"] is False
    if os.getenv("INVARIANT_CHECK") == "1":
        return
    assert out["actions"]["postularIgual"] is True


def test_clicks_de_filtro_y_postular_igual():
    if os.getenv("INVARIANT_CHECK") == "1":
        return
    out = node("""
(async () => {
T.S.selId = 7; T.S.detalle = post; T.S.posts.set(7, post); T.enlazarEventos(); T.renderEmail(true);
const filter = document.getElementById('radarFiltroNoApto'); filter.listeners.click();
const selected = T.S.filtro, pressed = filter.getAttribute('aria-pressed');
const calls = []; global.fetch = async (url, opts) => { calls.push(url);
 return reply({...post, no_apto:null, analisis: {requisitos:[]}, updated_at:'v2'}); };
const btn = document.getElementById('radarPostularIgualBtn');
const visible = !btn.hidden;
await btn.listeners.click({currentTarget:btn});
const hidden = btn.hidden;
document.getElementById('radarFiltros').listeners.click({target:{closest:() => ({dataset:{estado:'LISTO'}})}});
console.log(JSON.stringify({selected, pressed, visible, hidden, calls, final:T.S.filtro,
  seal:T.crearCard(T.S.posts.get(7)).textContent})); process.exit(0);
})();
""")
    assert out == {"selected": "NO_APTO", "pressed": "true", "visible": True, "hidden": True,
                   "calls": ["/api/radar/posts/7/force"], "final": "LISTO", "seal": ""}


def test_control_descarte_refleja_respuesta_y_no_cicla():
    out = node("""
(async () => {
T.S.selId = 7; T.S.detalle = post; T.S.posts.set(7, post); T.renderDetalle();
const detail = document.getElementById('radarDetallePost');
const calls = []; global.fetch = async (url, opts) => {
 const body=JSON.parse(opts.body); calls.push(body);
 const analisis={...post.analisis, requisitos:[{...post.analisis.requisitos[0], eliminatorio:body.eliminatorio}]};
 if (!body.eliminatorio) delete analisis.no_apto;
 return reply({...post, analisis, no_apto:analisis.no_apto || null});
};
const before=find(detail,'data-descarte-req')[0];
const pressed=before.getAttribute('aria-pressed'); await before.listeners.click();
const after=find(detail,'data-descarte-req')[0];
const unpressed=after.getAttribute('aria-pressed'); await after.listeners.click();
console.log(JSON.stringify({pressed, unpressed, calls, state:T.S.detalle.analisis.requisitos[0].estado}));
process.exit(0);
})();
""")
    # invariante: base medida, modificar descarte no altera el estado del requisito.
    assert out["state"] == "BRECHA_REAL"
    if os.getenv("INVARIANT_CHECK") == "1":
        return
    assert out["pressed"] == "true" and out["unpressed"] == "false"
    assert out["calls"] == [{"nombre": "Lugar", "eliminatorio": False}, {"nombre": "Lugar", "eliminatorio": True}]


def test_force_rechazado_conserva_sello():
    if os.getenv("INVARIANT_CHECK") == "1":
        return
    out = node("""
(async () => {
T.S.selId=7; T.S.detalle=post; T.S.posts.set(7,post);
global.fetch=async (url,opts) => opts.method==='POST'
 ? {ok:false,status:409,json:async()=>({detail:'Oferta ocupada'})} : reply(post);
await T.postularIgual(7);
console.log(JSON.stringify({seal:T.crearCard(T.S.posts.get(7)).textContent,
  state:T.S.detalle.estado, version:T.S.detalle.updated_at})); process.exit(0);
})();
""")
    assert out["seal"] == "No apto: Presencial en Córdoba"
    assert out["state"] == "ANALIZADA" and out["version"] == "v1"


@pytest.mark.parametrize("estado", ["ENVIADO", "DESCARTADO", "IGNORADA"])
def test_estados_cerrados_sin_acciones_nuevas(estado):
    if os.getenv("INVARIANT_CHECK") == "1":
        return
    out = node("""
post.estado=ESTADO; T.S.selId=7; T.S.detalle=post; T.renderDetalle();
if (post.estado === 'IGNORADA') { post.no_apto=null; post.analisis.no_apto=null; T.renderDetalle(); }
console.log(JSON.stringify({action:!!U.accionesPara(post).postularIgual,
 controls:find(document.getElementById('radarDetallePost'),'data-descarte-req').length}));
""".replace("ESTADO", json.dumps(estado)))
    assert out == {"action": False, "controls": 0}


def test_resumen_actualiza_condicion_aunque_haya_detalle_anterior():
    if os.getenv("INVARIANT_CHECK") == "1":
        return
    out = node("""
(async () => {
T.S.posts.set(7, post);
global.fetch=async()=>reply([{id:7, fuente:'busqueda', estado:'ANALIZADA', no_apto:null}]);
await T.cargarPosts();
const cleared=U.coincideFiltro(T.S.posts.get(7),'NO_APTO');
global.fetch=async()=>reply([{id:7, fuente:'busqueda', estado:'ANALIZADA', no_apto:{motivos:reasons}}]);
await T.cargarPosts();
console.log(JSON.stringify([cleared,U.coincideFiltro(T.S.posts.get(7),'NO_APTO')])); process.exit(0);
})();
""")
    assert out == [False, True]


def test_un_solo_control_descarte_por_fila():
    out = node("""
post.analisis.requisitos.push(
 {nombre:'Transferible', estado:'TRANSFERIBLE'}, {nombre:'Cumplido', estado:'MATCH_DIRECTO'});
T.S.detalle=post; T.renderDetalle();
const rows=all(document.getElementById('radarDetallePost')).filter(n=>n.tag==='li');
console.log(JSON.stringify(rows.map(row=>({
 labels:all(row).filter(n=>n.textContent==='Me descarta').map(n=>n.tag),
 controls:find(row,'data-descarte-req').map(n=>({name:n.getAttribute('aria-label'),
  pressed:n.getAttribute('aria-pressed'), classes:n.className,
  mark:n.getAttribute('data-descarte-marca')!==undefined}))
}))));
""")
    assert [r["labels"] for r in out] == [["button"], ["button"], [], []]
    for row, name, pressed in zip(out[:2], ["Lugar", "Docker"], ["true", "false"]):
        control, = row["controls"]
        assert control["name"] == f"Me descarta: {name}"
        assert control["pressed"] == pressed
        assert control["mark"] == (pressed == "true")
        # Rediseño E2: el tono de marcado lo pone radar.css según aria-pressed
        assert "req-descarta" in control["classes"].split()
    assert not out[2]["controls"]
    assert not out[3]["controls"]


def test_titulo_corta_entre_palabras():
    out = node("""
const cases=[post, {...post, no_apto:null, analisis:{}}];
console.log(JSON.stringify(cases.map(p=>{
 T.S.detalle=p; T.renderDetalle();
 return {card:T.crearCard(p).innerHTML, detail:document.getElementById('radarDetallePost').innerHTML};
})));
""")
    for i, case in enumerate(out):
        for location in ("card", "detail"):
            soup = BeautifulSoup(case[location], "html.parser")
            title = soup.find(string=lambda s: s and s.startswith("Analista con un título muy largo"))
            assert title
            classes = title.parent.get("class", [])
            if location == "card":
                # Rediseño E2: la tarjeta común va en una línea; con sello «No apto», entera
                assert ("titulo-linea" if i == 1 else "titulo-entero") in classes
                continue
            # Rediseño E2: el puesto del detalle (.det-puesto) corta entre palabras (radar.css)
            assert "det-puesto" in classes
    css = Path("static/css/radar.css").read_text(encoding="utf-8")
    regla = css[css.index(".det-puesto {"):]
    assert "overflow-wrap: break-word" in regla[:regla.index("}")]


def test_filtro_no_apto_con_conteo_y_estilo():
    soup = BeautifulSoup(Path("static/index.html").read_text(encoding="utf-8"), "html.parser")
    control = soup.find(id="radarFiltroNoApto")
    assert control.find("span", attrs={"data-count": True})
    assert "Mostrar también las no aptas" in control.get_text()  # §8
    assert not control.find_parent(id="radarFiltros")
    # Rediseño E2: el estado activo va por aria-pressed (componentes.css lo resalta), no por clases de color
    assert control["aria-pressed"] == "false"
    out = node("""
const filter=document.getElementById('radarFiltroNoApto');
const count=document.createElement('span'); count.setAttribute('data-count',''); filter.appendChild(count);
T.S.posts=new Map([
 [7,post], [8,{...post,id:8,fuente:'feed'}],
 [9,{...post,id:9,no_apto:{motivos:[]},analisis:{}}],
 [10,{...post,id:10,estado:'IGNORADA'}],
 [11,{...post,id:11,fuente:'manual'}]
]);
T.enlazarEventos();
document.getElementById('radarFiltros').listeners.click({target:{closest:()=>({dataset:{estado:'TODOS'}})}});
const initial={pressed:filter.getAttribute('aria-pressed'),count:count.textContent};
filter.listeners.click();
const active={classes:filter.className,count:count.textContent,pressed:filter.getAttribute('aria-pressed')};
T.S.posts.delete(8);
document.getElementById('radarFiltros').listeners.click({target:{closest:()=>({dataset:{estado:'TODOS'}})}});
console.log(JSON.stringify({initial,active,final:{pressed:filter.getAttribute('aria-pressed'),count:count.textContent}}));
""")
    # La ignorada con motivos también cuenta como No apto; la manual queda fuera.
    assert out["initial"]["count"] == "3"
    assert out["active"]["count"] == "3" and out["active"]["pressed"] == "true"
    assert out["final"]["count"] == "2"
    assert out["initial"]["pressed"] == "false" and out["final"]["pressed"] == "false"
