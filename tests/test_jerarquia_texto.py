"""UX: tres niveles de texto con contraste AA sobre los fondos oscuros del portal.

- Principal: zinc-100 / zinc-200 (títulos, valores).
- Secundario (lectura): zinc-300: texto corrido, descripciones, mensajes de «sin datos».
- Terciario (meta): zinc-400: fechas, contadores, etiquetas, ayudas.

Ojo: los tests de contraste solo verifican las constantes de color de este archivo (que zinc-100 a zinc-400
cumplen AA sobre los fondos del portal); no leen el HTML. La guardia real contra texto apagado es
TEXTO_APAGADO en test_legibilidad.py.
"""
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

INDEX = Path("static/index.html")
APP_JS = Path("static/app.js")
RADAR_JS = Path("static/radar.js")

ZINC = {"100": "#f4f4f5", "200": "#e4e4e7", "300": "#d4d4d8", "400": "#a1a1aa"}
# Fondos sobre los que se apoya el texto en el portal (index.html / radar.js / app.js)
FONDOS = ["#0f1115", "#121418", "#16181d", "#1a1d23", "#1e2026", "#262930"]


def _luminancia(hex_color: str) -> float:
    canales = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lineales = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in canales]
    return 0.2126 * lineales[0] + 0.7152 * lineales[1] + 0.0722 * lineales[2]


def _contraste(a: str, b: str) -> float:
    la, lb = sorted((_luminancia(a), _luminancia(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


@pytest.mark.parametrize("nivel", ["100", "200", "300", "400"])
@pytest.mark.parametrize("fondo", FONDOS)
def test_los_tres_niveles_cumplen_contraste_aa(nivel, fondo):
    assert _contraste(ZINC[nivel], fondo) >= 4.5, f"zinc-{nivel} sobre {fondo}"


def test_la_lectura_es_mas_clara_que_la_meta():
    for fondo in FONDOS:
        assert _contraste(ZINC["300"], fondo) > _contraste(ZINC["400"], fondo)


def _clases_por_id(soup, id_):
    el = soup.find(id=id_)
    assert el is not None, id_
    return el.get("class") or []


def _token_de(clases):
    """Token de color (--text-…) de la primera clase de radar.css que lo define, o None."""
    import re
    css = (INDEX.parent / "css" / "radar.css").read_text(encoding="utf-8")
    for clase in clases:
        m = re.search(r"\." + re.escape(clase) + r" \{([^}]*)\}", css)
        if m:
            t = re.search(r"color: var\(--(text-[a-z]+)\)", m.group(1))
            if t:
                return t.group(1)
    return None


def test_index_texto_de_lectura_en_zinc_300_y_meta_en_zinc_400():
    soup = BeautifulSoup(INDEX.read_text(encoding="utf-8"), "html.parser")
    # Rediseño E2: lo migrado usa clases de radar.css con el token equivalente (zinc-300 → --text-secondary,
    # zinc-400 → --text-muted); lo que todavía no se migró sigue con las utilidades de Tailwind
    for id_ in ("radarListaVacia", "radarListaFeedVacia", "radarEmailEstado", "radarEmailVacio"):  # pitchChannelDesc: la pestaña Pitch se quitó con el CV desde el maestro (14/11)
        clases = _clases_por_id(soup, id_)
        if _token_de(clases) == "text-secondary":
            continue
        assert "text-zinc-300" in clases and "text-zinc-400" not in clases, id_
    for id_ in ("fechaPostulacionDisplay", "radarGuardado", "radarPanelBusquedaCuenta"):
        clases = _clases_por_id(soup, id_)
        if _token_de(clases) == "text-muted":
            continue
        assert "text-zinc-400" in clases and "text-zinc-300" not in clases, id_
    # Rediseño E2: la franja de la cola es metadato con el token de texto secundario (radar.css)
    assert "cola" in _clases_por_id(soup, "radarCola")
    radar_css = (INDEX.parent / "css" / "radar.css").read_text(encoding="utf-8")
    regla = radar_css[radar_css.index(".cola {"):]
    assert "color: var(--text-secondary)" in regla[:regla.index("}")]


def test_index_descripciones_y_ayudas():
    texto = INDEX.read_text(encoding="utf-8")
    # Descripciones y texto corrido: zinc-300
    for frase in ("Se envían de a uno, con pausas",):
        linea = _linea_con(texto, frase) or _linea_con(texto, frase.replace("&amp;", "&"))
        assert linea, frase
        assert "text-zinc-300" in linea and "text-zinc-400" not in linea, frase
    # Ayudas breves: siguen en zinc-400
    assert "text-xs text-zinc-400\">Un clic para elegir" in texto


def _linea_con(texto: str, frase: str):
    """La línea con la frase o, si el texto va debajo de la etiqueta de apertura, la línea anterior."""
    lineas = texto.splitlines()
    for i, linea in enumerate(lineas):
        if frase in linea:
            return linea if "class=" in linea else lineas[i - 1]
    return None


def test_js_mensajes_sin_datos_y_descripciones_en_zinc_300():
    app = APP_JS.read_text(encoding="utf-8")
    radar = RADAR_JS.read_text(encoding="utf-8")
    for texto, frases in (
        (app, ("Sin descripción disponible.", "No se detectaron dolores específicos.", "No hay requerimientos clasificados.",
               "Seleccioná una postulación para visualizar la matriz", "Requerimientos sin match directo",
               "No se extrajo matriz estructurada")),
        (radar, ("Sigue en segundo plano", "Elegí un post de la lista para ver su detalle.")),
    ):
        for frase in frases:
            linea = _linea_con(texto, frase)
            assert linea, frase
            if texto is radar and _clase_con_token_secundario(linea):
                continue  # rediseño E2: clase propia con el token de texto de lectura (radar.css)
            assert "text-zinc-300" in linea and "text-zinc-400" not in linea, frase


def _clase_con_token_secundario(linea):
    """La línea usa una clase de radar.css cuyo color es --text-secondary (el equivalente de zinc-300)."""
    import re
    css = (RADAR_JS.parent / "css" / "radar.css").read_text(encoding="utf-8")
    for clase in re.findall(r'class="([a-z][\w-]*)', linea):
        m = re.search(r"\." + re.escape(clase) + r" \{([^}]*)\}", css)
        if m and "color: var(--text-secondary)" in m.group(1):
            return True
    return False


def test_js_metadatos_siguen_en_zinc_400():
    app = APP_JS.read_text(encoding="utf-8")
    radar = RADAR_JS.read_text(encoding="utf-8")
    assert "text-zinc-400 font-mono text-xs ml-1 flex-shrink-0\">${fechaStr}" in app
    # Rediseño E2: metadato del autor con .det-meta, que usa el token de texto apagado (radar.css)
    assert 'class="det-meta">${escapeHtml(p.cargo_autor' in radar
    css = (RADAR_JS.parent / "css" / "radar.css").read_text(encoding="utf-8")
    regla = css[css.index(".det-meta {"):]
    assert "color: var(--text-muted)" in regla[:regla.index("}")]
