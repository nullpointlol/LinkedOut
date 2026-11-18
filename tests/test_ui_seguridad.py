"""XSS en el portal real, en el navegador (A1 y A2 de docs/seguridad/auditoria-20261117.md, 18/11).

El texto de las ofertas es de terceros: un requisito o un CV generado con HTML o JavaScript adentro se tiene que ver
como texto, nunca ejecutarse.
"""
import pytest

from tests.test_ui_navegador import _abrir, navegador, portal  # noqa: F401  (fixtures del portal en vivo)

pytestmark = [pytest.mark.ui, pytest.mark.timeout(240)]

REQUISITO_MALICIOSO = "x');window.__xss=1;//\"><img src=x onerror=\"window.__xss=2\">"
CV_MALICIOSO = ("**negrita** <img src=x onerror=\"window.__xss=3\"> <script>window.__xss=4</script>\n\n"
                "[malo](javascript:window.__xss=5) [bueno](https://acme.test/aviso) ![foto](javascript:window.__xss=6)\n\n"
                "<iframe src=\"javascript:window.parent.__xss=7\"></iframe>")


def test_un_requisito_con_codigo_se_cicla_como_texto(navegador, portal):  # noqa: F811
    base, _ = portal
    contexto, page, errores = _abrir(navegador, base, 1200)
    try:
        resultado = page.evaluate("""(nombre) => {
          window.__llamadas = [];
          window.ciclarEstadoTag = (id, tag) => window.__llamadas.push([id, tag]);
          renderMatrizOferta({herramientas: [{nombre, estado: 'BRECHA_REAL'}], metodologias: [], responsabilidades: [],
                              otros: []}, 7);
          const cont = document.getElementById('contenedorMatrizOferta');
          const chips = cont.querySelectorAll('[data-ciclar-tag]');
          chips.forEach((c) => c.click());
          return {chips: chips.length, llamadas: window.__llamadas, xss: window.__xss ?? null,
                  imagenes: cont.querySelectorAll('img').length, inline: cont.querySelectorAll('[onclick]').length};
        }""", REQUISITO_MALICIOSO)
        assert resultado["chips"] >= 1
        assert resultado["llamadas"] == [[7, REQUISITO_MALICIOSO]] * resultado["chips"]
        assert resultado["xss"] is None
        assert resultado["imagenes"] == 0
        assert resultado["inline"] == 0
        assert errores.lista == []
        # Control: con el onclick de antes (escapeHtml dentro del atributo) el mismo nombre se ejecuta
        assert page.evaluate("""(nombre) => {
          window.__xss = null;
          const s = document.createElement('span');
          s.innerHTML = `<span onclick="ciclarEstadoTag(7, '${escapeHtml(nombre)}')">x</span>`;
          s.firstChild.click();
          return window.__xss;
        }""", REQUISITO_MALICIOSO) == 1
    finally:
        contexto.close()


def test_el_cv_con_html_se_muestra_como_texto(navegador, portal):  # noqa: F811
    base, _ = portal
    contexto, page, errores = _abrir(navegador, base, 1200)
    try:
        resultado = page.evaluate("""(md) => {
          const div = document.createElement('div');
          document.body.appendChild(div);
          div.innerHTML = markdownSeguro(md);
          div.querySelectorAll('a').forEach((a) => a.addEventListener('click', (e) => e.preventDefault()));
          div.querySelectorAll('a').forEach((a) => a.click());
          return {xss: window.__xss ?? null, peligrosos: div.querySelectorAll('img, script, iframe').length,
                  negrita: div.querySelectorAll('strong').length, links: [...div.querySelectorAll('a')].map((a) => a.href),
                  texto: div.textContent};
        }""", CV_MALICIOSO)
        assert resultado["xss"] is None
        assert resultado["peligrosos"] == 0
        assert resultado["negrita"] == 1
        assert resultado["links"] == ["https://acme.test/aviso"]
        assert "<img src=x" in resultado["texto"]  # el HTML se ve, como texto
        assert errores.lista == []
        # Control: marked.parse a secas deja pasar el HTML (por eso existe markdownSeguro)
        assert page.evaluate("(md) => { const d = document.createElement('div'); d.innerHTML = marked.parse(md);"
                             " return d.querySelectorAll('img, iframe').length; }", CV_MALICIOSO) > 0
    finally:
        contexto.close()
