"""Tarea 7 (UX): textos sin jerga y selector de modelo de AGY dentro del menú «Ajustes»."""
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

from bs4 import BeautifulSoup

INDEX = Path("static/index.html")
APP_JS = Path("static/app.js")
RADAR_JS = Path("static/radar.js")


def _soup():
    return BeautifulSoup(INDEX.read_text(encoding="utf-8"), "html.parser")


def _texto_visible(soup):
    """Texto visible del documento + title/aria-label/placeholder (sin <script>/<style>)."""
    copia = BeautifulSoup(str(soup), "html.parser")
    for t in copia(["script", "style"]):
        t.decompose()
    partes = [copia.get_text(" ")]
    for el in copia.find_all(True):
        for atributo in ("title", "aria-label", "placeholder"):
            if el.get(atributo):
                partes.append(el[atributo])
    return " ".join(partes)


def _norm(el):
    return " ".join(el.get_text(" ").split())


# ------------------------------------------------------------------ textos


def test_umbral_pasa_a_afinidad_minima():
    soup = _soup()
    label = soup.find("label", attrs={"for": "radarUmbralInput"})
    assert label is not None and _norm(label) == "Afinidad mínima"
    assert soup.find(id="radarUmbralInput") is not None  # mismo id: radar.js lo sigue usando


def test_cv_y_formato_sin_xyz():
    soup = _soup()
    assert _norm(soup.find(id="rightTab-cv")) == "CV"
    sub = soup.find(id="subtab-cv")
    assert "Formato Google XYZ" not in sub.get_text()
    assert any(_norm(s) == "Formato" for s in sub.find_all("span"))


def test_volver_a_analizar_reemplaza_re_analizar_oferta():
    soup = _soup()
    btn = soup.find(id="btnRegenerarGlobal")
    assert "Volver a analizar" in _norm(btn)
    assert "Re-analizar" not in str(btn)


def test_sin_menu_herramientas_y_se_quedan_ajustes_y_escape():
    """S5: el menú «Herramientas» y su modal se fueron; Ajustes y el Escape de «Nueva oferta» siguen."""
    soup = _soup()
    for id_ in ("contenedorMenuToolkit", "btnToolkit", "menuToolkitDropdown", "modalToolkit"):
        assert soup.find(id=id_) is None, f"Quedó #{id_}"
    assert soup.find(id="modalNueva") is not None
    js = APP_JS.read_text(encoding="utf-8")
    for viejo in ("toggleToolkitMenu", "openToolkitTool", "renderPreflightCheck", "/api/toolkit", "/toolkit/"):
        assert viejo not in js, f"Quedó «{viejo}» en app.js"
    assert "function toggleAjustesMenu" in js and "window.toggleAjustesMenu" in js
    assert "function cerrarModalesConEscape" in js


def test_no_quedan_textos_viejos_visibles():
    visible = _texto_visible(_soup())
    for viejo in ("Toolkit", "XYZ", "Re-analizar", "Umbral", "Agéntico"):
        assert viejo not in visible, f"Quedó el texto viejo «{viejo}»"
    assert not re.search(r"re-?analiz", visible, re.I), "Quedó una variante de «Re-analizar»/«Reanalizar»"
    assert "Agéntico" not in _soup().title.get_text()
    assert "pipeline" not in visible.lower(), "Quedó la jerga «pipeline» en el texto visible"


def test_js_sin_jerga_en_mensajes():
    for ruta in (APP_JS, RADAR_JS):
        js = ruta.read_text(encoding="utf-8")
        assert "El umbral debe ser" not in js
    assert "La afinidad mínima debe ser un entero entre 0 y 100." in RADAR_JS.read_text(encoding="utf-8")


# ------------------------------------------------------------------ Ajustes


def test_boton_ajustes_engranaje_accesible():
    soup = _soup()
    header = soup.find("header")
    btn = header.find(attrs={"aria-label": "Ajustes"})
    assert btn is not None and btn.name == "button"
    assert btn.get("type") == "button"
    assert btn.get("aria-expanded") == "false"
    assert btn.get("aria-haspopup")
    assert btn.select_one('svg use[href="/static/iconos.svg#settings"]') is not None  # engranaje (rediseño E1)
    menu = soup.find(id=btn.get("aria-controls"))
    assert menu is not None and header.find(id=menu["id"]) is not None
    assert "hidden" in menu.get("class", [])  # arranca cerrado


def test_menu_ajustes_contiene_selector_de_modelo_y_effort():
    soup = _soup()
    btn = soup.find("header").find(attrs={"aria-label": "Ajustes"})
    menu = soup.find(id=btn["aria-controls"])
    for ident in ("telemetriaModelosLista", "telemetriaEffort", "telemetriaModeloNombre",
                  "labelModeloActivo", "labelThinkingTokensBadge", "telemetriaCliEstado"):
        el = soup.find(id=ident)
        assert el is not None, f"Falta #{ident}"
        assert menu.find(id=ident) is not None, f"#{ident} debe vivir dentro del menú Ajustes"
        assert len(soup.find_all(id=ident)) == 1
    # El selector ya no queda suelto en la barra superior, fuera del menú
    assert soup.find(id="contenedorSelectorModelo") is not None
    assert soup.find(id="btnVerTelemetria") is not None
    assert soup.find(id="indicadorPulsoModelo") is not None


def test_panel_ajustes_no_desborda_en_celular():
    """En 375 px el panel es fijo con márgenes (no un w-80 anclado al botón) para no generar scroll horizontal."""
    soup = _soup()
    btn = soup.find("header").find(attrs={"aria-label": "Ajustes"})
    clases = soup.find(id=btn["aria-controls"]).get("class", [])
    # Rediseño E4b: .ajustes (modales.css): fijo con márgenes en el celular, anclado al engranaje desde 640 px
    assert "ajustes" in clases
    from pathlib import Path
    css = (Path("static") / "css" / "modales.css").read_text(encoding="utf-8")
    base = _regla_ajustes(css)
    assert "position: fixed" in base and "left: var(--sp-3)" in base and "right: var(--sp-3)" in base
    assert "width" not in base.replace("max-width", "")
    ancha = _regla_ajustes(css, "@media (min-width: 640px)")
    assert "position: absolute" in ancha and "right: 0" in ancha


def _regla_ajustes(css, dentro_de=None):
    """Cuerpo de la regla «.ajustes {…}» (la primera, o la que está dentro del @media indicado)."""
    if dentro_de:
        css = css[css.index(dentro_de):]
    i = css.index(".ajustes {")
    return css[i:css.index("}", i)]


def _node(script):
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(script)
        nombre = f.name
    try:
        res = subprocess.run(["node", nombre], capture_output=True, text=True, check=True)
        return json.loads(res.stdout.strip().splitlines()[-1])
    finally:
        os.unlink(nombre)


def test_menu_ajustes_teclado_alterna_y_escape_cierra():
    js = APP_JS.read_text(encoding="utf-8")
    script = f"""
    const els = {{}};
    function el(id) {{
      if (!els[id]) els[id] = {{
        id, attrs: {{}}, focused: false, textContent: '', innerHTML: '', style: {{}}, className: '',
        classList: {{ s: new Set(['hidden']), add(c) {{ this.s.add(c); }}, remove(c) {{ this.s.delete(c); }}, contains(c) {{ return this.s.has(c); }} }},
        setAttribute(k, v) {{ this.attrs[k] = String(v); }}, getAttribute(k) {{ return this.attrs[k]; }},
        focus() {{ this.focused = true; }}, contains() {{ return false; }}, appendChild() {{}}
      }};
      return els[id];
    }}
    global.document = {{ getElementById: el, addEventListener() {{}}, removeEventListener() {{}},
      createElement: () => ({{ className: '', style: {{}}, classList: {{ add() {{}}, remove() {{}} }}, appendChild() {{}}, setAttribute() {{}} }}) }};
    global.window = {{ innerWidth: 375, addEventListener() {{}}, removeEventListener() {{}} }};
    global.marked = {{ parse: t => t }};
    {js}
    try {{
      const panel = el('dropdownTelemetria'); const btn = el('btnVerTelemetria');
      const r = {{}};
      r.inicial = panel.classList.contains('hidden');
      toggleAjustesMenu();
      r.abre = !panel.classList.contains('hidden'); r.expandido = btn.attrs['aria-expanded'];
      toggleAjustesMenu();
      r.cierraConClic = panel.classList.contains('hidden'); r.colapsado = btn.attrs['aria-expanded'];
      toggleAjustesMenu();
      manejarTeclaAjustes({{ key: 'a' }});
      r.otraTeclaNoCierra = !panel.classList.contains('hidden');
      manejarTeclaAjustes({{ key: 'Escape' }});
      r.escapeCierra = panel.classList.contains('hidden'); r.foco = btn.focused; r.expandidoFinal = btn.attrs['aria-expanded'];
      console.log(JSON.stringify({{ ok: true, r }}));
    }} catch (e) {{ console.log(JSON.stringify({{ ok: false, error: e.message + ' ' + e.stack }})); }}
    """
    out = _node(script)
    assert out["ok"], out.get("error")
    r = out["r"]
    assert r["inicial"] and r["abre"] and r["expandido"] == "true"
    assert r["cierraConClic"] and r["colapsado"] == "false"
    assert r["otraTeclaNoCierra"] and r["escapeCierra"] and r["foco"] and r["expandidoFinal"] == "false"


def test_app_js_registra_escape_y_clic_afuera_para_ajustes():
    js = APP_JS.read_text(encoding="utf-8")
    assert "addEventListener('keydown', manejarTeclaAjustes)" in js
    assert "contenedorSelectorModelo" in js


def test_literales_de_texto_de_los_js_sin_terminos_viejos():
    """Ninguna cadena de app.js / radar.js (textos que ve la persona) conserva la jerga removida."""
    BS = chr(92)
    # cadenas JS: '...', "..." y `...` con escapes
    cuerpo = lambda q, nl: q + '(?:' + BS + BS + '.|[^' + q + BS + BS + nl + '])*' + q
    literal = re.compile('|'.join([cuerpo("'", BS + 'n'), cuerpo('"', BS + 'n'), cuerpo('`', '')]))
    viejos = re.compile(r"XYZ|\bUmbral\b|\bToolkit\b|(?i:re-?analiz)|Ag[ée]ntic|(?i:pipeline)")
    for ruta in (APP_JS, RADAR_JS):
        texto = ruta.read_text(encoding="utf-8")
        for m in literal.finditer(texto):
            hallado = viejos.search(m.group(0))
            assert not hallado, f"{ruta.name}: «{hallado.group(0)}» en {m.group(0)[:80]}"


def test_ajustes_sin_jerga_binario_local_ni_direct_cli():
    """El menu Ajustes dice «Conectado» y «Sesion en esta PC», sin «Binario Local» ni «Direct CLI»."""
    html = INDEX.read_text(encoding="utf-8")
    js = APP_JS.read_text(encoding="utf-8")
    for ruta, texto in (("index.html", html), ("app.js", js), ("radar.js", RADAR_JS.read_text(encoding="utf-8"))):
        assert "Binario Local" not in texto, ruta
        assert "Direct CLI" not in texto, ruta
        assert "Fallback activo" not in texto, ruta
    estado = _soup().find(id="telemetriaCliEstado")
    assert estado is not None and estado.get_text(strip=True) == "Conectado"
    assert "'Conectado' : 'No detectado (modo alternativo)'" in js
    assert "|| 'Sesión en esta PC'" in js
