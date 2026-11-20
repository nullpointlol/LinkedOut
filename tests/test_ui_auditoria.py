"""Radar con la auditoría del CV no aprobada: chip «Revisar», bloque del dictamen en el panel del mail, «Corregir»
y «Enviar igual» en dos pasos sobre el mismo botón.

DOM sintético en node (sin portal, sin proveedor ni datos reales).
"""
import pytest
from test_radar_ui import NODE_ARBOL_FALSO, _node
from test_ui_estados_radar import node, requiere_node

FRASE = "para automatización inteligente."
NODE_AUDITORIA = f"""
const MOTIVO = 'Redacción: «{FRASE}»';
const REPORTE = {{ approved: false, global_score: 95.5,
  deducciones: ['-15 pts en Redacción: términos no naturales («{FRASE}» (frase que suena a plantilla)).'] }};
const REVISAR = {{ ...LISTO, auditoria_ok: false, auditoria_motivos: [MOTIVO, 'Falta la keyword «SQL»', 'CV: raya'],
  postulacion: {{ status: 'READY', reporte_auditoria: REPORTE }} }};
const BIEN = {{ ...LISTO, auditoria_ok: true, postulacion: {{ status: 'READY', reporte_auditoria: {{ approved: true }} }} }};
"""


def ui(body: str):
    return node(NODE_AUDITORIA + body)


@requiere_node
def test_lista_con_auditoria_no_aprobada_dice_revisar_en_tarjeta_y_detalle():
    out = ui("""
const card = T.crearCard(REVISAR);
T.S.selId = 1; T.S.detalle = REVISAR; T.renderDetalle();
console.log(JSON.stringify({
  etiqueta: U.etiquetaEstado(REVISAR), bien: U.etiquetaEstado(BIEN).texto,
  aprobada: U.etiquetaEstado({ ...REVISAR, aprobado_at: '2026-11-20 10:00:00' }).texto,
  card: card.innerHTML, detalle: document.getElementById('radarDetallePost').innerHTML,
  enListas: U.coincideFiltro(REVISAR, 'LISTO'),
}));
""")
    assert out["etiqueta"]["texto"] == "Revisar"
    assert out["etiqueta"]["sub"] == f"Redacción: «{FRASE}»"
    assert out["bien"] == "Lista para enviar" and out["aprobada"] == "Por enviar"
    for html in (out["card"], out["detalle"]):
        assert 'class="chip chip-warn">Revisar</span>' in html, html
        assert "Lista para enviar" not in html
    # El motivo, en una línea al lado del chip
    assert f'<span class="oferta-subestado">Redacción: «{FRASE}»</span>' in out["card"]
    assert out["enListas"] is True


@requiere_node
def test_bloque_del_dictamen_arriba_de_los_botones_con_puntaje_y_frase():
    out = ui("""
const bloque = document.getElementById('radarAuditoriaAviso');
const ver = (p) => {
  T.S.selId = p.id; T.S.detalle = p; T.renderEmail(true);
  return { hidden: bloque.hidden, lineas: bloque.children.map((c) => c.textContent), tags: bloque.children.map((c) => c.tag) };
};
console.log(JSON.stringify({ revisar: ver(REVISAR), bien: ver(BIEN) }));
""")
    revisar = out["revisar"]
    assert revisar["hidden"] is False
    assert revisar["lineas"][0] == "La auditoría no aprobó el CV · 95,5/100"
    assert revisar["lineas"][1] == f"Redacción: «{FRASE}»"
    assert len(revisar["lineas"]) == 3  # como mucho 3 líneas
    assert out["bien"]["hidden"] is True and out["bien"]["lineas"] == []


@requiere_node
def test_bloque_usa_texto_y_no_html():
    out = ui("""
const malo = { ...REVISAR, auditoria_motivos: ['<img src=x onerror=alert(1)>'] };
T.S.selId = 1; T.S.detalle = malo; T.renderEmail(true);
const bloque = document.getElementById('radarAuditoriaAviso');
console.log(JSON.stringify({ html: bloque.innerHTML || '', linea: bloque.children[1].textContent }));
""")
    assert out["html"] == "" and out["linea"] == "<img src=x onerror=alert(1)>"


@requiere_node
def test_por_revisar_lista_la_auditoria_sin_boton_visto():
    out = _node(NODE_ARBOL_FALSO + f"""
const fila = T.itemRevision({{ tipo: 'auditoria', post_id: 1, frase: 'Redacción: «{FRASE}»', puesto: 'Analista', empresa: 'Acme' }});
console.log(JSON.stringify({{ botones: botones(fila).map((b) => b.textContent), texto: textoDe(fila) }}));
""")
    assert out["botones"] == ["Abrir"]
    assert out["texto"].startswith("Analista · Acme") and f"Redacción: «{FRASE}»" in out["texto"]


@requiere_node
def test_aprobar_todos_deja_afuera_las_que_no_aprobo_la_auditoria():
    out = ui("""
T.S.posts.set(1, REVISAR);
T.S.posts.set(2, { ...BIEN, id: 2 });
console.log(JSON.stringify(T.listosSinAprobar().map((p) => p.id)));
""")
    assert out == [2]


@requiere_node
def test_el_detalle_sin_campos_de_auditoria_pisa_el_revisar_de_la_lista():
    out = ui("""
const { postulacion, ...resumen } = REVISAR;
T.S.posts.set(1, resumen);

const detalleCorregido = { ...LISTO, postulacion: { status: 'READY', reporte_auditoria: { approved: true } } };
T.S.selId = 1;
T.aplicarDetalle(detalleCorregido, true);
console.log(JSON.stringify({ antes: U.etiquetaEstado(resumen).texto, despues: U.etiquetaEstado(T.S.posts.get(1)).texto }));
""")
    assert out == {"antes": "Revisar", "despues": "Lista para enviar"}


@requiere_node
def test_boton_corregir_solo_si_la_auditoria_no_aprobo():
    out = ui("""
const btn = boton('radarCorregirBtn');
const ver = (p) => {
  T.S.selId = p.id; T.S.detalle = p; T.renderEmail(true);
  return { hidden: btn.hidden, texto: btn.textContent.trim(), iconos: iconos(btn) };
};
console.log(JSON.stringify({ revisar: ver(REVISAR), bien: ver(BIEN),
  aprobada: ver({ ...REVISAR, aprobado_at: '2026-11-20 10:00:00' }) }));
""")
    assert out["revisar"] == {"hidden": False, "texto": "Corregir", "iconos": ["wrench"]}
    assert out["bien"]["hidden"] is True and out["aprobada"]["hidden"] is True


@requiere_node
def test_corregir_dice_corrigiendo_mientras_corre_y_recarga_el_detalle():
    out = ui("""
const btn = boton('radarCorregirBtn');
const pedidos = [];
let soltar;
global.fetch = (url, opts) => {
  pedidos.push({ url, metodo: opts && opts.method });
  if (opts && opts.method === 'POST') {
    return new Promise((ok) => { soltar = () => ok({ ok: true, status: 200, json: async () => ({ aprobado: true, lineas: ['V1'], motivos: [] }) }); });
  }
  return Promise.resolve({ ok: true, status: 200, json: async () => ({ ...LISTO, postulacion: { status: 'READY', reporte_auditoria: { approved: true } } }) });
};
T.S.selId = 1; T.S.detalle = REVISAR; T.S.posts.set(1, REVISAR); T.renderEmail(true);
const corriendo = T.corregirAuditoria();
setTimeout(async () => {
  const durante = { texto: btn.textContent.trim(), disabled: btn.disabled, iconos: iconos(btn) };
  // Un re-render a mitad de camino no lo vuelve a «Corregir»
  T.renderEmail(true);
  const rerender = btn.textContent.trim();
  soltar();
  await corriendo;
  setTimeout(() => {
    console.log(JSON.stringify({ durante, rerender, pedidos, despues: { hidden: btn.hidden, disabled: btn.disabled },
      etiqueta: U.etiquetaEstado(T.S.posts.get(1)).texto }));
    process.exit(0);
  }, 20);
}, 20);
""")
    assert out["durante"] == {"texto": "Corrigiendo…", "disabled": True, "iconos": ["loader-circle.girando"]}
    assert out["rerender"] == "Corrigiendo…"
    assert out["pedidos"][0] == {"url": "/api/radar/posts/1/corregir-auditoria", "metodo": "POST"}
    assert any(p["url"] == "/api/radar/posts/1" for p in out["pedidos"][1:])
    assert out["despues"]["hidden"] is True and out["etiqueta"] == "Lista para enviar"


NODE_ENVIO = """
const btn = boton('radarAprobarBtn');
const cuerpos = [];
global.fetch = async (url, opts) => {
  if (opts && opts.method === 'POST') cuerpos.push({ url, cuerpo: JSON.parse(opts.body || '{}') });
  return { ok: true, status: 200, json: async () => ({ ...LISTO, aprobado_at: '2026-11-20 10:00:00' }) };
};
const ver = () => ({ texto: btn.textContent.trim(), iconos: iconos(btn), clase: btn.className });
const abrir = (p) => { T.S.selId = p.id; T.S.detalle = p; T.S.posts.set(p.id, p); T.renderEmail(true); };
"""


@requiere_node
def test_enviar_igual_pide_confirmacion_en_el_mismo_boton():
    out = ui(NODE_ENVIO + """
(async () => {
  abrir(REVISAR);
  const inicial = ver();
  await T.aprobar(false);
  const confirmar = ver();
  const tras1 = cuerpos.length;
  // Un redibujo del mismo post no pierde la confirmación
  T.renderEmail(false);
  const redibujo = ver().texto;
  await T.aprobar(false);
  console.log(JSON.stringify({ inicial, confirmar, tras1, redibujo, cuerpos }));
  process.exit(0);
})();
""")
    assert out["inicial"]["texto"] == "Enviar igual" and out["inicial"]["iconos"] == ["send"]
    assert out["confirmar"]["texto"] == "¿Enviar sin aprobar?" and out["confirmar"]["iconos"] == ["triangle-alert"]
    assert "btn-borde-warn" in out["confirmar"]["clase"]
    assert out["tras1"] == 0, "el primer clic no envía nada"
    assert out["redibujo"] == "¿Enviar sin aprobar?"
    assert len(out["cuerpos"]) == 1
    assert out["cuerpos"][0]["url"] == "/api/radar/posts/1/approve"
    assert out["cuerpos"][0]["cuerpo"]["forzar_auditoria"] is True


@requiere_node
def test_la_confirmacion_vence_y_no_pasa_a_otro_post():
    out = ui(NODE_ENVIO + """
(async () => {
  abrir(REVISAR);
  await T.aprobar(false);
  abrir({ ...REVISAR, id: 2 });
  const otro = ver().texto;
  await T.aprobar(false);
  const otroConfirma = ver().texto;
  console.log(JSON.stringify({ otro, otroConfirma, enviados: cuerpos.length, plazo: T.MS_CONFIRMAR_DESCARTE }));
  process.exit(0);
})();
""")
    assert out == {"otro": "Enviar igual", "otroConfirma": "¿Enviar sin aprobar?", "enviados": 0, "plazo": 3500}


@requiere_node
def test_con_la_auditoria_aprobada_enviar_sale_al_primer_clic_sin_permiso():
    out = ui(NODE_ENVIO + """
(async () => {
  abrir(BIEN);
  const inicial = ver().texto;
  await T.aprobar(false);
  console.log(JSON.stringify({ inicial, cuerpos }));
  process.exit(0);
})();
""")
    assert out["inicial"] == "Enviar"
    assert len(out["cuerpos"]) == 1 and out["cuerpos"][0]["cuerpo"]["forzar_auditoria"] is False
