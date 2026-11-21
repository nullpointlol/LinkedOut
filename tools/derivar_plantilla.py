"""Deriva una plantilla de CV sin datos personales y con el mismo formato que la original.

    python tools/derivar_plantilla.py <entrada.docx> <salida.docx>

El generador (core/docx_generator.py) abre la plantilla, borra sus párrafos y conserva todo lo demás. Este script
copia el docx byte a byte salvo cuatro partes:
- word/document.xml: el cuerpo queda con un único párrafo (el nombre de ejemplo, estilo Title) y el sectPr intacto;
- word/_rels/document.xml.rels: sin relaciones de hipervínculo (mail, LinkedIn);
- docProps/core.xml: metadatos vacíos (autor, último en modificar, título...) y fechas fijas;
- docProps/app.xml: sin el título del documento (TitlesOfParts, HeadingPairs), empresa ni responsable, y con las
  estadísticas (palabras, caracteres, tiempo de edición...) en 0.
"""
import re
import sys
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

DOCUMENTO = "word/document.xml"
RELACIONES = "word/_rels/document.xml.rels"
METADATOS = "docProps/core.xml"
PROPIEDADES_APP = "docProps/app.xml"
ESTADISTICAS_APP = ("TotalTime", "Words", "Characters", "Lines", "Paragraphs", "CharactersWithSpaces")
DATOS_APP = ("Manager", "Company", "HyperlinkBase")
ESTILOS = "word/styles.xml"
REL_HIPERVINCULO = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"
FECHA_FIJA = "2026-01-01T00:00:00Z"

CORE_VACIO = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
    '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
    'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
    'xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
    "<dc:title></dc:title><dc:subject></dc:subject><dc:creator></dc:creator><cp:keywords></cp:keywords>"
    "<dc:description></dc:description><cp:lastModifiedBy></cp:lastModifiedBy><cp:revision>1</cp:revision>"
    f'<dcterms:created xsi:type="dcterms:W3CDTF">{FECHA_FIJA}</dcterms:created>'
    f'<dcterms:modified xsi:type="dcterms:W3CDTF">{FECHA_FIJA}</dcterms:modified>'
    "</cp:coreProperties>"
)


def _id_estilo_title(estilos: str) -> str:
    """styleId del estilo llamado «Title» (en un Word en castellano el id puede no ser «Title»)."""
    for m in re.finditer(r"<w:style\b[^>]*>.*?</w:style>", estilos, re.S):
        bloque = m.group(0)
        if re.search(r'<w:name w:val="Title"\s*/>', bloque):
            id_ = re.search(r'w:styleId="([^"]+)"', bloque)
            if id_:
                return id_.group(1)
    return "Title"


def _cuerpo(documento: str, nombre: str, id_title: str) -> str:
    inicio = documento.index("<w:body>") + len("<w:body>")
    fin = documento.rindex("<w:sectPr")
    parrafo = (f'<w:p><w:pPr><w:pStyle w:val="{id_title}"/></w:pPr>'
               f"<w:r><w:t>{escape(nombre)}</w:t></w:r></w:p>")
    return documento[:inicio] + parrafo + documento[fin:]


def _sin_hipervinculos(relaciones: str) -> str:
    return re.sub(r'<Relationship\b[^>]*Type="' + re.escape(REL_HIPERVINCULO) + r'"[^>]*/>', "", relaciones)


def _app_vacio(app: str) -> str:
    app = re.sub(r"<(HeadingPairs|TitlesOfParts)>.*?</\1>", "", app, flags=re.S)
    for etiqueta in ESTADISTICAS_APP:
        app = re.sub(rf"<{etiqueta}>[^<]*</{etiqueta}>", f"<{etiqueta}>0</{etiqueta}>", app)
    for etiqueta in DATOS_APP:
        app = re.sub(rf"<{etiqueta}>[^<]*</{etiqueta}>", f"<{etiqueta}></{etiqueta}>", app)
    return app


def derivar(entrada: Path, salida: Path, nombre: str = "Sam Ortega") -> None:
    entrada, salida = Path(entrada), Path(salida)
    with zipfile.ZipFile(entrada) as zin:
        estilos = zin.read(ESTILOS).decode("utf-8") if ESTILOS in zin.namelist() else ""
        nuevas = {
            DOCUMENTO: _cuerpo(zin.read(DOCUMENTO).decode("utf-8"), nombre, _id_estilo_title(estilos)).encode("utf-8"),
            METADATOS: CORE_VACIO.encode("utf-8"),
        }
        if PROPIEDADES_APP in zin.namelist():
            nuevas[PROPIEDADES_APP] = _app_vacio(zin.read(PROPIEDADES_APP).decode("utf-8")).encode("utf-8")
        if RELACIONES in zin.namelist():
            nuevas[RELACIONES] = _sin_hipervinculos(zin.read(RELACIONES).decode("utf-8")).encode("utf-8")
        salida.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(salida, "w") as zout:
            for info in zin.infolist():
                datos = nuevas.get(info.filename)
                zout.writestr(info, datos if datos is not None else zin.read(info.filename))


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        return 2
    derivar(Path(argv[0]), Path(argv[1]))
    print(f"Plantilla derivada: {argv[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
