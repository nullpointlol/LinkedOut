"""Tests para condiciones logísticas, brechas fuertes y visibilidad de descarte en UI Radar."""
import os
import pytest
from test_brechas_radar_ui import node


def test_condicion_no_cicla_ni_cuenta():
    # invariante: requisito no logístico cuenta en desglose y tiene botón de ciclo con data-req
    out_inv = node("""
const reqs = [{nombre: 'SQL', estado: 'MATCH_DIRECTO'}];
const d = T.desgloseAfinidad(reqs);
const res = T.resumenAfinidad(reqs);
post.analisis.requisitos = reqs;
T.S.detalle = post; T.S.selId = post.id; T.renderDetalle();
const detail = document.getElementById('radarDetallePost');
const reqBtns = find(detail, 'data-req');
console.log(JSON.stringify({
  d,
  res,
  detailHtml: detail.innerHTML,
  reqBtns: reqBtns.map(b => b.getAttribute('data-req'))
}));
""")
    assert out_inv["d"]["total"] == 1
    assert out_inv["d"]["cumple"] == 1
    assert "1 ✓" in out_inv["res"]["texto"]
    assert "data-req=" in out_inv["detailHtml"] or out_inv["reqBtns"] == ["SQL"]
    if os.getenv("INVARIANT_CHECK") == "1":
        return

    # Defecto: logistico=true muestra chip gris 'Condición', sin data-req, no cicla y está excluido de conteos
    out = node("""
(async () => {
const reqs = [
  {nombre: 'SQL', estado: 'MATCH_DIRECTO'},
  {nombre: 'Horario Lunes a Viernes', estado: 'MATCH_DIRECTO', logistico: true},
  {nombre: 'Incorporación inmediata', estado: 'BRECHA_REAL', logistico: true, aprendido: true},
  {nombre: 'Zona horaria compatible', estado: 'MATCH_DIRECTO', logistico: true, eliminatorio_usuario: true}
];
const d = T.desgloseAfinidad(reqs);
const res = T.resumenAfinidad(reqs);

post.analisis.requisitos = reqs;
T.S.detalle = post; T.S.selId = post.id; T.S.posts.set(post.id, post);
T.renderDetalle();
const detail = document.getElementById('radarDetallePost');
const rows = all(detail).filter(n => n.tag === 'li');

const infoRows = rows.map(r => ({
  text: r.textContent,
  reqBtns: find(r, 'data-req').map(b => b.getAttribute('data-req')),
  condicionChips: all(r).filter(n => n.textContent === 'Condición').map(n => ({
    tag: n.tag,
    hasDataReq: n.getAttribute && n.getAttribute('data-req') !== undefined,
    classes: n.className
  })),
  descarteBtns: find(r, 'data-descarte-req').map(b => ({
    label: b.getAttribute('aria-label'),
    pressed: b.getAttribute('aria-pressed')
  }))
}));

const calls = [];
global.fetch = async (url, opts) => {
  calls.push(JSON.parse(opts.body));
  return {ok: true, status: 200, json: async () => post};
};
await T.ciclarRequisito('Horario Lunes a Viernes');
await T.ciclarRequisito('Incorporación inmediata');
await T.ciclarRequisito('SQL');

console.log(JSON.stringify({d, res, infoRows, calls}));
process.exit(0);
})();
""")
    # Excluido de todos los contadores incluido total/aprendido:
    assert out["d"]["total"] == 1
    assert out["d"]["cumple"] == 1
    assert out["d"]["brecha"] == 0
    assert out["d"]["aprendido"] == 0
    assert "1 ✓" in out["res"]["texto"]
    assert "1 requisito" in out["res"]["titulo"]

    # Fila 0: SQL (no logístico)
    assert out["infoRows"][0]["reqBtns"] == ["SQL"]
    assert len(out["infoRows"][0]["condicionChips"]) == 0

    # Fila 1: Horario Lunes a Viernes (logístico)
    chip1 = out["infoRows"][1]["condicionChips"]
    assert len(chip1) == 1
    assert chip1[0]["hasDataReq"] is False
    assert chip1[0]["tag"] != "button"
    assert "text-zinc-400" in chip1[0]["classes"]
    assert len(out["infoRows"][1]["reqBtns"]) == 0
    assert len(out["infoRows"][1]["descarteBtns"]) == 0

    # Fila 2: Incorporación inmediata (logístico BRECHA_REAL no marcado)
    assert len(out["infoRows"][2]["reqBtns"]) == 0
    assert len(out["infoRows"][2]["descarteBtns"]) == 0

    # Fila 3: Zona horaria (logístico con eliminatorio_usuario: true)
    assert len(out["infoRows"][3]["reqBtns"]) == 0
    descarte3 = out["infoRows"][3]["descarteBtns"]
    assert len(descarte3) == 1
    assert descarte3[0]["pressed"] == "true"
    assert "Zona horaria compatible" in descarte3[0]["label"]

    # Logístico nunca cicla
    assert out["calls"] == [{"nombre": "SQL"}]


def test_visibilidad_me_descarta():
    # invariante: base medida, control de descarte en motivo confirmado conserva aria-pressed
    out_inv = node("""
const reqs = [
  {nombre: 'Lugar', estado: 'BRECHA_REAL', tipo: 'REQUISITO_EXCLUYENTE', eliminatorio: true}
];
post.analisis.requisitos = reqs;
T.S.detalle = post; T.S.selId = post.id; T.renderDetalle();
const detail = document.getElementById('radarDetallePost');
const descarte = find(detail, 'data-descarte-req')[0];
console.log(JSON.stringify({
  hasControl: !!descarte,
  pressed: descarte ? descarte.getAttribute('aria-pressed') : null,
  classes: descarte ? descarte.className : ''
}));
""")
    assert out_inv["hasControl"] is True
    assert out_inv["pressed"] == "true"
    assert "text-xs" in out_inv["classes"]
    if os.getenv("INVARIANT_CHECK") == "1":
        return

    # Defecto: 'Me descarta' visible en BRECHA_REAL+REQUISITO_EXCLUYENTE o ya marcado (eliminatorio_usuario===true o motivo confirmado),
    # incluso si cambió a MATCH_DIRECTO; false explícito veta marca del modelo. Estados cerrados mantienen bloqueo.
    out = node("""
const reqs = [
  // 1. BRECHA_REAL + REQUISITO_EXCLUYENTE (no marcado) -> VISIBLE (pressed=false)
  {nombre: 'Req1_BrechaExcl', estado: 'BRECHA_REAL', tipo: 'REQUISITO_EXCLUYENTE'},
  // 2. BRECHA_REAL + REQUISITO_DESEABLE (no marcado) -> NO VISIBLE
  {nombre: 'Req2_BrechaDeseable', estado: 'BRECHA_REAL', tipo: 'REQUISITO_DESEABLE'},
  // 3. TRANSFERIBLE + REQUISITO_EXCLUYENTE (no marcado) -> NO VISIBLE
  {nombre: 'Req3_TransfExcl', estado: 'TRANSFERIBLE', tipo: 'REQUISITO_EXCLUYENTE'},
  // 4. TRANSFERIBLE + REQUISITO_DESEABLE (no marcado) -> NO VISIBLE
  {nombre: 'Req4_TransfDeseable', estado: 'TRANSFERIBLE', tipo: 'REQUISITO_DESEABLE'},
  // 5. MATCH_DIRECTO + REQUISITO_EXCLUYENTE (no marcado) -> NO VISIBLE
  {nombre: 'Req5_MatchExcl', estado: 'MATCH_DIRECTO', tipo: 'REQUISITO_EXCLUYENTE'},
  // 6. MATCH_DIRECTO pero ya marcado por usuario (eliminatorio_usuario === true) -> VISIBLE (pressed=true)
  {nombre: 'Req6_MatchMarcado', estado: 'MATCH_DIRECTO', tipo: 'REQUISITO_EXCLUYENTE', eliminatorio_usuario: true},
  // 7. TRANSFERIBLE marcado por usuario -> VISIBLE (pressed=true)
  {nombre: 'Req7_TransfMarcado', estado: 'TRANSFERIBLE', tipo: 'REQUISITO_DESEABLE', eliminatorio_usuario: true},
  // 8. En motivos (confirmado por modelo) PERO eliminatorio_usuario === false explícito
  {nombre: 'Lugar', estado: 'BRECHA_REAL', tipo: 'REQUISITO_EXCLUYENTE', eliminatorio_usuario: false},
  // 9. En motivos pero TRANSFERIBLE con eliminatorio_usuario === false -> NO VISIBLE
  {nombre: 'Viajes', estado: 'TRANSFERIBLE', tipo: 'REQUISITO_EXCLUYENTE', eliminatorio_usuario: false}
];

post.analisis.requisitos = reqs;
T.S.detalle = post; T.S.selId = post.id; T.renderDetalle();
const detail = document.getElementById('radarDetallePost');
const rows = all(detail).filter(n => n.tag === 'li');

const matrix = rows.map((r, i) => {
  const req = reqs[i];
  const btns = find(r, 'data-descarte-req').map(b => ({
    name: b.getAttribute('data-descarte-req'),
    label: b.getAttribute('aria-label'),
    title: b.getAttribute('title'),
    text: b.textContent,
    pressed: b.getAttribute('aria-pressed'),
    hasMark: b.getAttribute('data-descarte-marca') !== undefined
  }));
  return {req: req.nombre, btns};
});

// Comprobar estado cerrado (ENVIADO)
post.estado = 'ENVIADO';
T.S.detalle = post;
T.renderDetalle();
const closedControls = find(document.getElementById('radarDetallePost'), 'data-descarte-req').length;
post.estado = 'ANALIZADA';

console.log(JSON.stringify({matrix, closedControls}));
""")
    m = {item["req"]: item["btns"] for item in out["matrix"]}

    # 1. BRECHA_REAL + REQUISITO_EXCLUYENTE -> botón 'Me descarta', aria-pressed="false"
    assert len(m["Req1_BrechaExcl"]) == 1
    b1 = m["Req1_BrechaExcl"][0]
    assert b1["text"] == "Me descarta"
    assert b1["label"] == "Me descarta: Req1_BrechaExcl"
    assert b1["title"] == "Manda la oferta a Ignoradas"
    assert b1["pressed"] == "false"
    assert b1["hasMark"] is False

    # 2, 3, 4, 5: no visibles
    assert len(m["Req2_BrechaDeseable"]) == 0
    assert len(m["Req3_TransfExcl"]) == 0
    assert len(m["Req4_TransfDeseable"]) == 0
    assert len(m["Req5_MatchExcl"]) == 0

    # 6. MATCH_DIRECTO con eliminatorio_usuario === true -> visible, pressed="true"
    assert len(m["Req6_MatchMarcado"]) == 1
    b6 = m["Req6_MatchMarcado"][0]
    assert b6["text"] == "Me descarta"
    assert b6["label"] == "Me descarta: Req6_MatchMarcado"
    assert b6["pressed"] == "true"
    assert b6["hasMark"] is True

    # 7. TRANSFERIBLE con eliminatorio_usuario === true -> visible, pressed="true"
    assert len(m["Req7_TransfMarcado"]) == 1
    b7 = m["Req7_TransfMarcado"][0]
    assert b7["pressed"] == "true"
    assert b7["hasMark"] is True

    # 8. 'Lugar' está en motivos, pero eliminatorio_usuario === false veta la marca
    assert len(m["Lugar"]) == 1
    b8 = m["Lugar"][0]
    assert b8["pressed"] == "false"
    assert b8["hasMark"] is False

    # 9. 'Viajes' está en motivos pero es TRANSFERIBLE y eliminatorio_usuario === false -> no visible
    assert len(m["Viajes"]) == 0

    # Estados cerrados mantienen bloqueo
    assert out["closedControls"] == 0


def test_dura_tooltip_y_seguridad():
    # invariante: título/justificación tooltip asignado sin evaluar script
    out_inv = node("""
const reqs = [{nombre: 'Seguridad', estado: 'BRECHA_REAL', tipo: 'REQUISITO_EXCLUYENTE', justificacion: 'Prueba de tooltip'}];
post.analisis.requisitos = reqs;
T.S.detalle = post; T.S.selId = post.id; T.renderDetalle();
const detail = document.getElementById('radarDetallePost');
const row = all(detail).filter(n => n.tag === 'li')[0];
console.log(JSON.stringify({
  rowTitle: row ? row.title : null
}));
""")
    assert out_inv["rowTitle"] == "Prueba de tooltip"
    if os.getenv("INVARIANT_CHECK") == "1":
        return

    # Defecto:
    # 1. Dura activa en BRECHA_REAL muestra marca visible 'Brecha fuerte', title=justificacion mediante textContent/setAttribute
    # 2. Dura en MATCH_DIRECTO o TRANSFERIBLE NO muestra 'Brecha fuerte' (inactiva)
    # 3. Payload HTML en nombre y justificación no interpola HTML (sin innerHTML con datos)
    # 4. Ícono de aprendido es >= 12 px (text-xs, no text-[8px])
    out = node("""
const xssPayload = '<img src=x onerror=alert(1)>';
const reqs = [
  {nombre: 'DevOps ' + xssPayload, estado: 'BRECHA_REAL', tipo: 'REQUISITO_EXCLUYENTE', dura: 'anios', justificacion: 'Pide 5 años ' + xssPayload, aprendido: true},
  {nombre: 'Salesforce', estado: 'MATCH_DIRECTO', tipo: 'REQUISITO_EXCLUYENTE', dura: 'nucleo', justificacion: 'Herramienta principal'},
  {nombre: 'PMP', estado: 'TRANSFERIBLE', tipo: 'REQUISITO_DESEABLE', dura: 'cert', justificacion: 'Certificación PMP'}
];

post.analisis.requisitos = reqs;
T.S.detalle = post; T.S.selId = post.id; T.renderDetalle();
const detail = document.getElementById('radarDetallePost');
const rows = all(detail).filter(n => n.tag === 'li');

const rowData = rows.map(r => {
  const marcasDuras = all(r).filter(n => n.textContent === 'Brecha fuerte').map(n => ({
    tag: n.tag,
    title: n.getAttribute ? n.getAttribute('title') : n.title,
    classes: n.className
  }));
  const learnedIcons = all(r).filter(n => n.className && n.className.includes('fa-brain')).map(n => ({
    classes: n.className
  }));
  const imgs = all(r).filter(n => n.tag === 'img');
  return {
    textContent: r.textContent,
    innerHTML: r.innerHTML,
    marcasDuras,
    learnedIcons,
    imgsCount: imgs.length
  };
});

console.log(JSON.stringify({rowData}));
""")
    # Fila 0: DevOps (BRECHA_REAL + dura='anios')
    r0 = out["rowData"][0]
    assert len(r0["marcasDuras"]) == 1
    m0 = r0["marcasDuras"][0]
    assert "Pide 5 años <img src=x onerror=alert(1)>" == m0["title"]
    assert "text-xs" in m0["classes"]

    # Fila 1: Salesforce (MATCH_DIRECTO con dura='nucleo') -> marca dura INACTIVA
    assert len(out["rowData"][1]["marcasDuras"]) == 0

    # Fila 2: PMP (TRANSFERIBLE con dura='cert') -> marca dura INACTIVA
    assert len(out["rowData"][2]["marcasDuras"]) == 0

    # Seguridad contra XSS: ningún <img> fue creado como tag DOM a partir de datos
    assert r0["imgsCount"] == 0
    assert "<img src=x onerror=alert(1)>" in r0["textContent"]

    # Ícono aprendido: debe ser >= 12 px (usar text-xs, no text-[8px])
    assert len(r0["learnedIcons"]) == 1
    assert "text-xs" in r0["learnedIcons"][0]["classes"]
    assert "text-[8px]" not in r0["learnedIcons"][0]["classes"]
