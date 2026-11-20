"""Contrato DOM/JS de la vista Radar LinkedIn (Task 10)."""
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

INDEX = Path("static/index.html")
APP_JS = Path("static/app.js")
RADAR_JS = Path("static/radar.js")

IDS_VISTA_RADAR = [
    "radarToggleBtn", "radarEstadoPill", "radarUmbralInput",
    "radarAprobarTodosBtn", "radarGmailAviso", "radarMostrarNavegadorBtn", "radarFiltros",
    "radarLista", "radarDetallePost", "radarEmailTo", "radarEmailSubject", "radarEmailBody",
    "radarVerPdf", "radarAprobarBtn", "radarDescartarBtn", "radarRegenerarBtn",
    "radarAnalizarBtn", "radarGenerarCvBtn", "radarReintentarBtn", "radarAbrirEditorBtn",
]


@pytest.fixture(scope="module")
def soup():
    return BeautifulSoup(INDEX.read_text(encoding="utf-8"), "html.parser")


def test_header_has_view_switch(soup):
    header = soup.find("header")
    assert header.find(id="btnVistaManual") is not None
    assert header.find(id="btnVistaRadar") is not None
    assert header.find(id="btnVistaPostulaciones") is None


def test_radar_view_hidden_by_default_and_has_contract_ids(soup):
    vista = soup.find(id="vista-radar")
    assert vista is not None
    assert vista.has_attr("hidden"), "#vista-radar debe iniciar oculta"
    for dom_id in IDS_VISTA_RADAR:
        assert vista.find(id=dom_id) is not None, f"Falta #{dom_id} dentro de #vista-radar"


def test_radar_view_is_sibling_of_main_not_inside_it(soup):
    vista = soup.find(id="vista-radar")
    assert vista.find_parent("main") is None


def test_radar_control_types(soup):
    umbral = soup.find(id="radarUmbralInput")
    assert umbral.name == "input"
    assert umbral.get("type") == "number"
    assert umbral.get("min") == "0" and umbral.get("max") == "100"
    destinatario = soup.find(id="radarEmailTo")
    # Texto libre (type=email) con sugerencias: permite elegir un email detectado o escribir otro
    assert destinatario.name == "input"
    assert destinatario.get("type") == "email"
    assert destinatario.get("list") == "radarEmailToOpciones"
    assert soup.find(id="radarEmailToOpciones").name == "datalist"
    assert soup.find(id="radarEmailSubject").name == "input"
    assert soup.find(id="radarEmailBody").name == "textarea"


def test_radar_filters_states(soup):
    botones = soup.find(id="radarFiltros").find_all("button")
    estados = [b.get("data-estado") for b in botones]
    assert sorted(estados) == sorted([
        "RELEVANTES", "PENDIENTES", "ANALIZADA", "LISTO", "GENERANDO", "ENVIADO", "DESCARTADO", "ERROR", "TODOS",
        "IGNORADA",  # temporal
    ])
    assert len(estados) == 10


def test_panels_are_named_feed_general_and_mi_feed(soup):
    vista = soup.find(id="vista-radar")
    texto = vista.get_text(" ", strip=True)
    assert "Búsquedas" in texto and "Mi feed" in texto  # §8: «Feed general» → «Búsquedas»
    assert "Búsqueda global" not in texto
    tab = vista.find(attrs={"data-radar-tab": "lista"})
    assert "Búsquedas" in tab.get_text(" ", strip=True) and "Posts" not in tab.get_text(" ", strip=True)


def test_app_js_forwards_radar_post_removed():
    js = APP_JS.read_text(encoding="utf-8")
    assert "radar_post_removed" in js and "onPostRemoved" in js


def test_radar_location_filter(soup):
    cont = soup.find(id="radarFiltroAlcance")
    assert cont is not None and cont.find_parent(id="vista-radar") is not None
    alcances = [b.get("data-alcance") for b in cont.find_all("button")]
    assert alcances == ["TODAS", "ARGENTINA", "EXTERIOR_COMPATIBLE"]


def test_radar_has_mobile_tabs_for_three_columns(soup):
    vista = soup.find(id="vista-radar")
    tabs = {b.get("data-radar-tab") for b in vista.find_all(attrs={"data-radar-tab": True})}
    assert {"lista", "feed", "detalle", "email"} <= tabs


def test_radar_script_cache_busted():
    html = INDEX.read_text(encoding="utf-8")
    assert 'src="/static/radar.js?v=' in html
    assert 'src="/static/app.js?v=' in html
    # radar.js usa escapeHtml de app.js: se carga después
    assert html.index("/static/app.js?v=") < html.index("/static/radar.js?v=")


def test_app_js_forwards_radar_events():
    js = APP_JS.read_text(encoding="utf-8")
    assert "radar_status" in js and "radar_post" in js and "window.Radar" in js


def test_app_js_has_view_switch():
    js = APP_JS.read_text(encoding="utf-8")
    assert "function cambiarVista" in js
    html = INDEX.read_text(encoding="utf-8")
    assert "cambiarVista('radar')" in html and "cambiarVista('manual')" in html


def test_radar_js_exposes_api_and_uses_escape_html():
    js = RADAR_JS.read_text(encoding="utf-8")
    assert "escapeHtml(" in js
    for nombre in ("init", "onStatus", "onPost", "mostrar"):
        assert re.search(rf"\b{nombre}\b", js)
    assert "window.Radar" in js


def test_radar_js_never_interpolates_raw_post_fields_into_html():
    """Ningún campo de post/LLM se interpola crudo en una plantilla HTML (`${p.autor}` etc.)."""
    js = RADAR_JS.read_text(encoding="utf-8")
    campos = ("autor", "cargo_autor", "texto", "motivo", "email_subject", "email_body", "email_to",
              "puesto", "empresa", "error", "instrucciones_postulacion", "estado_detalle", "message_id",
              "destino_canal", "canal")
    crudos = []
    for m in re.finditer(r"\$\{([^}]+)\}", js):
        expr = m.group(1).strip()
        if "escapeHtml(" in expr:
            continue
        # Asignar a `textContent` no interpreta HTML: es seguro aunque el campo vaya crudo
        sentencia = js[js.rfind(";", 0, m.start()) + 1:m.start()]
        if re.search(r"\.textContent\s*[+]?=", sentencia):
            continue
        if any(re.search(rf"\.{c}\b", expr) for c in campos):
            crudos.append(expr)
    assert crudos == [], crudos


requiere_node = pytest.mark.skipif(shutil.which("node") is None, reason="node no está instalado")


@requiere_node
def test_radar_js_syntax_valid():
    subprocess.run(["node", "--check", str(RADAR_JS)], check=True, capture_output=True)


def _run_node(script: str):
    if shutil.which("node") is None:
        pytest.skip("node no está instalado")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(script)
        nombre = f.name
    try:
        res = subprocess.run(["node", nombre], capture_output=True, text=True, encoding="utf-8", check=True)
        return json.loads(res.stdout.strip())
    finally:
        os.unlink(nombre)


NODE_PRELUDE = r"""
global.window = global;
global.document = { addEventListener() {}, getElementById() { return null; } };
global.escapeHtml = (s) => s == null ? '' : String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
require(%s);
const U = window.Radar._util;
"""


def _node(body: str):
    prelude = NODE_PRELUDE % json.dumps(str(RADAR_JS.resolve()))
    return _run_node(prelude + body)


@requiere_node
def test_util_url_segura_rejects_non_http():
    out = _node("""
console.log(JSON.stringify([
  U.urlSegura('javascript:alert(1)'), U.urlSegura('https://www.linkedin.com/feed/update/x'),
  U.urlSegura(null), U.urlSegura('data:text/html,hi'),
  U.urlSegura('http://evil.com'), U.urlSegura('https://evil.com/'),
  U.urlSegura('https://evil.com/www.linkedin.com'), U.urlSegura('https://www.linkedin.com.evil.com/x'),
  U.urlSegura('http://www.linkedin.com/feed/update/x'), U.urlSegura('https://linkedin.com/x'),
  U.urlSegura('https://www.linkedin.com@evil.com/x'), U.urlSegura('no es una url'),
]));
""")
    assert out == [
        "", "https://www.linkedin.com/feed/update/x", "", "",
        "", "", "", "", "", "", "", "",
    ]


@requiere_node
def test_util_filtrar_posts_by_chip():
    out = _node("""
const posts = [
  {id: 1, estado: 'LISTO'}, {id: 2, estado: 'GENERANDO'}, {id: 3, estado: 'ENVIADO'},
  {id: 4, estado: 'ENVIANDO'}, {id: 5, estado: 'NUEVO'}, {id: 6, estado: 'ERROR'}, {id: 7, estado: 'DESCARTADO'},
];
const ids = (f) => posts.filter((p) => U.coincideFiltro(p, f)).map((p) => p.id);
console.log(JSON.stringify({
  LISTO: ids('LISTO'), GENERANDO: ids('GENERANDO'), ENVIADO: ids('ENVIADO'),
  DESCARTADO: ids('DESCARTADO'), ERROR: ids('ERROR'), TODOS: ids('TODOS'),
}));
""")
    assert out["LISTO"] == [1]
    assert out["GENERANDO"] == [2]
    assert out["ENVIADO"] == [3, 4]  # ENVIANDO se agrupa con Enviados
    assert out["DESCARTADO"] == [7]
    assert out["ERROR"] == [6]
    assert out["TODOS"] == [1, 2, 3, 4, 5, 6, 7]


@requiere_node
def test_util_pill_describes_each_state():
    out = _node("""
const futuro = (min) => { const d = new Date(Date.now() + min * 60000);
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`; };
console.log(JSON.stringify({
  corriendo: U.describirPill({estado: 'CORRIENDO'}, false),
  pausa: U.describirPill({estado: 'PAUSA', next_cycle_at: futuro(10)}, false),
  login: U.describirPill({estado: 'ESPERANDO_LOGIN'}, false),
  error: U.describirPill({estado: 'ERROR', estado_detalle: 'selectores rotos'}, false),
  detenido: U.describirPill({estado: 'DETENIDO'}, false),
  deteniendo: U.describirPill({estado: 'CORRIENDO'}, true),
}));
""")
    # §8: «CORRIENDO / DETENIDO» → «En ejecución / Detenido» (y el resto de los estados, en el mismo tono)
    assert "En ejecución" in out["corriendo"]["texto"]
    assert "En pausa" in out["pausa"]["texto"] and "próxima vuelta" in out["pausa"]["texto"]
    assert re.search(r"\d+:\d{2}", out["pausa"]["texto"])
    assert "inicies sesión" in out["login"]["texto"]
    assert (out["error"]["texto"], out["error"]["title"]) == (
        "Error del Radar. Reintenta en el próximo ciclo", "selectores rotos"
    )
    assert "Detenido" in out["detenido"]["texto"]
    assert "Deteniendo" in out["deteniendo"]["texto"]


@requiere_node
def test_util_botones_por_estado():
    out = _node("""
console.log(JSON.stringify({
  listo: U.accionesPara({estado: 'LISTO', postulacion_id: 5}),
  listoAprobado: U.accionesPara({estado: 'LISTO', postulacion_id: 5, aprobado_at: '2026-01-01 10:00:00'}),
  generando: U.accionesPara({estado: 'GENERANDO', postulacion_id: 5}),
  descartado: U.accionesPara({estado: 'DESCARTADO', postulacion_id: null}),
  error: U.accionesPara({estado: 'ERROR', postulacion_id: 5}),
  enviado: U.accionesPara({estado: 'ENVIADO', postulacion_id: 5}),
  enviando: U.accionesPara({estado: 'ENVIANDO', postulacion_id: 5}),
}));
""")
    assert out["listo"] == {
        "aprobar": True, "descartar": True, "regenerar": True, "analizar": False, "generarCv": False,
        "reintentar": False, "editor": True, "editable": True,
    }
    assert out["listoAprobado"]["aprobar"] is False and out["listoAprobado"]["editable"] is False
    # GENERANDO: nunca "Abrir en editor completo" (la postulación está PENDING/PROCESSING)
    assert out["generando"]["editor"] is False and out["generando"]["aprobar"] is False
    assert out["descartado"]["generarCv"] is True and out["descartado"]["editor"] is False
    assert out["descartado"]["analizar"] is True
    assert out["error"]["reintentar"] is True and out["error"]["aprobar"] is False
    assert out["enviado"]["descartar"] is False and out["enviado"]["editable"] is False
    assert out["enviando"]["descartar"] is False and out["enviando"]["aprobar"] is False


@requiere_node
def test_util_detalle_error_shapes():
    out = _node("""
console.log(JSON.stringify([
  U.mensajeDeDetalle('texto simple'),
  U.mensajeDeDetalle({duplicate: true, email: 'a@b.test'}),
  U.mensajeDeDetalle([{msg: 'campo inválido'}]),
  U.mensajeDeDetalle(undefined),
]));
""")
    assert out[0] == "texto simple"
    assert "a@b.test" in out[1]
    assert "campo inválido" in out[2]
    assert isinstance(out[3], str) and out[3]


# --- Guardado previo a las acciones: un PATCH fallido nunca debe dejar pasar un approve ---

NODE_APROBAR_PRELUDE = r"""
const llamadas = [];
const respuestas = [];  // cola de {status, body} o 'red' (error de red) para cada fetch
global.fetch = async (url, opts) => {
  const metodo = (opts && opts.method) || 'GET';
  llamadas.push(metodo + ' ' + url);
  const r = respuestas.shift();
  if (r === 'red') throw new TypeError('network down');
  return { ok: r.status < 400, status: r.status, json: async () => r.body };
};
const elementos = {};
const falso = () => ({ hidden: true, textContent: '', className: '', value: '', children: [], handlers: {}, appendChild(c) { this.children.push(c); }, addEventListener(ev, fn) { this.handlers[ev] = fn; } });
global.document.getElementById = (id) => {
  if (['radarMensaje', 'radarMensajeTexto', 'radarGuardado', 'radarDescartarBtn',
       'radarAprobarModal', 'radarAprobarLista', 'radarAprobarConfirmarBtn'].includes(id)) {
    return elementos[id] || (elementos[id] = falso());
  }
  return null;
};
global.document.createElement = () => falso();
global.document.createTextNode = (t) => ({ textContent: t });
global.window.confirm = () => true;
const T = window.Radar._test;
const post = { id: 1, estado: 'LISTO', email_subject: 'viejo', email_body: 'b', email_to: 'a@b.test', emails: ['a@b.test'], texto: 't' };
T.S.selId = 1;
T.S.detalle = { ...post };
// «Aprobar todos» pide revisión previa: aca se marcan todas las filas y se confirma
async function aprobarTodosOk() {
  const p = T.aprobarTodos();
  if (T.S.aprobar) {
    T.S.aprobar.filas.forEach((f) => { f.check.checked = true; });
    T.cerrarAprobar(true);
  }
  await p;
}
const mensaje = () => (elementos.radarMensajeTexto ? elementos.radarMensajeTexto.children.map((c) => c.textContent).join(' ') : '');
"""


@requiere_node
def test_failed_patch_blocks_approve():
    out = _node(NODE_APROBAR_PRELUDE + """
(async () => {
  T.programarEdicion('email_subject', 'asunto editado');
  respuestas.push('red');                      // el PATCH falla por red
  await T.aprobar(false);
  const trasRed = { llamadas: llamadas.slice(), msg: mensaje(), pendiente: !!T.S.edit };

  llamadas.length = 0;
  T.programarEdicion('email_subject', 'asunto editado 2');
  respuestas.push({ status: 500, body: { detail: 'boom' } });  // 5xx
  await T.aprobar(false);
  const tras5xx = { llamadas: llamadas.slice() };

  llamadas.length = 0;
  T.programarEdicion('email_subject', 'asunto editado 3');
  respuestas.push({ status: 422, body: { detail: 'Destinatario inválido' } });  // 422
  await T.aprobar(false);
  const tras422 = { llamadas: llamadas.slice() };

  console.log(JSON.stringify({ trasRed, tras5xx, tras422 }));
  process.exit(0);
})();
""")
    for clave in ("trasRed", "tras5xx", "tras422"):
        assert out[clave]["llamadas"] == ["PATCH /api/radar/posts/1"], out[clave]
    assert "pudieron guardar" in out["trasRed"]["msg"]
    assert out["trasRed"]["pendiente"] is True, "el cambio fallido debe seguir pendiente"


@requiere_node
def test_successful_patch_then_approve():
    out = _node(NODE_APROBAR_PRELUDE + """
(async () => {
  T.programarEdicion('email_subject', 'asunto editado');
  respuestas.push({ status: 200, body: { ...post, email_subject: 'asunto editado' } });   // PATCH ok
  respuestas.push({ status: 200, body: { ...post, email_subject: 'asunto editado', aprobado_at: '2026-11-01 10:00:00' } });  // approve ok
  await T.aprobar(false);
  console.log(JSON.stringify({ llamadas, aprobado: T.S.detalle.aprobado_at, pendiente: !!T.S.edit }));
  process.exit(0);
})();
""")
    assert out["llamadas"] == ["PATCH /api/radar/posts/1", "POST /api/radar/posts/1/approve"]
    assert out["aprobado"] == "2026-11-01 10:00:00"
    assert out["pendiente"] is False


@requiere_node
def test_approve_without_pending_edit_goes_straight_through():
    out = _node(NODE_APROBAR_PRELUDE + """
(async () => {
  respuestas.push({ status: 200, body: { ...post, aprobado_at: '2026-11-01 10:00:00' } });
  await T.aprobar(false);
  console.log(JSON.stringify({ llamadas }));
  process.exit(0);
})();
""")
    assert out["llamadas"] == ["POST /api/radar/posts/1/approve"]


@requiere_node
def test_descartar_ofrece_deshacer_que_restaura_una_sola_vez():
    out = _node(NODE_APROBAR_PRELUDE + """
const timeouts = [];
global.setTimeout = (fn, ms) => { timeouts.push(ms); return timeouts.length; };
(async () => {
  T.S.posts.set(1, { ...post });
  await T.descartar();  // 1er clic: pide confirmación
  respuestas.push({ status: 200, body: { ...post, estado: 'DESCARTADO' } });
  await T.descartar();  // 2do clic: descarta
  const principal = elementos.radarMensajeTexto.children[0];
  const boton = principal.children[0];
  const antes = { msg: mensaje(), texto: boton.textContent, ms: timeouts[timeouts.length - 1] };
  llamadas.length = 0;
  respuestas.push({ status: 200, body: { ...post, estado: 'LISTO' } });
  boton.handlers.click();
  await new Promise((r) => setImmediate(r));
  console.log(JSON.stringify({ antes, deshabilitado: boton.disabled, llamadas, estado: T.S.detalle.estado, msgFinal: mensaje() }));
  process.exit(0);
})();
""")
    assert out["antes"]["texto"] == "Deshacer"
    assert "Oferta descartada" in out["antes"]["msg"]
    assert out["antes"]["ms"] == 10000
    assert out["deshabilitado"] is True
    assert out["llamadas"] == ["POST /api/radar/posts/1/restaurar"]
    assert out["estado"] == "LISTO"
    assert "restaurada" in out["msgFinal"]


@requiere_node
def test_descartar_un_post_ya_descartado_no_ofrece_deshacer():
    out = _node(NODE_APROBAR_PRELUDE + """
global.setTimeout = () => 1;
(async () => {
  T.S.posts.set(1, { ...post, estado: 'DESCARTADO' });
  T.S.detalle = { ...post, estado: 'DESCARTADO' };
  await T.descartar();  // 1er clic: pide confirmación
  respuestas.push({ status: 200, body: { ...post, estado: 'DESCARTADO' } });
  await T.descartar();  // 2do clic: descarta (ya lo estaba)
  const principal = elementos.radarMensajeTexto.children[0];
  console.log(JSON.stringify({ msg: mensaje(), botones: principal.children.length }));
  process.exit(0);
})();
""")
    assert "descartada" in out["msg"] and "Deshacer" not in out["msg"]
    assert out["botones"] == 0  # sin botón «Deshacer»


@requiere_node
def test_failed_patch_blocks_approve_all_and_discard():
    out = _node(NODE_APROBAR_PRELUDE + """
(async () => {
  T.S.posts.set(1, { ...post });
  T.programarEdicion('email_body', 'cuerpo editado');
  respuestas.push('red');
  await aprobarTodosOk();
  const todos = llamadas.slice();
  llamadas.length = 0;
  respuestas.push('red');
  await T.descartar();  // 1er clic: solo pide confirmación
  const confirmar = llamadas.slice();
  await T.descartar();  // 2do clic: descarta
  console.log(JSON.stringify({ todos, confirmar, descartar: llamadas.slice() }));
  process.exit(0);
})();
""")
    assert out["todos"] == ["PATCH /api/radar/posts/1"]
    assert out["confirmar"] == []
    assert out["descartar"] == ["PATCH /api/radar/posts/1"]


@requiere_node
def test_retry_of_interrupted_or_uncertain_send_asks_confirmation():
    out = _node(NODE_APROBAR_PRELUDE + """
(async () => {
  const res = {};
  const preguntas = [];
  for (const [clave, error, acepta] of [
    ['interrumpido', 'envío interrumpido, verificar en Gmail antes de reintentar', false],
    ['incierto', 'Envío incierto: verificá en Gmail (Enviados) antes de reintentar (corte)', false],
    ['inciertoAcepta', 'Envío incierto: verificá en Gmail (Enviados) antes de reintentar', true],
    ['otro', 'Falló la generación: boom', false],
  ]) {
    llamadas.length = 0;
    window.confirm = (m) => { preguntas.push(m); return acepta; };
    T.S.detalle = { ...post, estado: 'ERROR', error };
    respuestas.push({ status: 200, body: { ...post, estado: 'LISTO', error: null } });
    await T.reintentar();
    respuestas.length = 0;
    res[clave] = llamadas.slice();
  }
  console.log(JSON.stringify({ res, preguntas }));
  process.exit(0);
})();
""")
    assert out["res"]["interrumpido"] == []
    assert out["res"]["incierto"] == []
    assert out["res"]["inciertoAcepta"] == ["POST /api/radar/posts/1/retry"]
    assert out["res"]["otro"] == ["POST /api/radar/posts/1/retry"]
    assert len(out["preguntas"]) == 3
    assert all("puede haberse enviado ya" in q for q in out["preguntas"])


@requiere_node
def test_mutating_calls_always_send_json_body():
    """Sin cuerpo JSON un POST sería "simple" (sin consulta previa de CORS) y otro sitio podría hacerlo por CSRF."""
    out = _node(NODE_APROBAR_PRELUDE + """
const vistos = [];
const fetchPrevio = global.fetch;
global.fetch = async (url, opts) => {
  vistos.push({ m: (opts && opts.method) || 'GET', ct: opts && opts.headers && opts.headers['Content-Type'], body: opts && opts.body });
  return fetchPrevio(url, opts);
};
(async () => {
  respuestas.push({ status: 200, body: { ...post, estado: 'DESCARTADO' } });
  await T.descartar();
  await T.descartar();
  T.S.detalle = { ...post, estado: 'ERROR', error: 'otro' };
  respuestas.push({ status: 200, body: { ...post } });
  await T.reintentar();
  console.log(JSON.stringify(vistos));
  process.exit(0);
})();
""")
    mutantes = [v for v in out if v["m"] != "GET"]
    assert len(mutantes) == 2
    for v in mutantes:
        assert v["ct"] == "application/json"
    # Descartar dice desde dónde; el resto manda un JSON vacío
    assert [v["body"] for v in mutantes] == ['{"desde":"detalle"}', "{}"]


NODE_FORMULARIO = r"""
const campos = {
  radarEmailTo: { value: 'a@b.test', disabled: false },
  radarEmailSubject: { value: 'viejo', disabled: false },
  radarEmailBody: { value: 'b', disabled: false },
};
const getPrevio = global.document.getElementById;
global.document.getElementById = (id) => campos[id] || getPrevio(id);
const cuerpos = [];
const fetchPrevio2 = global.fetch;
global.fetch = async (url, opts) => {
  cuerpos.push({ url, m: (opts && opts.method) || 'GET', body: opts && opts.body ? JSON.parse(opts.body) : null });
  return fetchPrevio2(url, opts);
};
"""


@requiere_node
@pytest.mark.parametrize("editar,confirmar", [(False, False), (True, False), (False, True)])
def test_aprobar_individual_manda_version_revisada(editar, confirmar):
    out = _node(NODE_APROBAR_PRELUDE + NODE_FORMULARIO + """
(async () => {
  T.S.detalle.updated_at = 'version-vista';
  // La lista puede tener una versión más nueva que el detalle que se revisó.
  T.S.posts.set(1, { ...post, updated_at: 'version-no-revisada' });
  if (EDITAR) {
    campos.radarEmailSubject.value = 'asunto nuevo';
    respuestas.push({ status: 200, body: { ...post, email_subject: 'asunto nuevo', updated_at: 'version-guardada' } });
  }
  respuestas.push({ status: 200, body: { ...post, aprobado_at: 'aprobado' } });
  await T.aprobar(CONFIRMAR);
  console.log(JSON.stringify(cuerpos.filter(c => c.url.endsWith('/approve'))));
  process.exit(0);
})();
""".replace("EDITAR", json.dumps(editar)).replace("CONFIRMAR", json.dumps(confirmar)))
    assert len(out) == 1
    assert out[0]["body"]["updated_at"] == ("version-guardada" if editar else "version-vista")
    assert out[0]["body"].get("confirm_duplicate", False) is confirmar


@requiere_node
def test_approve_saves_typed_recipient_even_without_change_event():
    """Safari: el destinatario escrito puede no haber disparado `change` antes del clic en Aprobar."""
    out = _node(NODE_APROBAR_PRELUDE + NODE_FORMULARIO + """
(async () => {
  campos.radarEmailTo.value = 'yo@mio.test';         // escrito, sin evento
  campos.radarEmailSubject.value = 'asunto nuevo';  // idem
  respuestas.push({ status: 200, body: { ...post, email_to: 'yo@mio.test', email_subject: 'asunto nuevo' } });
  respuestas.push({ status: 200, body: { ...post, email_to: 'yo@mio.test', aprobado_at: '2026-11-01 10:00:00' } });
  await T.aprobar(false);
  const conCambios = cuerpos.slice();
  cuerpos.length = 0;
  llamadas.length = 0;
  // Sin diferencias: va directo al approve
  T.S.detalle = { ...post, email_to: 'yo@mio.test', email_subject: 'asunto nuevo' };
  respuestas.push({ status: 200, body: { ...post, aprobado_at: '2026-11-01 10:00:00' } });
  await T.aprobar(false);
  console.log(JSON.stringify({ conCambios, sinCambios: llamadas.slice() }));
  process.exit(0);
})();
""")
    patch, approve = out["conCambios"]
    assert patch["m"] == "PATCH" and patch["url"] == "/api/radar/posts/1"
    assert patch["body"] == {"email_to": "yo@mio.test", "email_subject": "asunto nuevo"}
    assert approve["m"] == "POST" and approve["url"] == "/api/radar/posts/1/approve"
    assert out["sinCambios"] == ["POST /api/radar/posts/1/approve"]


@requiere_node
def test_approve_all_sends_counted_ids_with_updated_at():
    out = _node(NODE_APROBAR_PRELUDE + NODE_FORMULARIO + """
(async () => {
  T.S.posts.set(1, { ...post, updated_at: '2026-11-01 09:00:00.100' });
  T.S.posts.set(2, { ...post, id: 2, updated_at: '2026-11-01 09:00:00.200' });
  T.S.posts.set(3, { ...post, id: 3, aprobado_at: 'x', updated_at: 'z' });     // ya aprobado: no se cuenta
  T.S.posts.set(4, { ...post, id: 4, estado: 'GENERANDO', updated_at: 'z' });  // no listo
  campos.radarEmailTo.value = 'yo@mio.test';  // el seleccionado (1) tiene un cambio sin guardar
  respuestas.push({ status: 200, body: { ...post, email_to: 'yo@mio.test', updated_at: '2026-11-01 09:05:00.000' } });
  respuestas.push({ status: 200, body: { encolados: [1, 2], omitidos: [] } });
  respuestas.push({ status: 200, body: [] });   // cargarPosts
  respuestas.push({ status: 200, body: post }); // cargarDetalle
  await aprobarTodosOk();
  console.log(JSON.stringify(cuerpos.slice(0, 2)));
  process.exit(0);
})();
""")
    patch, todos = out
    assert patch["m"] == "PATCH" and patch["body"] == {"email_to": "yo@mio.test"}
    assert todos["url"] == "/api/radar/approve-all"
    assert sorted(todos["body"]["ids"]) == [1, 2]
    # El post editado por el propio usuario viaja con el updated_at de su PATCH
    assert todos["body"]["updated_at"] == {"1": "2026-11-01 09:05:00.000", "2": "2026-11-01 09:00:00.200"}


@requiere_node
def test_typing_incomplete_recipient_does_not_autosave():
    out = _node(NODE_APROBAR_PRELUDE + """
(async () => {
  T.programarEdicion('email_to', 'juan@');
  await new Promise((r) => setTimeout(r, 800));  // más que el debounce
  const tras = llamadas.slice();
  T.programarEdicion('email_to', 'juan@empresa.test');
  respuestas.push({ status: 200, body: { ...post, email_to: 'juan@empresa.test' } });
  await new Promise((r) => setTimeout(r, 800));
  console.log(JSON.stringify({ tras, despues: llamadas.slice() }));
  process.exit(0);
})();
""")
    assert out["tras"] == []
    assert out["despues"] == ["PATCH /api/radar/posts/1"]


def test_radar_js_listens_input_on_recipient():
    js = RADAR_JS.read_text(encoding="utf-8")
    assert re.search(r"\['input', 'change'\]\.forEach\(\(ev\) => \$\('radarEmailTo'\)\.addEventListener", js)


@requiere_node
def test_util_alcance_filter_and_revisar():
    out = _node("""
const posts = [
  {id: 1, estado: 'LISTO', alcance: 'ARGENTINA'}, {id: 2, estado: 'LISTO', alcance: 'EXTERIOR_COMPATIBLE'},
  {id: 3, estado: 'DESCARTADO', alcance: 'EXTERIOR'}, {id: 4, estado: 'REVISAR', alcance: 'DESCONOCIDO'},
  {id: 5, estado: 'NUEVO'},
];
const ids = (a) => posts.filter((p) => U.coincideAlcance(p, a)).map((p) => p.id);
console.log(JSON.stringify({
  TODAS: ids('TODAS'), AR: ids('ARGENTINA'), EXT: ids('EXTERIOR_COMPATIBLE'),
  REVISAR: posts.filter((p) => U.coincideFiltro(p, 'REVISAR')).map((p) => p.id),
  generarRevisar: U.accionesPara({estado: 'REVISAR'}).generarCv,
  descartarRevisar: U.accionesPara({estado: 'REVISAR'}).descartar,
  etiquetas: ['ARGENTINA', 'EXTERIOR_COMPATIBLE', 'EXTERIOR', 'DESCONOCIDO', ''].map(U.etiquetaAlcance),
}));
""")
    assert out["TODAS"] == [1, 2, 3, 4, 5]
    assert out["AR"] == [1]
    assert out["EXT"] == [2]
    assert out["REVISAR"] == [4]
    assert out["generarRevisar"] is True and out["descartarRevisar"] is True
    assert out["etiquetas"][0].endswith("Argentina")
    assert "Exterior compatible" in out["etiquetas"][1]
    assert "Sin ubicación" in out["etiquetas"][3]
    assert out["etiquetas"][4] == ""


# --- Task 7: dos paneles (búsqueda global / Mi feed) y acciones por canal ---

IDS_PANELES_Y_CANAL = [
    "radarPanelBusqueda", "radarPanelFeed", "radarLista", "radarListaFeed", "radarCanalAcciones",
    "radarCopiarMensajeBtn", "radarDescargarCvBtn", "radarAbrirDestinoBtn", "radarMarcarPostuladoBtn",
]


def test_radar_two_panels_inside_list_column(soup):
    vista = soup.find(id="vista-radar")
    for dom_id in IDS_PANELES_Y_CANAL:
        assert vista.find(id=dom_id) is not None, f"Falta #{dom_id} dentro de #vista-radar"
    columna = vista.find(id="radar-panel-lista")
    busqueda, feed = columna.find(id="radarPanelBusqueda"), columna.find(id="radarPanelFeed")
    assert busqueda is not None and feed is not None
    assert busqueda.find(id="radarLista") is not None and feed.find(id="radarListaFeed") is not None
    assert busqueda.find(id="radarListaFeed") is None and feed.find(id="radarLista") is None
    # Rediseño E2: mismo contenedor (.listas); una pestaña cada uno también en escritorio (#radarTabsLista)
    assert busqueda.parent is feed.parent
    assert "listas" in busqueda.parent.get("class", [])


def test_radar_channel_actions_live_in_email_column(soup):
    columna = soup.find(id="radar-panel-email")
    acciones = columna.find(id="radarCanalAcciones")
    assert acciones is not None
    for dom_id in ("radarCopiarMensajeBtn", "radarDescargarCvBtn", "radarAbrirDestinoBtn"):
        boton = acciones.find(id=dom_id)
        assert boton is not None and boton.name == "button" and boton.get("type") == "button"
        assert boton.has_attr("hidden"), f"#{dom_id} debe iniciar oculto"
    # Listo va con las acciones principales: también sirve para un email sin destinatario
    listo = columna.find(id="radarAcciones").find(id="radarMarcarPostuladoBtn")
    assert listo is not None and listo.has_attr("hidden") and "Ya me postulé" in listo.get_text()


@requiere_node
def test_radar_default_chip_is_relevantes(soup):
    chips = soup.find(id="radarFiltros").find_all("button")
    assert chips[0].get("data-estado") == "RELEVANTES"
    activos = [b.get("data-estado") for b in chips if b.get("aria-pressed") == "true"]  # rediseño E2
    assert activos == ["RELEVANTES"]
    assert _node("console.log(JSON.stringify(window.Radar._test.S.filtro));") == "RELEVANTES"


@requiere_node
def test_util_coincide_filtro_relevantes_y_pendientes():
    out = _node("""
const estados = ['NUEVO', 'REVISAR', 'GENERANDO', 'LISTO', 'ENVIANDO', 'ENVIADO', 'DESCARTADO', 'ERROR'];
const por = (f) => estados.filter((e) => U.coincideFiltro({estado: e}, f));
console.log(JSON.stringify({ rel: por('RELEVANTES'), pend: por('PENDIENTES'), todos: por('TODOS'), listo: por('LISTO') }));
""")
    assert out["rel"] == ["REVISAR", "GENERANDO", "LISTO", "ENVIANDO", "ENVIADO", "ERROR"]
    assert out["pend"] == ["NUEVO"]
    assert len(out["todos"]) == 8
    assert out["listo"] == ["LISTO"]


@requiere_node
def test_util_icono_canal():
    out = _node("""
console.log(JSON.stringify(['email', 'link', 'whatsapp', 'dm', 'otro', '', undefined, 'raro'].map(U.iconoCanal)));
""")
    assert out == ["✉️", "🔗", "💬", "💼", "❔", "✉️", "✉️", "❔"]


@requiere_node
def test_util_url_destino_whatsapp_solo_digitos_y_texto_codificado():
    out = _node("""
const wa = (d, b) => U.urlDestino({canal: 'whatsapp', destino_canal: d}, b);
console.log(JSON.stringify([
  wa('+54 9 11 5555-1234', 'Hola, & chau ¿qué tal?'), wa('5491155551234', ''), wa('sin numero', 'x'), wa('', 'x'),
]));
""")
    assert out[0] == "https://wa.me/5491155551234?text=Hola%2C%20%26%20chau%20%C2%BFqu%C3%A9%20tal%3F"
    assert out[1] == "https://wa.me/5491155551234?text="
    assert out[2] == "" and out[3] == ""


@requiere_node
def test_util_url_destino_link_solo_https():
    out = _node("""
const l = (d) => U.urlDestino({canal: 'link', destino_canal: d}, 'x');
console.log(JSON.stringify([
  l('https://forms.example.org/job?id=1'), l('http://forms.example.org/job'), l('javascript:alert(1)'),
  l('data:text/html,hi'), l(''), l('ftp://x.com/a'), l('https://'), l(null),
]));
""")
    assert out == ["https://forms.example.org/job?id=1", "", "", "", "", "", "", ""]


@requiere_node
def test_util_url_destino_dm_perfil_linkedin_y_otros_canales():
    out = _node("""
console.log(JSON.stringify([
  U.urlDestino({canal: 'dm', url: 'https://www.linkedin.com/in/ana/', destino_canal: 'https://evil.com'}, 'x'),
  U.urlDestino({canal: 'dm', url: 'https://evil.com/in/ana'}, 'x'),
  U.urlDestino({canal: 'dm', url: 'javascript:alert(1)'}, 'x'),
  U.urlDestino({canal: 'otro', destino_canal: 'https://a.com'}, 'x'),
  U.urlDestino({canal: 'email', destino_canal: 'a@b.test'}, 'x'),
  U.urlDestino({destino_canal: 'https://a.com'}, 'x'),
]));
""")
    assert out == ["https://www.linkedin.com/in/ana/", "", "", "", "", ""]


@requiere_node
def test_util_acciones_por_canal():
    out = _node("""
const base = {estado: 'LISTO', postulacion_id: 5};
console.log(JSON.stringify({
  email: U.accionesPara({...base, canal: 'email'}), legacy: U.accionesPara(base),
  wa: U.accionesPara({...base, canal: 'whatsapp'}),
  canalWa: U.accionesCanal({...base, canal: 'whatsapp'}),
  canalEmail: U.accionesCanal({...base, canal: 'email', email_to: 'rrhh@x.test'}),
  emailSinDestinatario: U.accionesCanal({estado: 'ANALIZADA', canal: 'email', email_to: ''}),
  canalEnviado: U.accionesCanal({estado: 'ENVIADO', canal: 'link'}),
}));
""")
    assert out["email"]["aprobar"] is True and out["legacy"]["aprobar"] is True
    assert out["wa"]["aprobar"] is False and out["wa"]["editable"] is True
    assert out["canalWa"] == {"panel": True, "copiar": True, "marcarPostulado": True}
    assert out["canalEmail"] == {"panel": False, "copiar": False, "marcarPostulado": False}
    assert out["canalEnviado"]["marcarPostulado"] is False and out["canalEnviado"]["panel"] is True
    assert out["emailSinDestinatario"] == {"panel": False, "copiar": False, "marcarPostulado": True}


@requiere_node
def test_approve_all_revision_previa_lists_rows_and_approves_only_checked():
    out = _node(NODE_APROBAR_PRELUDE + NODE_FORMULARIO + """
(async () => {
  T.S.estado.umbral_match = 60;
  T.S.posts.set(1, { ...post, id: 1, updated_at: 'u1', email_to: 'rrhh@uno.test',
    triage: { puesto: 'Dev Python', empresa: 'Uno SA', alcance: 'ARGENTINA' }, analisis: { match_score: 85 } });
  T.S.posts.set(2, { ...post, id: 2, updated_at: 'u2', email_to: 'rrhh@dos.test',
    triage: { puesto: 'Dev Go', empresa: 'Dos Inc', alcance: 'EXTERIOR' }, analisis: { match_score: 40, oferta_cerrada: true } });
  T.S.posts.set(3, { ...post, id: 3, updated_at: 'u3', email_to: 'rrhh@tres.test' });  // sin triage
  respuestas.push({ status: 200, body: { encolados: [1], omitidos: [] } });
  respuestas.push({ status: 200, body: [] });
  respuestas.push({ status: 200, body: post });
  const pendiente = T.aprobarTodos();
  const antes = llamadas.slice();
  const filas = T.S.aprobar.filas.length;
  const hijos = elementos.radarAprobarLista.children.length;
  const marcadas = T.idsMarcados();
  T.actualizarConteoAprobar();
  const boton = elementos.radarAprobarConfirmarBtn.textContent;
  const visible = elementos.radarAprobarModal.hidden;
  const datos = [1, 2, 3].map((id) => T.datosAprobacion(T.S.posts.get(id)));
  T.cerrarAprobar(true);
  await pendiente;
  const cuerpo = cuerpos.filter((c) => c.url.endsWith('/approve-all'))[0].body;
  console.log(JSON.stringify({ antes, filas, hijos, marcadas, boton, visible, datos, cuerpo,
    cerrado: elementos.radarAprobarModal.hidden, abierta: T.S.aprobar }));
  process.exit(0);
})();
""")
    assert out["antes"] == [], "no se llama a la API hasta confirmar"
    assert out["filas"] == 3 and out["hijos"] == 3
    assert out["marcadas"] == [1] and out["boton"] == "Aprobar 1"
    assert out["visible"] is False
    d1, d2, d3 = out["datos"]
    assert d1["titulo"] == "Dev Python · Uno SA" and d1["destinatario"] == "rrhh@uno.test"
    assert d1["ubicacion"] == "Argentina" and d1["afinidad"] == 85 and d1["alertas"] == [] and d1["marcada"] is True
    assert d2["ubicacion"] == "Exterior" and d2["marcada"] is False
    assert d2["alertas"] == ["Exterior", "Afinidad bajo el mínimo", "Oferta cerrada"]
    assert d3["ubicacion"] == "Sin ubicación" and d3["marcada"] is False
    assert d3["alertas"] == ["Sin ubicación"] and d3["afinidad"] is None
    assert out["cuerpo"]["ids"] == [1]
    assert out["cuerpo"]["updated_at"] == {"1": "u1"}
    assert out["cerrado"] is True and out["abierta"] is None


@requiere_node
def test_approve_all_revision_previa_cancel_and_empty_selection_send_nothing():
    out = _node(NODE_APROBAR_PRELUDE + NODE_FORMULARIO + """
(async () => {
  T.S.posts.set(1, { ...post, id: 1, updated_at: 'u1', triage: { alcance: 'ARGENTINA' } });
  T.S.posts.set(2, { ...post, id: 2, updated_at: 'u2', triage: { alcance: 'EXTERIOR' } });
  const cancelada = T.aprobarTodos();
  T.cerrarAprobar(false);
  await cancelada;
  const trasCancelar = llamadas.slice();
  const abiertaTras = T.S.aprobar;
  const otra = T.aprobarTodos();
  T.S.aprobar.filas.forEach((f) => { f.check.checked = false; });
  T.actualizarConteoAprobar();
  const sinMarcar = { boton: elementos.radarAprobarConfirmarBtn.textContent, deshabilitado: elementos.radarAprobarConfirmarBtn.disabled };
  T.cerrarAprobar(true);  // con 0 marcadas no cierra ni confirma
  const sigueAbierta = !!T.S.aprobar;
  T.cerrarAprobar(false);
  await otra;
  console.log(JSON.stringify({ trasCancelar, abiertaTras, sinMarcar, sigueAbierta, final: llamadas.slice() }));
  process.exit(0);
})();
""")
    assert out["trasCancelar"] == [] and out["abiertaTras"] is None
    assert out["sinMarcar"] == {"boton": "Aprobar 0", "deshabilitado": True}
    assert out["sigueAbierta"] is True
    assert out["final"] == []


@requiere_node
def test_el_fondo_del_modal_de_aprobar_no_se_cierra_con_el_doble_clic_del_boton():
    out = _node(NODE_APROBAR_PRELUDE + """
(async () => {
  T.S.posts.set(1, { ...post, id: 1, updated_at: 'u1', email_to: 'rrhh@uno.test' });
  const fondo = elementos.radarAprobarModal || (elementos.radarAprobarModal = falso());
  const dentro = {};
  const real = Date.now;
  let ahora = 1000000;
  Date.now = () => ahora;
  const pendiente = T.aprobarTodos();
  const resultado = {};
  // Doble clic en «Aprobar…»: el 2.º clic cae en el fondo recién abierto
  ahora += 120;
  T.pulsarFondoAprobar({ target: fondo });
  T.clicFondoAprobar({ target: fondo });
  resultado.trasDobleClic = !!T.S.aprobar;
  // Apretar adentro y soltar en el fondo (arrastrar una selección) tampoco cierra
  ahora += 2000;
  T.pulsarFondoAprobar({ target: dentro });
  T.clicFondoAprobar({ target: fondo });
  resultado.trasArrastre = !!T.S.aprobar;
  // Un clic de verdad en el fondo (apretar y soltar ahí, ya pasado el instante) cierra
  T.pulsarFondoAprobar({ target: fondo });
  T.clicFondoAprobar({ target: fondo });
  resultado.trasClicReal = !!T.S.aprobar;
  Date.now = real;
  await pendiente;
  console.log(JSON.stringify(resultado));
  process.exit(0);
})();
""")
    assert out == {"trasDobleClic": True, "trasArrastre": True, "trasClicReal": False}


def test_radar_aprobar_modal_markup(soup):
    radar = soup.find(id="vista-radar")
    modal = radar.find(id="radarAprobarModal")
    assert modal is not None and modal.has_attr("hidden")
    for ident in ("radarAprobarLista", "radarAprobarConfirmarBtn", "radarAprobarCancelarBtn"):
        assert modal.find(id=ident) is not None, ident
    assert "window.confirm" not in RADAR_JS.read_text(encoding="utf-8").split("async function aprobarTodos")[1].split("async function alternarRadar")[0]


@requiere_node
def test_approve_all_counts_only_email_channel():
    out = _node(NODE_APROBAR_PRELUDE + NODE_FORMULARIO + """
(async () => {
  T.S.posts.set(1, { ...post, updated_at: 'u1' });
  T.S.posts.set(2, { ...post, id: 2, canal: 'whatsapp', updated_at: 'u2' });
  T.S.posts.set(3, { ...post, id: 3, canal: 'link', updated_at: 'u3' });
  T.S.posts.set(4, { ...post, id: 4, canal: 'email', updated_at: 'u4' });
  respuestas.push({ status: 200, body: { encolados: [1, 4], omitidos: [] } });
  respuestas.push({ status: 200, body: [] });
  respuestas.push({ status: 200, body: post });
  await aprobarTodosOk();
  console.log(JSON.stringify(cuerpos.filter((c) => c.url.endsWith('/approve-all'))[0].body));
  process.exit(0);
})();
""")
    assert sorted(out["ids"]) == [1, 4]
    assert out["updated_at"] == {"1": "u1", "4": "u4"}


@requiere_node
def test_non_email_channel_never_patches_email_to_nor_approves():
    out = _node(NODE_APROBAR_PRELUDE + """
(async () => {
  T.S.detalle = { ...post, canal: 'whatsapp', email_to: '5491155551234', destino_canal: '5491155551234', email_subject: '' };
  T.programarEdicion('email_to', 'otra@cosa.test');
  T.programarEdicion('email_subject', 'asunto');
  await new Promise((r) => setTimeout(r, 800));
  const sinEdicion = { llamadas: llamadas.slice(), edit: !!T.S.edit };
  T.programarEdicion('email_body', 'mensaje nuevo');
  respuestas.push({ status: 200, body: { ...T.S.detalle, email_body: 'mensaje nuevo' } });
  await new Promise((r) => setTimeout(r, 800));
  const conCuerpo = llamadas.slice();
  llamadas.length = 0;
  await T.aprobar(false);
  console.log(JSON.stringify({ sinEdicion, conCuerpo, aprobar: llamadas.slice() }));
  process.exit(0);
})();
""")
    assert out["sinEdicion"] == {"llamadas": [], "edit": False}
    assert out["conCuerpo"] == ["PATCH /api/radar/posts/1"]
    assert out["aprobar"] == []


@requiere_node
def test_listo_asks_confirmation_on_the_same_button_then_posts():
    out = _node(NODE_APROBAR_PRELUDE + """
const cuerpos = [];
const fetchPrevio3 = global.fetch;
global.fetch = async (url, opts) => {
  cuerpos.push({ url, m: (opts && opts.method) || 'GET', body: opts && opts.body });
  return fetchPrevio3(url, opts);
};
(async () => {
  T.S.detalle = { ...post, canal: 'link', destino_canal: 'https://x.org/j', email_to: 'https://x.org/j' };
  await T.marcarPostulado();  // 1er clic: solo cambia el botón a «¿Ya te postulaste?»
  const rechazado = llamadas.slice();
  respuestas.push({ status: 200, body: { ...T.S.detalle, estado: 'ENVIADO', message_id: 'manual:link' } });
  await T.marcarPostulado();
  // un post de email nunca llama al endpoint manual
  T.S.detalle = { ...post };
  const antes = llamadas.length;
  await T.marcarPostulado();
  console.log(JSON.stringify({ rechazado, llamadas: llamadas.slice(), cuerpos: cuerpos.slice(), emailSinLlamada: llamadas.length === antes, estado: T.S.posts.get(1).estado }));
  process.exit(0);
})();
""")
    assert out["rechazado"] == []
    assert out["llamadas"] == ["POST /api/radar/posts/1/marcar-postulado"]
    assert out["cuerpos"][0]["body"] == "{}"
    assert out["emailSinLlamada"] is True
    assert out["estado"] == "ENVIADO"


def test_radar_canal_acciones_no_trae_flex_en_el_markup(soup):
    """`flex` de Tailwind pisa el atributo `hidden`: el panel de acciones lo recibe por JS al mostrarse."""
    acciones = soup.find(id="radarCanalAcciones")
    assert acciones.has_attr("hidden") and "flex" not in acciones.get("class", [])
    js = RADAR_JS.read_text(encoding="utf-8")
    assert "classList.toggle('flex'" in js


NODE_DOM_FALSO = r"""
const nodos = {};
const falso = (id) => {
  const clases = new Set();
  const attrs = {};
  let _text = '';
  return { id, hidden: true, className: '', value: '', disabled: false, checked: false, children: [],
    get textContent() {
      if (!this.children || this.children.length === 0) return _text;
      return _text + this.children.map(c => (c && c.textContent) || '').join(' ');
    },
    set textContent(v) { _text = String(v); this.children = []; },
    setAttribute(k, v) { attrs[k] = String(v); },
    getAttribute(k) { return attrs[k]; },
    appendChild(c) { if (c) { c.parent = this; this.children.push(c); } },
    insertBefore(c, ref) {
      if (!c) return;
      c.parent = this;
      const idx = this.children.indexOf(ref);
      if (idx >= 0) this.children.splice(idx, 0, c);
      else this.children.push(c);
    },
    remove() {
      if (this.parent && this.parent.children) {
        const idx = this.parent.children.indexOf(this);
        if (idx >= 0) this.parent.children.splice(idx, 1);
      }
    },
    querySelector(sel) {
      return this.querySelectorAll(sel)[0] || null;
    },
    querySelectorAll(sel) {
      const res = [];
      const attrMatch = /^\[([a-zA-Z0-9_-]+)(?:="([^"]*)")?\]$/.exec(sel);
      const walk = (n) => {
        for (const c of (n.children || [])) {
          if (!c) continue;
          if (attrMatch) {
            const val = c.getAttribute ? c.getAttribute(attrMatch[1]) : undefined;
            if (val !== undefined && (attrMatch[2] === undefined || val === attrMatch[2])) res.push(c);
          } else if ((c.tag && c.tag.toLowerCase() === sel.toLowerCase()) || c.id === sel) {
            res.push(c);
          }
          walk(c);
        }
      };
      walk(this);
      return res;
    },
    classList: { toggle(c, on) { if (on) clases.add(c); else clases.delete(c); }, contains: (c) => clases.has(c) } };
};
global.document.getElementById = (id) => nodos[id] || (nodos[id] = falso(id));
global.document.createElement = (tag) => { const f = falso('nuevo'); f.tag = tag; return f; };
global.document.createDocumentFragment = () => falso('fragment');
global.document.createTextNode = (t) => ({ textContent: t });
global.document.querySelectorAll = () => [];
const T = window.Radar._test;
"""


@requiere_node
def test_util_url_destino_con_surrogate_suelto_no_lanza():
    out = _node(r"""
const suelto = String.fromCharCode(0xD800);
console.log(JSON.stringify([
  U.urlDestino({canal: 'whatsapp', destino_canal: '5491155551234'}, 'hola ' + suelto),
  U.urlDestino({canal: 'whatsapp', destino_canal: '5491155551234'}, 'hola'),
]));
""")
    assert out == ["", "https://wa.me/5491155551234?text=hola"]


@requiere_node
def test_util_tablas_de_canal_ignoran_claves_heredadas():
    out = _node("""
const raros = ['constructor', 'toString', '__proto__', 'hasOwnProperty'];
console.log(JSON.stringify({
  iconos: raros.map(U.iconoCanal), nombres: raros.map(U.nombreCanal),
  conocidos: [U.nombreCanal('whatsapp'), U.nombreCanal(''), U.nombreCanal('dm')],
}));
""")
    assert out["iconos"] == ["❔"] * 4
    assert out["nombres"] == ["Otro"] * 4
    assert out["conocidos"] == ["WhatsApp", "Mail", "Mensaje directo"]  # §8


@requiere_node
def test_render_email_oculta_el_pdf_en_canales_sin_email():
    out = _node(NODE_DOM_FALSO + """
const base = { id: 1, estado: 'LISTO', postulacion_id: 5, email_body: 'm', email_to: '5491155551234', destino_canal: '5491155551234' };
const pdf = () => nodos.radarVerPdf.hidden;
T.S.selId = 1;
T.S.detalle = { ...base, canal: 'whatsapp', postulacion: { status: 'READY' } };
T.renderEmail(true);
const wa = { pdf: pdf(), acciones: nodos.radarCanalAcciones.hidden, flex: nodos.radarCanalAcciones.classList.contains('flex') };
T.S.detalle = { ...base, canal: 'email', emails: ['a@b.test'], email_to: 'a@b.test', postulacion: { status: 'READY' } };
T.renderEmail(true);
const em = { pdf: pdf(), acciones: nodos.radarCanalAcciones.hidden, flex: nodos.radarCanalAcciones.classList.contains('flex') };
console.log(JSON.stringify({ wa, em }));
""")
    assert out["wa"] == {"pdf": True, "acciones": False, "flex": True}
    assert out["em"] == {"pdf": False, "acciones": True, "flex": False}


@requiere_node
def test_abrir_destino_abre_la_ventana_de_forma_sincrona_con_el_texto_del_formulario():
    out = _node(NODE_DOM_FALSO + """
const orden = [];
const abiertas = [];
global.fetch = async () => { orden.push('fetch'); return { ok: true, status: 200, json: async () => ({ id: 1, email_body: 'editado' }) }; };
window.open = (url) => { orden.push('open'); abiertas.push(url); return {}; };
T.S.selId = 1;
T.S.detalle = { id: 1, estado: 'LISTO', canal: 'whatsapp', destino_canal: '+54 9 11 5555-1234', email_body: 'viejo' };
nodos.radarEmailBody = { ...falso('b'), value: 'texto editado', hidden: false };
T.programarEdicion('email_body', 'texto editado');
T.abrirDestino();
const sincrono = orden.slice();
setTimeout(() => {
  console.log(JSON.stringify({ sincrono, final: orden, abiertas }));
  process.exit(0);
}, 50);
""")
    assert out["sincrono"] == ["open"]
    assert out["abiertas"] == ["https://wa.me/5491155551234?text=texto%20editado"]
    assert "fetch" in out["final"]  # las ediciones pendientes se guardan después, en segundo plano


@requiere_node
def test_guardado_fallido_en_canal_sin_email_no_habla_de_email():
    out = _node(NODE_APROBAR_PRELUDE + """
(async () => {
  T.S.detalle = { ...post, canal: 'link', destino_canal: 'https://x.org/j', email_to: 'https://x.org/j' };
  T.programarEdicion('email_body', 'mensaje nuevo');
  respuestas.push('red');
  await T.marcarPostulado();  // confirmación en el botón
  await T.marcarPostulado();
  const noEmail = mensaje();
  T.S.detalle = { ...post };
  T.programarEdicion('email_subject', 'asunto');
  respuestas.push('red');
  await T.aprobar(false);
  console.log(JSON.stringify({ noEmail, email: mensaje() }));
  process.exit(0);
})();
""")
    assert "del mensaje" in out["noEmail"] and "del mail" not in out["noEmail"]  # §8: «email» → «mail»
    assert "No se pudo guardar el mensaje" in out["noEmail"] and "guardar el mail" not in out["noEmail"]
    assert "del mail" in out["email"]
    assert "No se pudo guardar el mail" in out["email"]


@requiere_node
def test_texto_vacio_avisa_de_pendientes_solo_bajo_relevantes():
    out = _node(NODE_DOM_FALSO + """
T.S.posts.set(1, { id: 1, estado: 'NUEVO', fuente: 'feed' });
T.S.posts.set(2, { id: 2, estado: 'NUEVO', fuente: 'feed' });
T.S.posts.set(3, { id: 3, estado: 'DESCARTADO', fuente: 'busqueda' });
const r = {};
T.S.filtro = 'RELEVANTES';
r.feedRel = T.textoVacio(true);
r.busqRel = T.textoVacio(false);
T.S.filtro = 'PENDIENTES';
r.feedPend = T.textoVacio(true);
T.S.posts.clear();
r.sinPosts = T.textoVacio(true);
console.log(JSON.stringify(r));
""")
    assert out["feedRel"] == "No hay ofertas en este filtro. (hay 2 sin clasificar: chip Por analizar)"
    assert out["busqRel"] == "No hay ofertas en este filtro."
    assert out["feedPend"] == "No hay ofertas en este filtro."
    assert out["sinPosts"].startswith("Todavía no hay ofertas de tu feed")


@requiere_node
def test_util_analizar_y_generar_cv_por_estado():
    out = _node("""
const a = (e) => U.accionesPara({estado: e, postulacion_id: null});
console.log(JSON.stringify(Object.fromEntries(
  ['NUEVO', 'OFERTA', 'ANALIZANDO', 'ANALIZADA', 'ERROR', 'DESCARTADO', 'GENERANDO', 'LISTO', 'ENVIADO']
    .map((e) => [e, [a(e).analizar, a(e).generarCv]]))));
""")
    # Analizar: siempre que no tenga CV (también si falló o se está analizando, para darle prioridad)
    assert out["NUEVO"] == [True, True] and out["OFERTA"] == [True, True]
    assert out["ANALIZADA"] == [True, True] and out["ERROR"] == [True, True]
    assert out["ANALIZANDO"] == [False, False]
    for e in ("GENERANDO", "LISTO", "ENVIADO"):
        assert out[e] == [False, False]


@requiere_node
def test_util_relevantes_incluye_ofertas_y_pendientes_es_lo_no_analizado():
    out = _node("""
const ps = ['NUEVO', 'OFERTA', 'ANALIZANDO', 'ANALIZADA', 'DESCARTADO'].map((e, i) => ({id: i, estado: e}));
const ids = (f) => ps.filter((p) => U.coincideFiltro(p, f)).map((p) => p.estado);
console.log(JSON.stringify({rel: ids('RELEVANTES'), pen: ids('PENDIENTES'), ana: ids('ANALIZADA')}));
""")
    assert out == {"rel": ["OFERTA", "ANALIZANDO", "ANALIZADA"], "pen": ["NUEVO", "OFERTA", "ANALIZANDO"], "ana": ["ANALIZADA"]}


def test_cards_have_analizar_and_generar_cv_buttons():
    js = RADAR_JS.read_text(encoding="utf-8")
    assert 'data-accion="${accion}"' in js
    assert "botonCard('analizar'" in js and "botonCard('generar-cv'" in js
    assert "/analizar`" in js and "/generar-cv`" in js



@requiere_node
def test_discard_asks_confirmation_on_the_same_button_and_expires():
    out = _node(NODE_APROBAR_PRELUDE + """
(async () => {
  const btn = document.getElementById('radarDescartarBtn');
  btn.innerHTML = 'Descartar';
  await T.descartar();
  const tras1 = { llamadas: llamadas.slice(), html: btn.innerHTML };
  await new Promise((r) => setTimeout(r, 3700));  // vence la confirmación
  const vencido = btn.innerHTML;
  await T.descartar();
  const tras3 = llamadas.slice();
  console.log(JSON.stringify({ tras1, vencido, tras3 }));
  process.exit(0);
})();
""")
    assert out["tras1"]["llamadas"] == [] and "Confirmar descarte" in out["tras1"]["html"]
    assert out["vencido"] == "Descartar"
    assert out["tras3"] == []  # tras vencer, el clic vuelve a pedir confirmación


def test_radar_hidden_attribute_wins_over_tailwind_display_utilities():
    # Un aviso con class="flex" y hidden se veía siempre ("Sin cuota de AGY" con cuota disponible).
    # Rediseño E5: la regla vive en radar.css y base.css la extiende a toda la página.
    css = Path(__file__).resolve().parent.parent / "static" / "css"
    assert "#vista-radar [hidden] { display: none !important; }" in (css / "radar.css").read_text(encoding="utf-8")
    assert "[hidden] { display: none !important; }" in (css / "base.css").read_text(encoding="utf-8")


@requiere_node
def test_desglose_afinidad_cuenta_por_estado_y_aprendizaje():
    out = _node("""
const T = window.Radar._test;
const reqs = [
  { nombre: 'a', estado: 'MATCH_DIRECTO' },
  { nombre: 'b', estado: 'MATCH_DIRECTO', aprendido: true },
  { nombre: 'c', estado: 'TRANSFERIBLE', aprendido: true },
  { nombre: 'd', estado: 'TRANSFERIBLE' },
  { nombre: 'e', estado: 'BRECHA_REAL' },
  null, 'suelto',
];
console.log(JSON.stringify({
  d: T.desgloseAfinidad(reqs),
  vacio: T.desgloseAfinidad([]), ausente: T.desgloseAfinidad(undefined), noLista: T.desgloseAfinidad({}),
  resVacio: T.resumenAfinidad([]), resAusente: T.resumenAfinidad(null),
}));
""")
    assert out["d"] == {"cumple": 1, "transferible": 1, "brecha": 1, "aprendido": 2, "total": 5}
    cero = {"cumple": 0, "transferible": 0, "brecha": 0, "aprendido": 0, "total": 0}
    assert out["vacio"] == out["ausente"] == out["noLista"] == cero
    assert out["resVacio"] is None and out["resAusente"] is None


@requiere_node
def test_resumen_afinidad_texto_omite_ceros_salvo_brecha_y_explica_en_el_title():
    out = _node("""
const T = window.Radar._test;
const rep = (n, extra) => Array.from({ length: n }, (_, i) => ({ nombre: 'r' + i, ...extra }));
const mixto = T.resumenAfinidad([...rep(6, { estado: 'MATCH_DIRECTO' }), ...rep(27, { estado: 'MATCH_DIRECTO', aprendido: true })]);
const uno = T.resumenAfinidad([{ estado: 'MATCH_DIRECTO' }, { estado: 'TRANSFERIBLE', aprendido: true }, { estado: 'BRECHA_REAL' }]);
const mitad = T.resumenAfinidad([...rep(2, { estado: 'MATCH_DIRECTO' }), ...rep(2, { estado: 'MATCH_DIRECTO', aprendido: true })]);
const solo = T.resumenAfinidad(rep(1, { estado: 'MATCH_DIRECTO' }));
console.log(JSON.stringify({ mixto, uno, mitad, solo }));
""")
    assert out["mixto"]["texto"] == "6 ✓ · 27 aprendidos · 0 faltan"  # §8: «brecha» → «falta»
    assert out["mixto"]["titulo"] == ("Afinidad calculada sobre 33 requisitos: 6 los cumplís por tu experiencia, "
                                      "27 porque los marcaste como aprendidos")
    assert out["mixto"]["mayormenteAprendido"] is True
    assert out["uno"]["texto"] == "1 ✓ · 1 aprendido · 1 falta"
    assert out["uno"]["titulo"].endswith(", 1 todavía sin cubrir")
    assert out["mitad"]["mayormenteAprendido"] is False  # justo la mitad no alcanza: tiene que ser más de la mitad
    assert out["solo"]["texto"] == "1 ✓ · 0 faltan"
    assert out["solo"]["titulo"].startswith("Afinidad calculada sobre 1 requisito: ")


@requiere_node
def test_card_muestra_el_desglose_y_atenua_el_porcentaje_si_es_mayormente_aprendido():
    out = _node(NODE_DOM_FALSO + """
global.document.createElement = () => ({ dataset: {}, setAttribute() {}, innerHTML: '' });
T.S.estado.umbral_match = 60;
const base = { id: 1, estado: 'ANALIZADA', autor: 'Ana', match_score: 80 };
const req = (n, extra) => Array.from({ length: n }, () => ({ estado: 'MATCH_DIRECTO', ...extra }));
const aprendidos = T.crearCard({ ...base, requisitos_estado: [...req(1, {}), ...req(3, { aprendido: true })] }).innerHTML;
const propios = T.crearCard({ ...base, requisitos_estado: req(4, {}) }).innerHTML;
const deAnalisis = T.crearCard({ ...base, requisitos_estado: req(4, {}), analisis: { requisitos: req(4, { aprendido: true }) } }).innerHTML;
const sinReqs = T.crearCard({ ...base }).innerHTML;
console.log(JSON.stringify({ aprendidos, propios, deAnalisis, sinReqs }));
""")
    assert "1 ✓ · 3 aprendidos · 0 faltan" in out["aprendidos"] and "mayormente por aprendizajes" in out["aprendidos"]
    assert "atenuado" in out["aprendidos"]  # rediseño E2: porcentaje atenuado por clase
    assert "4 ✓ · 0 faltan" in out["propios"] and "mayormente" not in out["propios"] and "atenuado" not in out["propios"]
    assert "4 aprendidos" in out["deAnalisis"]  # el análisis del detalle manda sobre el resumen de la lista
    assert "data-afinidad" not in out["sinReqs"]


NODE_ARBOL_FALSO = r"""
const nodoFalso = (tag) => ({
  tag, textContent: '', className: '', type: '', hidden: false, hijos: [], oyentes: {},
  append(...c) { this.hijos.push(...c); },
  addEventListener(ev, fn) { this.oyentes[ev] = fn; },
  remove() {}, setAttribute() {},
  set innerHTML(v) { this.textContent = String(v).replace(/<[^>]*>/g, ''); },
});
global.document.createElement = nodoFalso;
global.document.createTextNode = (t) => ({ tag: '#text', textContent: t, hijos: [] });
const textoDe = (n) => (n.textContent || '') + (n.hijos || []).map(textoDe).join('');
const botones = (n) => (n.tag === 'button' ? [n] : []).concat(...(n.hijos || []).map(botones));
const T = window.Radar._test;
"""


@requiere_node
def test_item_de_revision_muestra_puesto_y_empresa_y_abre_el_post():
    out = _node(NODE_ARBOL_FALSO + """
const panel = { hidden: false, setAttribute() {} };
const btn = { hidden: false, setAttribute() {} };
global.document.getElementById = (id) => ({ radarRevisionPanel: panel, radarRevisionBtn: btn }[id] || null);
global.fetch = async () => ({ ok: true, status: 200, json: async () => ({ id: 7, estado: 'LISTO' }) });
const fila = T.itemRevision({ id: 1, tipo: 'contradiccion', campo: 'puesto objetivo', valor_usado: 'DATA ANALYST',
  detalle: 'tiene 8 palabras', post_id: 7, postulacion_id: 3, puesto: 'Analista de datos', empresa: 'Acme',
  frase: 'Revisá el título del CV porque no cumple las reglas y no se pudo corregir.' });
const abrir = botones(fila).find((b) => b.textContent === 'Abrir');
abrir.oyentes.click();
setTimeout(() => {
  console.log(JSON.stringify({ texto: textoDe(fila), etiquetas: botones(fila).map((b) => b.textContent),
    panelOculto: panel.hidden, seleccionado: T.S.selId }));
  process.exit(0);
}, 50);
""")
    assert out["texto"].startswith("Analista de datos · Acme")
    # Una frase con qué revisar y por qué; ni el valor completo ni el detalle técnico
    assert "Revisá el título del CV porque no cumple las reglas y no se pudo corregir." in out["texto"]
    assert "DATA ANALYST" not in out["texto"] and "tiene 8 palabras" not in out["texto"]
    assert out["etiquetas"] == ["Abrir", "Visto"]
    assert out["panelOculto"] is True and out["seleccionado"] == 7


@requiere_node
def test_item_de_revision_sin_oferta_conocida_usa_el_numero_de_post():
    out = _node(NODE_ARBOL_FALSO + """
const conPost = T.itemRevision({ id: 1, tipo: 'placeholder', post_id: 12 });
const sinPost = T.itemRevision({ id: 2, tipo: 'placeholder', postulacion_id: 5 });
console.log(JSON.stringify({ post: textoDe(conPost), solo: textoDe(sinPost), abrirSinPost: botones(sinPost).map((b) => b.textContent) }));
""")
    assert out["post"].startswith("Post #12")
    assert out["solo"].startswith("Postulación #5")
    assert out["abrirSinPost"] == ["Visto"]


@requiere_node
@requiere_node
def test_franja_siempre_visible():
    out = _node(NODE_DOM_FALSO + """
const desde = new Date(2026, 10, 4, 15, 0, 0).getTime();
const cola = { generando: 13, analizando: 99, actual: { post_id: 7, puesto: 'Analista Funcional', etapa: null }, eta_s: 2400 };
const completo = T.textoCola(cola, null, desde);
const conEtapa = T.textoCola(cola, 'Redactando el mensaje', desde);
const sinEta = T.textoCola({ ...cola, eta_s: null }, null, desde);
const soloAnalisis = T.textoCola({ generando: 0, analizando: 4, actual: null, eta_s: null }, null, desde);
const vacio = T.textoCola({ generando: 0, analizando: 0, actual: null, eta_s: null }, null, desde);

T.S.estado = { ...T.S.estado, cola };
T.S.colaDesde = desde;
T.S.progreso.set(7, { percent: 40, step: 'Armando el CV', t0: 0 });
T.renderCola();
const visible = { oculta: nodos.radarCola.hidden, texto: nodos.radarCola.textContent };
T.S.estado = { ...T.S.estado, cola: { generando: 0, analizando: 0, actual: null, eta_s: null } };
T.renderCola();
const conCero = { oculta: nodos.radarCola.hidden, texto: nodos.radarCola.textContent };
console.log(JSON.stringify({ completo, conEtapa, sinEta, soloAnalisis, vacio, visible, conCero, ocultaConCero: nodos.radarCola.hidden }));
""")
    assert out["completo"] == "Generando 13 · ahora: Analista Funcional (CV) · termina ~15:40 · 99 por analizar"
    assert out["conEtapa"].startswith("Generando 13 · ahora: Analista Funcional (Redactando el mensaje) · ")
    assert out["sinEta"] == "Generando 13 · ahora: Analista Funcional (CV) · 99 por analizar"
    assert out["soloAnalisis"] == "4 por analizar"
    assert out["vacio"] == ""
    assert out["visible"] == {
        "oculta": False,
        "texto": "Generando 13 · ahora: Analista Funcional (Armando el CV) · termina ~15:40 · 99 por analizar",
    }
    assert out["ocultaConCero"] is False
    assert out["conCero"]["texto"] == "Nada en curso · Ver procesos"  # §8


@requiere_node
def test_texto_cola_sin_actividad_ficticia():
    out = _node(NODE_DOM_FALSO + """
const desde = new Date(2026, 10, 4, 15, 0, 0).getTime();
const colaEnEspera = { generando: 3, analizando: 0, actual: null, eta_s: null };
const colaEnEsperaConAnalisis = { generando: 3, analizando: 5, actual: null, eta_s: null };
const colaEnCurso = { generando: 3, analizando: 0, actual: { post_id: 1, puesto: 'Analista' }, eta_s: 300 };

const txtEspera = T.textoCola(colaEnEspera, null, desde);
const txtEsperaAnalisis = T.textoCola(colaEnEsperaConAnalisis, null, desde);
const txtCurso = T.textoCola(colaEnCurso, null, desde);

console.log(JSON.stringify({ txtEspera, txtEsperaAnalisis, txtCurso }));
""")
    assert out["txtEspera"] == "Generando 3 en cola"
    assert out["txtEsperaAnalisis"] == "Generando 3 en cola · 5 por analizar"
    assert "ahora:" not in out["txtEspera"]
    assert "termina ~" not in out["txtEspera"]
    assert "Generando 3 · ahora: Analista (CV)" in out["txtCurso"]
    assert "termina ~" in out["txtCurso"]


@requiere_node
def test_franja_de_cola_se_refresca_cuando_un_post_cambia_de_estado():
    out = _node(NODE_DOM_FALSO + """
const llamadas = [];
global.fetch = async (url, opts) => {
  llamadas.push(((opts && opts.method) || 'GET') + ' ' + url);
  return { ok: true, status: 200, json: async () => ({ cola: { generando: 0, analizando: 0, actual: null, eta_s: null } }) };
};
(async () => {
  T.S.posts.set(5, { id: 5, estado: 'GENERANDO', autor: 'A' });
  window.Radar.onPost({ id: 5, estado: 'GENERANDO', autor: 'A' });   // mismo estado: no pide nada
  window.Radar.onPost({ id: 5, estado: 'LISTO', autor: 'A' });
  window.Radar.onPost({ id: 5, estado: 'LISTO', autor: 'A' });
  await new Promise((r) => setTimeout(r, 700));
  console.log(JSON.stringify({ estados: llamadas.filter((l) => l.endsWith('/api/radar/status')).length }));
})();
""")
    assert out["estados"] == 1


def test_franja_y_panel_procesos_en_el_markup(soup):
    franja = soup.find(id="radarCola")
    assert franja is not None and franja.name == "button"
    assert franja.get("aria-controls") == "radarProcesosPanel"
    assert not franja.has_attr("hidden")

    panel = soup.find(id="radarProcesosPanel")
    assert panel is not None
    assert panel.find_parent(id="vista-radar") is not None
    assert panel.has_attr("hidden")
    assert panel.get("role") == "dialog"
    assert panel.get("aria-modal") == "true"
    assert panel.get("aria-labelledby") == "radarProcesosTitulo"


@requiere_node
def test_linea_de_pasos():
    out = _node(NODE_DOM_FALSO + """
const prog = {
  paso: 'redactar',
  etiqueta: 'Redactando el CV',
  fin: null,
  pasos: [
    { paso: 'preparar', etiqueta: 'Preparando la postulación', inicio: '2026-11-04T10:00:00', fin: '2026-11-04T10:00:05', duracion_s: 5.0 },
    { paso: 'redactar', etiqueta: 'Redactando el CV', inicio: '2026-11-04T10:00:05', fin: null, duracion_s: null }
  ]
};
const linea = T.lineaPasos(prog);
console.log(JSON.stringify(linea));
""")
    assert len(out) >= 2
    assert out[0] == {"paso": "preparar", "etiqueta": "Preparando la postulación", "estado": "hecho", "duracion_s": 5.0}
    assert out[1] == {"paso": "redactar", "etiqueta": "Redactando el CV", "estado": "actual", "duracion_s": None}
    # Los pasos posteriores a redactar en PASOS_CV son auditar, completar, mensaje, completar_mensaje
    pendientes = out[2:]
    assert [p["paso"] for p in pendientes] == ["auditar", "completar", "mensaje", "completar_mensaje"]
    assert all(p["estado"] == "pendiente" for p in pendientes)
    # Analizar fue salteado: no debe figurar como pendiente
    assert not any(p["paso"] == "analizar" for p in out)


@requiere_node
def test_pasos_pendientes_e_historial_sin_jerga():
    out = _node(NODE_DOM_FALSO + """
const pendiente = T.lineaPasos({ paso: 'redactar', pasos: [] }).find(p => p.paso === 'completar');
const historial = T.lineaPasos({ fin: '2026-11-05', pasos: [{ paso: 'completar', fin: '2026-11-05' }] });
console.log(JSON.stringify([pendiente.etiqueta, historial[0].etiqueta]));
""")
    assert out == ["Completando puesto, CV, presentación y canales"] * 2


@requiere_node
def test_texto_topes_y_texto_cuota_proceso():
    out = _node(NODE_DOM_FALSO + """
const topes = { leidos_busquedas: 120, leidos_feed: 80, tope_leidos: 600, enviados: 3, tope_envios: 40, postulados: 1, ofertas: 14 };
const sinReglas = T.textoTopes({ leidos_busquedas: 2, leidos_feed: 1, tope_leidos: null, enviados: 0, tope_envios: null });
const txtTopes = T.textoTopes(topes);
const conCuota = T.textoCuotaProceso({ esperando_cuota: true, cuota_renueva: '10:15' });
const conCuotaIso = T.textoCuotaProceso({ esperando_cuota: true, cuota_renueva: '2026-11-04T10:15:00' });
const sinCuota = T.textoCuotaProceso({ esperando_cuota: false });
const sinProgreso = T.textoCuotaProceso(null);
console.log(JSON.stringify({ txtTopes, sinReglas, conCuota, conCuotaIso, sinCuota, sinProgreso }));
""")
    # Espacio duro dentro de cada dato (no se corta entre etiqueta y número)
    assert out["txtTopes"] == "Feed 80 · Búsquedas 120 · Enviados 3/40 · Postulados hoy 1 · Ofertas 14".replace(
        " · ", "|").replace(" ", " ").replace("|", " · ")
    # Sin reglas del perfil no hay tope que mostrar: «—», nunca un número inventado
    assert out["sinReglas"].startswith("Feed 1 · Búsquedas 2 · Enviados 0/—")
    assert out["conCuota"] == "Esperando cuota de AGY · se renueva a las 10:15"
    assert "Esperando cuota de AGY · se renueva a las " in out["conCuotaIso"]
    assert out["sinCuota"] == ""
    assert out["sinProgreso"] == ""


@requiere_node
def test_fila_proceso_no_interpreta_html():
    out = _node(NODE_DOM_FALSO + """
const item = {
  post_id: 1,
  puesto: '<img onerror=x>',
  empresa: '<b>Acme</b>',
  estado: 'GENERANDO',
  fuente: 'feed',
  tipo: 'generacion',
  posicion: 1,
  progreso: null,
  resultado: null,
  duracion_s: null,
  terminado_at: null
};
const nodo = T.filaProceso(item);
const imgs = nodo.querySelectorAll('img');
console.log(JSON.stringify({
  tieneImg: imgs.length > 0,
  texto: nodo.textContent || ''
}));
""")
    assert out["tieneImg"] is False
    assert "<img onerror=x>" in out["texto"]


@requiere_node
def test_fila_proceso_muestra_estado_y_distingue_analizada_de_error():
    out = _node(NODE_DOM_FALSO + """
const itemA = { post_id: 1, puesto: 'Dev', empresa: 'Acme', estado: 'ANALIZADA', tipo: 'generacion', progreso: null };
const itemE = { post_id: 2, puesto: 'Dev', empresa: 'Acme', estado: 'ERROR', tipo: 'generacion', progreso: null };
const itemG1 = { post_id: 3, puesto: 'Dev', empresa: 'Acme', estado: 'GENERANDO', tipo: 'generacion', progreso: null };
const itemG2 = { post_id: 4, puesto: 'Dev', empresa: 'Acme', estado: 'GENERANDO', tipo: 'generacion', progreso: { etiqueta: 'Redactando CV', pct: 40 } };
const itemL = { post_id: 5, puesto: 'Dev', empresa: 'Acme', estado: 'LISTO', tipo: 'generacion', progreso: null };

const nA = T.filaProceso(itemA);
const nE = T.filaProceso(itemE);
const nG1 = T.filaProceso(itemG1);
const nG2 = T.filaProceso(itemG2);
const nL = T.filaProceso(itemL);

const todasClases = (nodo) => {
  let c = [nodo.className || ''];
  for (const hijo of (nodo.children || [])) c = c.concat(todasClases(hijo));
  return c;
};

console.log(JSON.stringify({
  textoA: nA.textContent || '',
  textoE: nE.textContent || '',
  textoG1: nG1.textContent || '',
  textoG2: nG2.textContent || '',
  textoL: nL.textContent || '',
  clasesA: todasClases(nA).join(' '),
  clasesE: todasClases(nE).join(' ')
}));
""")
    assert "Analizada" in out["textoA"]
    assert "Error" in out["textoE"]
    assert out["textoA"] != out["textoE"]
    assert "chip-ok" in out["clasesA"]  # rediseño E2: tono por chip
    assert "chip-danger" in out["clasesE"]
    assert "En cola" in out["textoG1"]
    assert "Generando" in out["textoG2"]
    assert "Lista para enviar" in out["textoL"]


@requiere_node
def test_fila_proceso_sin_empresa_no_muestra_feed_como_empresa():
    out = _node(NODE_DOM_FALSO + """
const itemSinEmpresa = { post_id: 20, puesto: 'Tech Lead', empresa: '', fuente: 'feed', estado: 'ANALIZADA', tipo: 'generacion' };
const itemConEmpresa = { post_id: 21, puesto: 'Frontend Dev', empresa: 'Globant', fuente: 'feed', estado: 'ANALIZADA', tipo: 'generacion' };

const nSin = T.filaProceso(itemSinEmpresa);
const nCon = T.filaProceso(itemConEmpresa);

console.log(JSON.stringify({
  textoSin: nSin.textContent || '',
  textoCon: nCon.textContent || ''
}));
""")
    assert "Tech Lead" in out["textoSin"]
    assert "Globant" in out["textoCon"]
    assert "Mi feed" not in out["textoSin"]
    assert "Feed general" not in out["textoSin"]


@requiere_node
def test_sembrar_progreso_desde_el_servidor():
    out = _node(NODE_DOM_FALSO + """
const post = { id: 7, estado: 'GENERANDO', autor: 'Alguien', match_score: 80 };
T.S.posts.set(7, post);
window.Radar.onStatus({
  cola: {
    actual: {
      post_id: 7,
      progreso: {
        etiqueta: 'Redactando el CV',
        pct: 15,
        inicio: '2026-11-04T10:00:00'
      }
    }
  }
});
const html = T.htmlProgreso(post);
console.log(JSON.stringify({ html }));
""")
    assert "Redactando el CV" in out["html"]
    assert "En cola" not in out["html"]


# ---------------------------------------------------------------------------
# Tarea 6: tarjetas con el puesto arriba, estados en castellano y vínculo entre vistas
# ---------------------------------------------------------------------------


def _node_app(funciones, body: str):
    """Corre funciones de nivel superior de static/app.js (extraídas por nombre) sin cargar todo el archivo."""
    js = APP_JS.read_text(encoding="utf-8")
    trozos = []
    for nombre in funciones:
        m = re.search(rf"^function {nombre}\(.*?^\}}\n", js, re.S | re.M)
        assert m, f"app.js no define function {nombre}()"
        trozos.append(m.group(0))
    return _run_node("global.window = global;\n" + "\n".join(trozos) + "\n" + body)


@requiere_node
def test_card_titulo_es_el_puesto_y_segunda_linea_empresa_y_autor():
    out = _node(NODE_DOM_FALSO + r"""
global.document.createElement = () => ({ dataset: {}, setAttribute() {}, innerHTML: '' });
const base = { id: 1, estado: 'ANALIZADA', autor: 'Ana Pérez', match_score: 80 };
const html = (p) => T.crearCard({ ...base, ...p }).innerHTML;
const titulo = (h) => (/oferta-titulo titulo-linea">([^<]*)</.exec(h) || [])[1];  // rediseño E2
const linea2 = (h) => (/oferta-sub">([^<]*)</.exec(h) || [])[1];
const par = (h) => [titulo(h), linea2(h)];
const xss = html({ puesto: '<script>alert(1)</script>', empresa: 'A&B' });
console.log(JSON.stringify({
  conPuesto: par(html({ puesto: 'Backend Dev', empresa: 'Acme' })),
  deTriage: par(html({ triage: { puesto: 'Data Eng' }, empresa: 'Beta' })),
  deAnalisis: par(html({ triage: { puesto: 'Viejo' }, analisis: { puesto: 'Nuevo' }, empresa: 'Beta' })),
  sinPuesto: par(html({ extracto: 'Buscamos gente' })),
  sinPuestoConEmpresa: par(html({ empresa: 'Acme' })),
  xssCrudo: xss.includes('<script>'),
}));
""")
    assert out["conPuesto"] == ["Backend Dev", "Acme · Ana Pérez"]
    assert out["deTriage"] == ["Data Eng", "Beta · Ana Pérez"]
    assert out["deAnalisis"] == ["Nuevo", "Beta · Ana Pérez"]
    # sin puesto: el autor queda arriba, como antes
    assert out["sinPuesto"] == ["Ana Pérez", "Buscamos gente"]
    assert out["sinPuestoConEmpresa"] == ["Ana Pérez", "Acme"]
    assert out["xssCrudo"] is False


@requiere_node
def test_card_sin_puesto_ni_autor_no_rompe():
    out = _node(NODE_DOM_FALSO + """
global.document.createElement = () => ({ dataset: {}, setAttribute() {}, innerHTML: '' });
const h = T.crearCard({ id: 2, estado: 'NUEVO' }).innerHTML;
console.log(JSON.stringify({ desconocido: h.includes('Autor desconocido') }));
""")
    assert out["desconocido"] is True


@requiere_node
def test_texto_estado_cv_en_castellano_sin_cambiar_los_valores():
    out = _node_app(["textoEstadoCv"], """
console.log(JSON.stringify(['READY', 'PROCESSING', 'ERROR', 'PENDING', undefined, '', 'RARO'].map(textoEstadoCv)));
""")
    assert out == ["CV listo", "Generando", "Error", "Pendiente", "Pendiente", "Pendiente", "RARO"]


@requiere_node
def test_es_de_radar_por_origen_o_referencia():
    out = _node_app(["esDeRadar"], """
console.log(JSON.stringify([
  esDeRadar({ origen: 'linkedin' }), esDeRadar({ origen: 'manual', radar_post_ref: 'urn:li:x' }),
  esDeRadar({ origen: 'manual', radar_post_ref: '' }), esDeRadar({}), esDeRadar(null),
]));
""")
    assert out == [True, True, False, False, False]


@requiere_node
def test_abrir_post_de_postulacion_selecciona_el_post_por_su_postulacion_id():
    out = _node(NODE_DOM_FALSO + """
global.fetch = async () => ({ ok: false, status: 500, json: async () => ({ detail: 'x' }) });
global.innerWidth = 1400;
T.S.iniciado = true;
T.S.posts.set(4, { id: 4, estado: 'LISTO', postulacion_id: 70 });
T.S.posts.set(5, { id: 5, estado: 'LISTO', postulacion_id: 77 });
(async () => {
  const ok = await window.Radar.abrirPostDePostulacion(77);
  const selAntes = T.S.selId;
  const noHay = await window.Radar.abrirPostDePostulacion(999);
  const sinId = await window.Radar.abrirPostDePostulacion(null);
  const porId = await window.Radar.abrirPost(4);
  console.log(JSON.stringify({ ok, selAntes, noHay, sinId, porId, selFinal: T.S.selId }));
})();
""")
    assert out == {"ok": True, "selAntes": 5, "noHay": False, "sinId": False, "porId": True, "selFinal": 4}


@requiere_node
def test_detalle_del_radar_ofrece_ver_postulacion_solo_si_hay_postulacion():
    out = _node(NODE_DOM_FALSO + """
const cont = global.document.getElementById('radarDetallePost');
T.S.detalle = { id: 1, estado: 'LISTO', autor: 'Ana', postulacion_id: 42 };
T.renderDetalle();
const con = cont.innerHTML;
T.S.detalle = { id: 2, estado: 'ANALIZADA', autor: 'Ana' };
T.renderDetalle();
const sin = cont.innerHTML;
console.log(JSON.stringify({ con: /data-ver-postulacion="42"/.test(con) && con.includes('Ver postulación'), sin: sin.includes('data-ver-postulacion') }));
""")
    assert out == {"con": True, "sin": False}


def test_postulaciones_tiene_boton_ver_en_radar_y_usa_el_texto_en_castellano():
    js = APP_JS.read_text(encoding="utf-8")
    html = Path("static/index.html").read_text(encoding="utf-8")
    assert 'id="btnVerEnRadar"' in html
    assert "textoEstadoCv(" in js and "esDeRadar(" in js
    assert "abrirPostDePostulacion" in js
    # los valores de datos y los filtros siguen usando los códigos internos
    assert "o.status === 'READY' && !!o.cv_markdown" in js


# --- «Aprobar todos»: ofertas omitidas por duplicado con acciones en el mismo mensaje ---

NODE_OMITIDOS = r"""
const textoDe = (n) => (n.textContent || '') + ' ' + (n.children || []).map(textoDe).join(' ');
const todos = (n, acc = []) => { acc.push(n); (n.children || []).forEach((h) => todos(h, acc)); return acc; };
const botones = (n) => todos(n).filter((x) => x.handlers && x.handlers.click && x.type === 'button');
const filasDup = () => elementos.radarMensajeTexto.children.filter((c) => botones(c).length);
const botonDe = (fila, texto) => botones(fila).find((b) => b.textContent === texto);
const plano = (s) => s.replace(/\s+/g, ' ').trim();
const timeouts = [];
const setTimeoutReal = global.setTimeout;
global.setTimeout = (fn, ms) => { timeouts.push(ms); return setTimeoutReal(fn, ms); };
global.document.createElement = () => {
  const n = falso();
  Object.defineProperty(n, 'innerHTML', { set() { throw new Error('innerHTML prohibido'); }, get() { return ''; } });
  return n;
};
const dupEnviado = { id: 5, motivo: 'duplicado: ya se envió o está por enviarse a info@empresa-ficticia.test', tipo: 'duplicado',
  email: 'info@empresa-ficticia.test', previo: { post_id: 90, puesto: 'Contador', empresa: 'Empresa Ejemplo', sent_at: '2026-11-02 10:00:00', estado: 'ENVIADO' } };
const dupCola = { id: 6, motivo: 'duplicado: ya se envió o está por enviarse a rrhh@x.test', tipo: 'duplicado',
  email: 'rrhh@x.test', previo: { post_id: 91, puesto: 'Analista', empresa: 'X', sent_at: null, estado: 'LISTO' } };
function prepararOmitidos(omitidos) {
  [5, 6].forEach((id) => T.S.posts.set(id, { ...post, id, updated_at: 'u' + id,
    triage: { puesto: 'Oferta ' + id, empresa: 'Emp ' + id, alcance: 'ARGENTINA' } }));
  respuestas.push({ status: 200, body: { encolados: [], omitidos } });
  respuestas.push({ status: 200, body: [] });   // cargarPosts
  respuestas.push({ status: 200, body: post }); // cargarDetalle
}
"""


@requiere_node
def test_approve_all_duplicado_se_muestra_como_fila_con_botones_sin_innerhtml():
    out = _node(NODE_APROBAR_PRELUDE + NODE_FORMULARIO + NODE_OMITIDOS + """
(async () => {
  prepararOmitidos([dupEnviado, dupCola, { id: 7, motivo: 'cambió desde que lo revisaste: abrilo y aprobalo de nuevo' }]);
  await aprobarTodosOk();
  const filas = filasDup();
  console.log(JSON.stringify({
    principal: elementos.radarMensajeTexto.children[0].textContent, n: filas.length,
    textos: filas.map((f) => plano(textoDe(f))),
    botones: filas.map((f) => botones(f).map((b) => b.textContent)),
    sueltas: elementos.radarMensajeTexto.children.map((c) => c.textContent).filter((t) => /^Omitido /.test(t)),
  }));
  process.exit(0);
})();
""")
    assert out["principal"] == "Encolados: 0 · Omitidos: 3"
    assert out["n"] == 2
    assert out["textos"][0].startswith("Oferta 5 · Emp 5: ya le escribiste a info@empresa-ficticia.test el 02/11 por «Contador»")
    assert out["textos"][1].startswith("Oferta 6 · Emp 6: ya está en cola para rrhh@x.test por «Analista»")
    assert out["botones"] == [["Enviar igual", "Descartar"]] * 2
    # El omitido que no es duplicado sigue como línea de texto
    assert len(out["sueltas"]) == 1 and "#7" in out["sueltas"][0]


@requiere_node
def test_enviar_igual_manda_confirm_duplicate_una_sola_vez_aunque_se_haga_doble_clic():
    out = _node(NODE_APROBAR_PRELUDE + NODE_FORMULARIO + NODE_OMITIDOS + """
(async () => {
  prepararOmitidos([dupEnviado]);
  await aprobarTodosOk();
  const fila = filasDup()[0];
  const btn = botonDe(fila, 'Enviar igual');
  const antes = cuerpos.length;
  respuestas.push({ status: 200, body: { ...post, id: 5, aprobado_at: '2026-11-04 10:00:00', updated_at: 'u5' } });
  const a = btn.handlers.click();
  const b = btn.handlers.click();
  await Promise.all([a, b]);
  console.log(JSON.stringify({
    nuevas: cuerpos.slice(antes),
    deshabilitados: botones(fila).map((x) => x.disabled),
    texto: plano(textoDe(fila)),
  }));
  process.exit(0);
})();
""")
    assert len(out["nuevas"]) == 1
    nueva = out["nuevas"][0]
    assert nueva["m"] == "POST" and nueva["url"] == "/api/radar/posts/5/approve"
    assert nueva["body"] == {"confirm_duplicate": True, "updated_at": "u5"}  # lo que se revisó
    assert out["deshabilitados"] == [True, True]
    assert "Encolado" in out["texto"]


@requiere_node
def test_enviar_igual_no_pide_otra_confirmacion_y_muestra_el_error_del_servidor():
    out = _node(NODE_APROBAR_PRELUDE + NODE_FORMULARIO + NODE_OMITIDOS + """
(async () => {
  let preguntas = 0;
  window.confirm = () => { preguntas++; return false; };
  prepararOmitidos([dupEnviado]);
  await aprobarTodosOk();
  const fila = filasDup()[0];
  respuestas.push({ status: 409, body: { detail: 'No se puede aprobar: falta el cuerpo' } });
  await botonDe(fila, 'Enviar igual').handlers.click();
  console.log(JSON.stringify({ preguntas, texto: plano(textoDe(fila)), deshabilitados: botones(fila).map((x) => x.disabled) }));
  process.exit(0);
})();
""")
    assert out["preguntas"] == 0
    assert "No se puede aprobar: falta el cuerpo" in out["texto"]
    assert out["deshabilitados"] == [False, False]  # tras un error se puede reintentar


@requiere_node
def test_enviar_igual_no_aprueba_si_la_oferta_cambio_desde_que_se_reviso():
    out = _node(NODE_APROBAR_PRELUDE + NODE_FORMULARIO + NODE_OMITIDOS + """
(async () => {
  prepararOmitidos([dupEnviado]);
  await aprobarTodosOk();
  const fila = filasDup()[0];
  T.S.posts.set(5, { ...T.S.posts.get(5), updated_at: 'otro' });  // la editaron mientras tanto
  const antes = cuerpos.length;
  await botonDe(fila, 'Enviar igual').handlers.click();
  console.log(JSON.stringify({ llamadas: cuerpos.length - antes, texto: plano(textoDe(fila)) }));
  process.exit(0);
})();
""")
    assert out["llamadas"] == 0
    assert "cambió" in out["texto"].lower()


@requiere_node
def test_descartar_un_duplicado_llama_al_descarte_sin_deshacer():
    out = _node(NODE_APROBAR_PRELUDE + NODE_FORMULARIO + NODE_OMITIDOS + """
(async () => {
  prepararOmitidos([dupCola]);
  await aprobarTodosOk();
  const fila = filasDup()[0];
  const antes = cuerpos.length;
  respuestas.push({ status: 200, body: { ...post, id: 6, estado: 'DESCARTADO' } });
  const btn = botonDe(fila, 'Descartar');
  await Promise.all([btn.handlers.click(), btn.handlers.click()]);
  console.log(JSON.stringify({ nuevas: cuerpos.slice(antes), texto: plano(textoDe(fila)), mensaje: plano(textoDe(elementos.radarMensajeTexto)) }));
  process.exit(0);
})();
""")
    assert [(c["m"], c["url"]) for c in out["nuevas"]] == [("POST", "/api/radar/posts/6/discard")]
    assert "Descartado" in out["texto"]
    assert "Deshacer" not in out["mensaje"]


@requiere_node
def test_descartar_un_duplicado_pide_confirmacion_con_el_primer_clic():
    # 107 ofertas aparecieron descartadas sin que el usuario lo pidiera; un clic suelto no descarta
    out = _node(NODE_APROBAR_PRELUDE + NODE_FORMULARIO + NODE_OMITIDOS + """
(async () => {
  prepararOmitidos([dupCola]);
  await aprobarTodosOk();
  const fila = filasDup()[0];
  const antes = cuerpos.length;
  await botonDe(fila, 'Descartar').handlers.click();
  console.log(JSON.stringify({ nuevas: cuerpos.slice(antes), texto: plano(textoDe(fila)) }));
  process.exit(0);
})();
""")
    assert out["nuevas"] == []
    assert "¿Confirmar descarte?" in out["texto"]


@requiere_node
def test_el_mensaje_no_se_oculta_solo_con_acciones_pendientes():
    out = _node(NODE_APROBAR_PRELUDE + NODE_FORMULARIO + NODE_OMITIDOS + """
(async () => {
  prepararOmitidos([dupEnviado, dupCola]);
  respuestas[0].body.encolados = [1];  // hubo envíos: sin duplicados pendientes el mensaje (verde) se iría solo
  T.S.posts.set(1, { ...post, updated_at: 'u1' });
  await aprobarTodosOk();
  console.log(JSON.stringify({ timeouts, oculto: elementos.radarMensaje.hidden }));
  process.exit(0);
})();
""")
    # Ningún temporizador de autocierre (6 s / 10 s) mientras queden acciones por hacer
    assert 6000 not in out["timeouts"] and 10000 not in out["timeouts"]
    assert out["oculto"] is False


@requiere_node
def test_el_mensaje_sin_duplicados_sigue_ocultandose_solo():
    out = _node(NODE_APROBAR_PRELUDE + NODE_FORMULARIO + NODE_OMITIDOS + """
(async () => {
  respuestas.push({ status: 200, body: { encolados: [1], omitidos: [{ id: 7, motivo: 'ya no existe' }] } });
  respuestas.push({ status: 200, body: [] });
  respuestas.push({ status: 200, body: post });
  T.S.posts.set(1, { ...post, updated_at: 'u1' });
  await aprobarTodosOk();
  console.log(JSON.stringify({ timeouts }));
  process.exit(0);
})();
""")
    assert 6000 in out["timeouts"]


# --- Task 2 (motor único): estados del modelo del usuario, canal y «Ya me postulé» ---

@requiere_node
def test_etiqueta_estado_modelo_del_usuario():
    out = _node("""
const e = (p) => U.etiquetaEstado(p);
const estados = ['NUEVO', 'OFERTA', 'ANALIZANDO', 'ANALIZADA', 'GENERANDO', 'LISTO', 'ENVIANDO', 'ENVIADO', 'DESCARTADO', 'ERROR', 'REVISAR'];
console.log(JSON.stringify({
  todos: Object.fromEntries(estados.map((s) => [s, e({estado: s})])),
  manual: e({estado: 'ENVIADO', message_id: 'manual:link'}),
  gmail: e({estado: 'ENVIADO', message_id: '<x@correo-ficticio.test>'}),
  raro: e({estado: 'constructor'}),
}));
""")
    t = out["todos"]
    assert t["NUEVO"] == {"texto": "Pendiente", "sub": "sin clasificar"}
    assert t["OFERTA"] == {"texto": "Pendiente", "sub": "por analizar"}
    assert t["ANALIZANDO"] == {"texto": "Pendiente", "sub": "analizando"}
    assert {k: t[k]["texto"] for k in ("ANALIZADA", "GENERANDO", "LISTO", "ENVIANDO", "ENVIADO", "DESCARTADO", "ERROR", "REVISAR")} == {
        "ANALIZADA": "Analizada", "GENERANDO": "En cola", "LISTO": "Lista para enviar", "ENVIANDO": "Enviando",
        "ENVIADO": "Enviada", "DESCARTADO": "Descartada", "ERROR": "Error", "REVISAR": "A revisar",
    }
    assert out["manual"]["texto"] == "Postulada"
    assert out["gmail"]["texto"] == "Enviada"
    assert out["raro"]["texto"] == "constructor"


@requiere_node
def test_chip_pendientes_cubre_los_tres_estados():
    out = _node("""
const ok = (s) => U.coincideFiltro({estado: s}, 'PENDIENTES');
console.log(JSON.stringify(['NUEVO', 'OFERTA', 'ANALIZANDO', 'ANALIZADA'].map(ok)));
""")
    assert out == [True, True, True, False]


@requiere_node
def test_filtro_de_canal():
    out = _node("""
const posts = [
  {id: 1, canal: 'email', email_to: 'a@b.test'},
  {id: 2, canal: 'email', email_to: '  ', emails: ['x@y.test']},
  {id: 3, canal: 'email', email_to: '', emails: []},
  {id: 4, canal: 'link'},
  {id: 5, canal: 'whatsapp'},
];
const ids = (c) => posts.filter((p) => U.coincideCanal(p, c)).map((p) => p.id);
console.log(JSON.stringify({ e: ids('EMAIL'), l: ids('ENLACE'), t: ids('TODOS') }));
""")
    assert out == {"e": [1, 2], "l": [3, 4, 5], "t": [1, 2, 3, 4, 5]}


def test_index_filtro_de_canal_y_sin_contadores(soup):
    vista = soup.find(id="vista-radar")
    cont = vista.find(id="radarFiltroCanal")
    assert cont is not None
    assert [b.get("data-canal") for b in cont.find_all("button")] == ["TODOS", "EMAIL", "ENLACE"]
    assert [b.get_text(strip=True) for b in cont.find_all("button")] == ["Todos", "Con mail", "Solo enlace"]  # §8
    assert soup.find(id="radarContadores") is None
    assert "renderContadores" not in RADAR_JS.read_text(encoding="utf-8")


def test_index_chips_de_estado_con_etiquetas_nuevas(soup):
    chips = soup.find(id="radarFiltros").find_all("button")
    assert [b.get("data-estado") for b in chips] == [
        "RELEVANTES", "PENDIENTES", "ANALIZADA", "LISTO", "GENERANDO", "ENVIADO", "DESCARTADO", "ERROR", "TODOS",
        "IGNORADA",  # temporal
    ]
    textos = [b.get_text(" ", strip=True).rsplit(" ", 1)[0] for b in chips]
    assert textos == ["Relevantes", "Por analizar", "Analizadas", "Listas", "Generando", "Enviadas", "Descartadas", "Con error", "Todas", "Ignoradas"]


def test_boton_ya_me_postule(soup):
    btn = soup.find(id="radarMarcarPostuladoBtn")
    assert btn.get_text(strip=True) == "Ya me postulé"
    assert btn.get("title") == "Marcá que ya te postulaste por tu cuenta (te pide confirmar)"
    for ruta in ("static/index.html", "static/radar.js", "core/radar_api.py", "core/radar.py"):
        assert "Marcar como postulado" not in Path(ruta).read_text(encoding="utf-8"), ruta


@requiere_node
def test_ya_me_postule_pide_confirmar_y_dice_listo():
    out = _node(NODE_DOM_FALSO + """
const btn = document.getElementById('radarMarcarPostuladoBtn');
T.S.selId = 5;
T.S.detalle = { id: 5, estado: 'ANALIZADA', canal: 'link' };
(async () => {
  await T.marcarPostulado();
  console.log(JSON.stringify({ primero: btn.textContent }));
  process.exit(0);
})();
""")
    assert out["primero"] == "¿Confirmás? Tocá de nuevo"


@requiere_node
def test_texto_estado_email_enviada_vs_postulada_y_sin_listo():
    out = _node("""
const t = (p) => U.textoEstadoEmail(p);
console.log(JSON.stringify({
  manualEmail: t({estado: 'ENVIADO', canal: 'email', email_to: 'rrhh@example.com', message_id: 'manual:x'}),
  gmail: t({estado: 'ENVIADO', canal: 'email', email_to: 'rrhh@example.com', message_id: '<a@b>'}),
  manualLink: t({estado: 'ENVIADO', canal: 'link', message_id: 'manual:y'}),
  listoLink: t({estado: 'LISTO', canal: 'link'}),
  sinDest: t({estado: 'LISTO', canal: 'email', email_to: ''}),
}));
""")
    assert out["manualEmail"] == "Postulada"
    assert out["gmail"] == "Enviada"
    assert out["manualLink"] == "Postulada"
    assert "Ya me postulé" in out["listoLink"] and "Ya me postulé" in out["sinDest"]


def test_radar_no_indica_el_nombre_viejo_del_boton_de_postulacion():
    js = RADAR_JS.read_text(encoding="utf-8")
    for viejo in ("tocá Listo", "marcalo como postulado"):
        assert viejo not in js


@requiere_node
@pytest.mark.parametrize("canal", ["email", "link"])
@pytest.mark.parametrize("message_id,etiqueta", [("manual:x", "Postulada"), ("<a@b>", "Enviada")])
def test_texto_estado_email_conserva_fecha_y_distingue_postulacion_manual(canal, message_id, etiqueta):
    post = {"estado": "ENVIADO", "canal": canal, "email_to": "rrhh@example.com",
            "message_id": message_id, "sent_at": "2026-11-05 14:30:00"}
    out = _node("console.log(JSON.stringify(U.textoEstadoEmail(" + json.dumps(post) + "))); ")
    assert out == etiqueta + " · 05/11 14:30"


@requiere_node
@pytest.mark.parametrize("canal", ["email", "link", "whatsapp", "dm", "otro"])
@pytest.mark.parametrize("sent_at", [None, "2026-11-05T15:30:00Z"])
def test_texto_estado_enviado_conserva_etiqueta_y_fecha(canal, sent_at):
    out = _node("""
const base = %s;
const t = (message_id) => U.textoEstadoEmail({...base, message_id});
console.log(JSON.stringify({manual: t('manual:x'), enviada: t('<a@b>')}));
""" % json.dumps({"estado": "ENVIADO", "canal": canal, "sent_at": sent_at}))
    assert out["manual"].startswith("Postulada")
    assert out["enviada"].startswith("Enviada")
    if sent_at:
        fecha = out["manual"].removeprefix("Postulada · ")
        assert fecha and fecha != out["manual"]
        assert out["enviada"] == "Enviada · " + fecha
    else:
        assert out == {"manual": "Postulada", "enviada": "Enviada"}


@requiere_node
def test_sse_radar_progreso_actualiza_panel_completo_sin_post_en_lista():
    out = _node(NODE_DOM_FALSO + """
// 1. Post 50 NO está en S.posts (p. ej. otra página o filtro)
T.S.posts.delete(50);

// Crear la fila en el panel de procesos
const lista = document.getElementById('radarProcesosLista');
const fila = T.filaProceso({
  post_id: 50,
  puesto: 'Dev Python',
  empresa: 'Globant',
  estado: 'GENERANDO',
  fuente: 'feed',
  tipo: 'generacion',
  progreso: null
});
lista.appendChild(fila);

const textoInicial = fila.textContent || '';

// 2. Llega evento radar_progreso normal
window.Radar.onProgreso({
  id: 50,
  percent: 25,
  step: 'Redactando el CV',
  progreso: {
    inicio: '2026-11-05T12:00:00Z',
    etiqueta: 'Redactando el CV',
    pct: 25,
    esperando_cuota: false,
    paso: 'redactar',
    pasos: [
      { paso: 'preparar', etiqueta: 'Preparando la base', duracion_s: 2.5, estado: 'hecho' }
    ]
  }
});
const textoNormal = fila.textContent || '';

// 3. Llega espera de cuota (aparece)
window.Radar.onProgreso({
  id: 50,
  progreso: {
    inicio: '2026-11-05T12:00:00Z',
    esperando_cuota: true,
    renueva_a: '14:30',
    etiqueta: 'Esperando cuota',
    pct: 25,
    paso: 'redactar',
    pasos: [
      { paso: 'preparar', etiqueta: 'Preparando la base', duracion_s: 2.5, estado: 'hecho' }
    ]
  }
});
const textoCuota = fila.textContent || '';

// 4. Se renueva la cuota (desaparece espera de cuota)
window.Radar.onProgreso({
  id: 50,
  percent: 60,
  step: 'Revisando y corrigiendo el CV',
  progreso: {
    inicio: '2026-11-05T12:00:00Z',
    etiqueta: 'Revisando y corrigiendo el CV',
    pct: 60,
    esperando_cuota: false,
    paso: 'auditar',
    pasos: [
      { paso: 'preparar', etiqueta: 'Preparando la base', duracion_s: 2.5, estado: 'hecho' },
      { paso: 'redactar', etiqueta: 'Redactando el CV', duracion_s: 15.0, estado: 'hecho' }
    ]
  }
});
const textoReanudado = fila.textContent || '';

console.log(JSON.stringify({ textoInicial, textoNormal, textoCuota, textoReanudado }));
""")
    # En texto inicial muestra "En cola"
    assert "En cola" in out["textoInicial"]
    # Al llegar progreso, no se descarta (a pesar de no estar en S.posts):
    assert "Redactando el CV" in out["textoNormal"]
    assert "25%" in out["textoNormal"]
    assert "Preparando la base" in out["textoNormal"]
    # Cuando espera cuota:
    assert "Esperando cuota de AGY · se renueva a las 14:30" in out["textoCuota"]
    # Cuando se renueva cuota:
    assert "Esperando cuota" not in out["textoReanudado"]
    assert "Revisando y corrigiendo el CV" in out["textoReanudado"]
    assert "60%" in out["textoReanudado"]


@requiere_node
def test_sse_radar_progreso_muestra_y_quita_sub_en_panel():
    out = _node(NODE_DOM_FALSO + """
const lista = document.getElementById('radarProcesosLista');
const fila = T.filaProceso({
  post_id: 60,
  puesto: 'Backend Go',
  empresa: 'MercadoLibre',
  estado: 'GENERANDO',
  tipo: 'generacion',
  progreso: { etiqueta: 'Preparando la postulación', pct: 5, sub: null }
});
lista.appendChild(fila);

const textoInicial = fila.textContent || '';
const subInicial = fila.querySelector('[data-proceso-sub]');

// 1. Llega evento radar_progreso con progreso.sub
window.Radar.onProgreso({
  id: 60,
  progreso: {
    inicio: '2026-11-05T12:00:00Z',
    etiqueta: 'Revisando y corrigiendo el CV',
    sub: 'Intento 2 de 3',
    pct: 60,
    esperando_cuota: false,
    paso: 'auditar'
  }
});
const textoConSub = fila.textContent || '';
const elSub1 = fila.querySelector('[data-proceso-sub]');
const subVal1 = elSub1 ? elSub1.textContent : null;

// 2. Llega evento radar_progreso con progreso.sub = null
window.Radar.onProgreso({
  id: 60,
  progreso: {
    inicio: '2026-11-05T12:00:00Z',
    etiqueta: 'Completando puesto, CV, presentación y canales',
    sub: null,
    pct: 85,
    esperando_cuota: false,
    paso: 'completar'
  }
});
const textoSinSub = fila.textContent || '';
const elSub2 = fila.querySelector('[data-proceso-sub]');

// 3. Fila creada directamente con sub en progreso
const filaDirecta = T.filaProceso({
  post_id: 61,
  puesto: 'Dev Ruby',
  empresa: 'Stripe',
  estado: 'GENERANDO',
  tipo: 'generacion',
  progreso: { etiqueta: 'Redactando el mensaje', pct: 90, sub: 'Intento 1 de 2' }
});
const elSubDirecto = filaDirecta.querySelector('[data-proceso-sub]');
const subDirectoVal = elSubDirecto ? elSubDirecto.textContent : null;

console.log(JSON.stringify({
  subInicial: subInicial ? subInicial.textContent : null,
  textoConSub,
  subVal1,
  textoSinSub,
  elSub2Existe: !!elSub2,
  subDirectoVal
}));
""")
    assert out["subInicial"] is None
    # Muestra progreso.sub
    assert "Intento 2 de 3" in out["textoConSub"]
    assert out["subVal1"] == "Intento 2 de 3"
    # Lo quita cuando viene null
    assert "Intento 2 de 3" not in out["textoSinSub"]
    assert out["elSub2Existe"] is False
    # Fila creada inicialmente con sub
    assert out["subDirectoVal"] == "Intento 1 de 2"



@requiere_node
@pytest.mark.parametrize("volver_a_manual", [False, True])
def test_seleccion_pendiente_no_sobrevive_cambio_de_modo(volver_a_manual):
    out = _node(NODE_DOM_FALSO + """
(async () => {
  window.innerWidth = 375;
  T.S.iniciado = true;
  T.S.modo = 'manual';
  T.S.tab = 'lista';
  T.S.selId = 1;
  const anterior = { id: 1, fuente: 'manual', estado: 'LISTO', email_body: 'visto' };
  const otra = { id: 2, fuente: 'manual', estado: 'LISTO', email_body: 'otra' };
  T.S.detalle = anterior;
  T.S.posts.set(2, otra);
  T.S.edit = { postId: 1, campos: { email_body: 'editado' }, timer: null };
  let resolver;
  const pedidosAlDetalle = [];
  const respuesta = data => ({ ok: true, status: 200, json: async () => data });
  global.fetch = async (url, opts) => {
    if (opts && opts.method === 'PATCH') return new Promise(r => { resolver = r; });
    if (String(url).endsWith('/posts/2')) pedidosAlDetalle.push(url);
    return respuesta(String(url).includes('/posts?') ? [] : null);
  };
  const seleccion = window.Radar.abrirPost(2);
  for (let i = 0; i < 10; i++) await Promise.resolve();
  await window.Radar.mostrar('radar');
  if (VOLVER) await window.Radar.mostrar('manual');
  resolver(respuesta({ ...anterior, email_body: 'editado' }));
  await seleccion;
  console.log(JSON.stringify({ id: T.S.selId, tab: T.S.tab, pedidosAlDetalle }));
  process.exit(0);
})();
""".replace("VOLVER", json.dumps(volver_a_manual)))
    assert out == {"id": None, "tab": "lista", "pedidosAlDetalle": []}


@requiere_node
def test_mostrar_navegador_respeta_modo_al_recibir_estado():
    out = _node(NODE_DOM_FALSO + """
T.S.iniciado = true;
const oculto = [];
for (const modo of ['manual', 'radar', 'manual']) {
  T.S.modo = modo;
  window.Radar.onStatus({ estado: 'ESPERANDO_LOGIN', desired_running: true });
  oculto.push(document.getElementById('radarMostrarNavegadorBtn').hidden);
}
T.S.modo = 'radar';
window.Radar.onStatus({ estado: 'DETENIDO', desired_running: false });
oculto.push(document.getElementById('radarMostrarNavegadorBtn').hidden);
console.log(JSON.stringify(oculto));
process.exit(0);
""")
    assert out == [True, False, True, True]


@requiere_node
def test_guardado_previo_a_aprobar_envia_version_del_detalle():
    out = _node(NODE_APROBAR_PRELUDE + NODE_FORMULARIO + """
(async () => {
  T.S.detalle.updated_at = 'version-revisada';
  campos.radarEmailSubject.value = 'Asunto propio';
  respuestas.push({ status: 409, body: { detail: 'Cambió desde que lo revisaste' } });
  respuestas.push({ status: 200, body: { ...post, updated_at: 'version-remota', email_body: 'Mensaje remoto' } });
  await T.aprobar(false);
  console.log(JSON.stringify(cuerpos));
  process.exit(0);
})();
""")
    assert out[0]["body"]["updated_at"] == "version-revisada"
    assert all(c["m"] != "POST" for c in out)


@requiere_node
def test_historial_guardado_con_etiqueta_anterior_se_muestra_sin_jerga():
    out = _node(NODE_DOM_FALSO + """
const etiqueta = 'Completando puesto, CV, pitch y canales';
const progreso = { paso: 'completar', etiqueta, pct: 85,
  pasos: [{ paso: 'completar', etiqueta, fin: '2026-11-05' }], fin: '2026-11-05' };
const fila = T.filaProceso({ post_id: 70, estado: 'LISTO', tipo: 'generacion', progreso });
T.sembrarProgreso(70, progreso);
console.log(JSON.stringify({ texto: fila.textContent, paso: T.S.progreso.get(70).step }));
""")
    assert "pitch" not in out["texto"].lower()
    assert "presentación" in out["texto"]
    assert out["paso"] == "Completando puesto, CV, presentación y canales"


@requiere_node
def test_aprobar_con_formulario_en_foco_conserva_version_mostrada():
    out = _node(NODE_DOM_FALSO + """
(async () => {
  T.S.selId = 1;
  const post = { id: 1, estado: 'LISTO', email_to: 'a@b.test', email_subject: 'Asunto', email_body: 'Mensaje', updated_at: 'version-mostrada' };
  T.S.detalle = post;
  T.renderEmail(true);
  document.activeElement = document.getElementById('radarEmailSubject');
  // Refresco mientras hay foco: el formulario conserva la revisión anterior.
  T.S.detalle = { ...post, updated_at: 'version-remota' };
  T.renderEmail(false);
  const solicitudes = [];
  global.fetch = async (url, opts) => {
    solicitudes.push({ url, body: JSON.parse(opts.body || '{}') });
    return { ok: false, status: 409, json: async () => ({ detail: 'Cambió desde que lo revisaste' }) };
  };
  await T.aprobar(false);
  console.log(JSON.stringify(solicitudes[0]));
  process.exit(0);
})();
""")
    assert out["body"]["updated_at"] == "version-mostrada"
