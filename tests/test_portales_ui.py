"""Portales (14/11) en la interfaz, sin navegador: pestañas, modo Portales de static/radar.js y Mi perfil.

El recorrido en un navegador real a 1200 px está en tests/test_ui_navegador.py
(test_portales_a_1200_solo_ofertas_de_portales_con_logo_y_postular)."""
from pathlib import Path

from bs4 import BeautifulSoup

from test_radar_ui import _node, requiere_node

RAIZ = Path(__file__).resolve().parent.parent
HTML = (RAIZ / "static" / "index.html").read_text(encoding="utf-8")
PERFIL_JS = (RAIZ / "static" / "perfil.js").read_text(encoding="utf-8")

# Un document mínimo con createElement para los sellos y enlaces que arma radar.js
DOM = """
const crear = (tag) => ({tag, dataset: {}, children: [], className: '', textContent: '', title: '',
  appendChild(c) { this.children.push(c); return c; }});
document.createElement = crear;
const P = window.Radar._portales;
"""


def test_pestanas_manual_linkedin_portales_tickets():
    soup = BeautifulSoup(HTML, "html.parser")
    botones = soup.select("#selectorVista > button")
    assert [b.get("id") for b in botones] == [
        "btnVistaManual", "btnVistaRadar", "btnVistaPortales", "btnVistaTickets", "btnVistaPerfil"]
    assert [b.get_text(" ", strip=True) for b in botones][:4] == ["Manual", "LinkedIn", "Portales", "Tickets"]
    assert soup.select_one("#btnVistaRadar")["onclick"] == "cambiarVista('radar')"
    assert soup.select_one("#btnVistaPortales")["onclick"] == "cambiarVista('portales')"
    assert "Radar LinkedIn" not in soup.select_one("#selectorVista").get_text()
    # Mi feed es solo de LinkedIn; los chips de portal, solo de Portales
    assert soup.select_one("#radarPanelFeed").has_attr("data-solo-linkedin")
    assert soup.select_one("[data-radar-tab=feed]").has_attr("data-solo-linkedin")
    chips = soup.select("#radarFiltroPortal button[data-portal]")
    assert [c["data-portal"] for c in chips] == ["TODOS", "jooble", "indeed", "computrabajo", "bumeran"]
    assert soup.select_one("#radarFiltroPortal").has_attr("data-solo-portales")


@requiere_node
def test_cada_post_pertenece_a_su_vista():
    salida = _node(DOM + """
      const S = window.Radar._test.S;
      const posts = [{fuente: 'manual'}, {fuente: 'busqueda'}, {fuente: 'feed'}, {fuente: 'jooble'}, {fuente: 'indeed'}];
      const por = {};
      for (const modo of ['manual', 'radar', 'portales']) {
        S.modo = modo;
        por[modo] = posts.filter(p => window.Radar._test.perteneceAlModo(p)).map(p => p.fuente);
      }
      console.log(JSON.stringify({por, modos: posts.map(P.modoDePost)}));
    """)
    assert salida["por"] == {"manual": ["manual"], "radar": ["busqueda", "feed"], "portales": ["jooble", "indeed"]}
    assert salida["modos"] == ["manual", "radar", "radar", "portales", "portales"]


@requiere_node
def test_postular_en_el_sitio_solo_sin_mail_y_con_link_https():
    salida = _node(DOM + """
      const casos = [
        {fuente: 'jooble', links: ['https://acme.test/postular', 'https://jooble.test/1'], post_url: 'https://jooble.test/1'},
        {fuente: 'jooble', links: ['javascript:alert(1)', 'http://inseguro.test'], post_url: 'https://jooble.test/2'},
        {fuente: 'indeed', links: [], post_url: 'https://ar.indeed.com/viewjob?jk=1', emails: ['rrhh@acme.test']},
        {fuente: 'indeed', links: [], post_url: 'https://ar.indeed.com/viewjob?jk=2', email_to: 'rrhh@acme.test'},
        {fuente: 'indeed', links: ['no es url'], post_url: ''},
        {fuente: 'busqueda', links: ['https://acme.test/x'], post_url: 'https://www.linkedin.com/x'},
      ];
      console.log(JSON.stringify(casos.map(P.linkPostularSitio)));
    """)
    assert salida == ["https://acme.test/postular", "https://jooble.test/2", "", "", "", ""]


@requiere_node
def test_logo_y_enlace_se_arman_con_textcontent():
    salida = _node(DOM + """
      const p = {id: 7, fuente: 'indeed', links: ['https://acme.test/<b>x</b>'], post_url: ''};
      const logo = P.logoPortal(p);
      const a = P.botonPostularSitio(p);
      const cont = {firstChild: null, children: [], appendChild(c) { this.children.push(c); },
        insertBefore(c) { this.children.unshift(c); }};
      P.extrasDePortal(cont, p);
      P.extrasDePortal(cont, {fuente: 'manual'});
      console.log(JSON.stringify({logo: [logo.className, logo.textContent, logo.title, logo.dataset.portal],
        a: [a.href, a.target, a.rel, a.textContent, a.dataset.postularSitio],
        ninguno: [P.logoPortal({fuente: 'feed'}), P.botonPostularSitio({fuente: 'feed'})],
        detalle: cont.children.map(c => [c.dataset.portalDetalle, c.children.length])}));
    """)
    assert salida["logo"] == ["portal-logo portal-logo-indeed", "Indeed", "Oferta de Indeed", "indeed"]
    assert salida["a"] == ["https://acme.test/%3Cb%3Ex%3C/b%3E", "_blank", "noopener noreferrer",
                           "Postular en el sitio", "7"]
    assert salida["ninguno"] == [None, None]
    assert salida["detalle"] == [["indeed", 2]]


@requiere_node
def test_filtro_de_portal():
    salida = _node(DOM + """
      const S = window.Radar._test.S;
      S.modo = 'portales';
      const j = {fuente: 'jooble', estado: 'ANALIZADA'};
      const i = {fuente: 'indeed', estado: 'ANALIZADA'};
      const r = [];
      for (const portal of ['TODOS', 'jooble', 'indeed']) r.push([P.coincidePortal(j, portal), P.coincidePortal(i, portal)]);
      console.log(JSON.stringify(r));
    """)
    assert salida == [[True, True], [True, False], [False, True]]


@requiere_node
def test_la_vista_portales_pide_solo_las_ofertas_de_portales():
    salida = _node("""
      const pedidos = [];
      global.fetch = async (url) => { pedidos.push(String(url)); return {ok: true, status: 200, json: async () => []}; };
      const S = window.Radar._test.S;
      (async () => {
        for (const modo of ['portales', 'manual', 'radar']) { S.modo = modo; await window.Radar._test.cargarPosts(); }
        console.log(JSON.stringify(pedidos.filter(u => u.includes('/posts'))));
      })();
    """)
    assert [u.split("/posts", 1)[1] for u in salida] == [
        "?limite_descartados=500&fuente=portales", "?limite_descartados=500&fuente=manual", "?limite_descartados=500"]


def test_mi_perfil_tiene_reglas_de_portales_y_casillas_por_busqueda():
    assert "['reglas_portales', 'Reglas de Portales']" in PERFIL_JS
    assert "'/api/candidato/busquedas/portales', 'PUT'" in PERFIL_JS
    # Rediseño E3: los portales de las búsquedas se guardan con la barra única (guardarTodo)
    assert "if (t === busquedas) await guardarPortales();" in PERFIL_JS
    # Cada campo de las reglas de cada portal se edita (si falta uno, ese portal no corre)
    for campo in ("consultas_por_dia", "minutos_entre_consultas", "resultados_por_consulta", "dias", "ubicacion"):
        assert f"['{campo}'," in PERFIL_JS
    assert "innerHTML" not in PERFIL_JS


@requiere_node
def test_titulo_del_detalle_segun_la_vista():
    salida = _node("""
      const el = {textContent: ''};
      document.getElementById = (id) => (id === 'radarDetalleTitulo' ? el : null);
      document.querySelectorAll = () => [];
      const S = window.Radar._test.S;
      const r = [];
      for (const modo of ['radar', 'portales', 'manual']) { S.modo = modo; window.Radar._test.aplicarModo(); r.push(el.textContent); }
      console.log(JSON.stringify(r));
    """)
    assert salida == ["Post de LinkedIn", "Oferta", "Oferta"]
