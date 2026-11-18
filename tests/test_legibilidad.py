"""Tarea 8 (UX): lectura y accesibilidad: tamaño mínimo 12 px, contraste y botones solo-ícono con nombre."""
import re
from pathlib import Path

from bs4 import BeautifulSoup

ARCHIVOS = [Path("static/index.html"), Path("static/radar.js"), Path("static/app.js")]
# Prefijos de variante opcionales (hover:, sm:, group-hover:, ...) delante de la clase.
TEXTO_CHICO = re.compile(r"text-\[(?:[0-9]|10)px\]")
TEXTO_APAGADO = re.compile(r"(?<![\w\[-])(?:[a-z-]+:)*text-zinc-(?:500|600)(?![\w-])")


def test_no_quedan_tamanos_de_texto_menores_a_11px():
    """Ningún texto a 10 px o menos. Se exceptúan los íconos (`<i ...>`: spinners, fa-brain), que no llevan texto."""
    for ruta in ARCHIVOS:
        texto = ruta.read_text(encoding="utf-8")
        for m in TEXTO_CHICO.finditer(texto):
            etiqueta = texto[texto.rfind("<", 0, m.start()): m.start()]
            if etiqueta.startswith("<i "):
                continue
            raise AssertionError(f"{ruta.name}: texto de {m.group(0)} fuera de un ícono: {texto[max(0, m.start()-60): m.end()+60]!r}")


def test_sin_texto_gris_de_bajo_contraste():
    for ruta in ARCHIVOS:
        texto = ruta.read_text(encoding="utf-8")
        hallados = TEXTO_APAGADO.findall(texto)
        assert not hallados, f"{ruta.name}: {len(hallados)} usos de text-zinc-500/600 (usar zinc-400 o zinc-300)"


def test_textos_de_11px_solo_en_insignias_de_una_palabra():
    """Las únicas excepciones a 12 px son insignias decorativas de una sola palabra."""
    for ruta in ARCHIVOS:
        texto = ruta.read_text(encoding="utf-8")
        for m in re.finditer(r"text-\[11px\]", texto):
            # Contexto: el elemento que lleva la clase y su contenido hasta el siguiente cierre
            resto = texto[m.end(): m.end() + 400]
            cierre = re.search(r">([^<]*)</", resto)
            contenido = re.sub(r"\$\{[^}]*\}", "X", cierre.group(1)).strip() if cierre else ""
            assert cierre and contenido and len(contenido.split()) == 1, (
                f"{ruta.name}: text-[11px] fuera de una insignia de una palabra: {texto[max(0, m.start()-60): m.end()+80]!r}"
            )


def test_botones_de_index_sin_texto_tienen_aria_label():
    soup = BeautifulSoup(Path("static/index.html").read_text(encoding="utf-8"), "html.parser")
    for b in soup.find_all("button"):
        if not b.get_text(strip=True):
            assert b.get("aria-label"), f"Botón solo-ícono sin aria-label: {str(b)[:120]}"
            if b.get("title"):
                assert b["aria-label"] == b["title"], f"aria-label distinto de title: {str(b)[:120]}"


def test_botones_de_los_js_sin_texto_tienen_aria_label():
    for ruta in (Path("static/radar.js"), Path("static/app.js")):
        texto = ruta.read_text(encoding="utf-8")
        for m in re.finditer(r"<button\b([^>]*)>(.*?)</button>", texto, re.S):
            atributos, interno = m.groups()
            visible = re.sub(r"\$\{[^}]*\}", "X", re.sub(r"<[^>]*>", "", interno)).strip()
            assert visible or "aria-label" in atributos, f"{ruta.name}: botón sin texto ni aria-label: {m.group(0)[:100]}"


def test_boton_de_estado_de_requisito_no_parte_la_palabra():
    """«Transferible» + el ícono de «aprendido» a 12 px no caben en 74 px: ancho mayor y sin salto de línea."""
    js = Path("static/radar.js").read_text(encoding="utf-8")
    m = re.search(r'<button type="button" data-req="[^"]*" class="([^"]*)"', js)
    assert m, "No se encontró el botón de estado de requisito"
    clases = m.group(1).split()
    assert "whitespace-nowrap" in clases
    ancho = [int(x) for c in clases for x in re.findall(r"^w-\[(\d+)px\]$", c)]
    assert ancho and ancho[0] >= 90, f"Ancho insuficiente para «Transferible» + ícono: {ancho}"


def test_titulo_del_header_cabe_en_375px():
    """A 375 px el titulo «LinkedOut» tiene que verse entero: sin el icono decorativo y mas chico en celular."""
    soup = BeautifulSoup(Path("static/index.html").read_text(encoding="utf-8"), "html.parser")
    h1 = soup.find("h1")
    assert h1 is not None and h1.get_text(" ", strip=True).startswith("LinkedOut")
    # Rediseño E1: «.marca» de marco.css (15 px) con un ícono decorativo de 18 px; que entre a 360/375 sin
    # desbordar lo mide tests/test_rediseno_marco.py en un navegador
    assert "marca" in h1.get("class")
    assert h1.select_one('svg[aria-hidden="true"] use[href="/static/iconos.svg#briefcase"]') is not None
    marco = Path("static/css/marco.css").read_text(encoding="utf-8")
    assert re.search(r"\.marca \{[^}]*font-size: 15px", marco)
