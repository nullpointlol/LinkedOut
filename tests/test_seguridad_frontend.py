"""Controles de seguridad del frontend (auditoría de vulnerabilidades, 17/11): trinquete.

Cada control tiene su lista de excepciones conocidas (docs/seguridad/auditoria-20261117.md; hoy solo Tailwind).
Lo ya existente pasa; algo nuevo con el mismo problema falla. Al arreglar un hallazgo, sacalo de la lista:
el test avisa si una excepción ya no hace falta, para que la lista nunca crezca en silencio.
"""
import re
from collections import Counter
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
STATIC = RAIZ / "static"

# Recursos de CDN (dominio externo) en <script src>, <link href> o URL_* de los .js
_RE_EXTERNO_HTML = re.compile(r"<(script|link)\b[^>]*?(?:src|href)=\"(https?://[^\"]+)\"[^>]*>", re.I)
_RE_EXTERNO_JS = re.compile(r"['\"](https://(?:cdn\.|cdnjs\.|unpkg\.)[^'\"]+\.(?:js|css))['\"]")
# Versión exacta en la URL: /1.4.1/, @18.2.1, /3.4.17
_RE_VERSION = re.compile(r"[@/]v?\d+\.\d+\.\d+(?:[/?#]|$)")

# M1/M2 arreglados el 18/11: las librerías van en static/vendor/. Tailwind queda por CDN hasta el rediseño
# (decisión del creador, 17/11), con versión fija y sin integrity: cdn.tailwindcss.com no manda CORS y con
# integrity el navegador bloquea el script. Es el único recurso externo permitido.
EXTERNOS_PERMITIDOS = {"https://cdn.tailwindcss.com/3.4.17"}
SIN_VERSION_CONOCIDOS = set()
SIN_INTEGRITY_CONOCIDOS = {"https://cdn.tailwindcss.com/3.4.17"}

# Hallazgo A1: un manejador inline (onclick="…${x}…") arma JavaScript desde texto. escapeHtml no
# sirve ahí: el navegador decodifica &#39; antes de ejecutar el atributo. Conteo por fragmento.
_RE_HANDLER_INTERPOLADO = re.compile(r"\bon[a-z]+=\"([^\"]*\$\{[^\"]*)\"")
HANDLERS_CONOCIDOS = Counter()  # A1 arreglado el 18/11: los chips usan data-ciclar-tag + addEventListener


def _recursos_externos():
    """{url: tiene_integrity} de index.html y de los .js que cargan scripts de CDN."""
    recursos = {}
    for m in _RE_EXTERNO_HTML.finditer((STATIC / "index.html").read_text(encoding="utf-8")):
        recursos[m.group(2)] = "integrity=" in m.group(0)
    for js in sorted(STATIC.glob("*.js")):
        texto = js.read_text(encoding="utf-8")
        for m in _RE_EXTERNO_JS.finditer(texto):
            # Carga dinámica: el integrity va en el elemento (s.integrity = '…')
            recursos[m.group(1)] = bool(re.search(r"\.integrity\s*=", texto))
    return recursos


def test_hay_recursos_externos_para_controlar():
    # Si el regex dejara de encontrar los CDN, los otros tests pasarían sin controlar nada
    assert _recursos_externos()


def test_solo_los_recursos_externos_permitidos():
    assert set(_recursos_externos()) <= EXTERNOS_PERMITIDOS, "Copiá la librería a static/vendor/ (VERSIONES.json)"


def test_las_librerias_locales_existen():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = "".join(f.read_text(encoding="utf-8") for f in STATIC.glob("*.js"))
    locales = re.findall(r"/static/(vendor/[^\"'?]+)", html + js)
    assert len(locales) >= 3
    for ruta in locales:
        assert (STATIC / ruta).is_file(), ruta


def test_cada_recurso_de_cdn_tiene_version_exacta():
    sin_version = {u for u in _recursos_externos() if not _RE_VERSION.search(u)}
    assert sin_version - SIN_VERSION_CONOCIDOS == set(), "CDN sin versión exacta (fijala en la URL)"
    assert SIN_VERSION_CONOCIDOS - sin_version == set(), "Ya tiene versión: sacala de SIN_VERSION_CONOCIDOS"


def test_cada_recurso_de_cdn_tiene_integrity():
    sin_sri = {u for u, sri in _recursos_externos().items() if not sri}
    assert sin_sri - SIN_INTEGRITY_CONOCIDOS == set(), "CDN sin integrity (SRI) ni crossorigin"
    assert SIN_INTEGRITY_CONOCIDOS - sin_sri == set(), "Ya tiene integrity: sacala de SIN_INTEGRITY_CONOCIDOS"


def test_ningun_manejador_inline_nuevo_con_interpolacion():
    encontrados = Counter()
    for archivo in sorted(STATIC.glob("*.js")) + [STATIC / "index.html"]:
        for m in _RE_HANDLER_INTERPOLADO.finditer(archivo.read_text(encoding="utf-8")):
            encontrados[m.group(1)] += 1
    nuevos = encontrados - HANDLERS_CONOCIDOS
    assert not nuevos, f"Manejador inline con datos (usá addEventListener): {dict(nuevos)}"
    arreglados = HANDLERS_CONOCIDOS - encontrados
    assert not arreglados, f"Ya no están: bajalos de HANDLERS_CONOCIDOS: {dict(arreglados)}"


def test_el_cv_se_renderiza_solo_con_markdown_seguro():
    """A2: `marked.parse` a secas deja pasar HTML; todo Markdown de la página pasa por `markdownSeguro`."""
    for js in sorted(STATIC.glob("*.js")):
        assert "marked.parse(" not in js.read_text(encoding="utf-8"), js.name
