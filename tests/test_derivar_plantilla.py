"""tools/derivar_plantilla.py: deja una plantilla docx sin datos y con el mismo formato.

Contrato:
    derivar(entrada: Path, salida: Path, nombre: str = "Alex Rivera") -> None
    CLI: python tools/derivar_plantilla.py <entrada.docx> <salida.docx>
    - Copia byte a byte todas las partes del zip salvo word/document.xml, word/_rels/document.xml.rels
      docProps/core.xml y docProps/app.xml.
    - document.xml: el cuerpo queda con un solo párrafo `nombre` con estilo Title y el sectPr original intacto.
    - document.xml.rels: sin relaciones de hipervínculo.
    - core.xml: creator, lastModifiedBy, title, subject, keywords y description vacíos; fechas fijas; revision 1.
    - app.xml: sin título del documento (TitlesOfParts, HeadingPairs), empresa ni responsable; estadísticas en 0.
"""
import importlib.util
import subprocess
import sys
import zipfile
from pathlib import Path

import docx
import pytest

RAIZ = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("derivar_plantilla", RAIZ / "tools" / "derivar_plantilla.py")
derivar_plantilla = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(derivar_plantilla)

REL_HIPERVINCULO = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"
CAMBIAN = {"word/document.xml", "word/_rels/document.xml.rels", "docProps/core.xml", "docProps/app.xml"}
APP_CON_DATOS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n<Properties xmlns="http://schemas.openxmlformats.org/'
    'officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/'
    'docPropsVTypes"><Template>Normal.dotm</Template><TotalTime>21</TotalTime><Pages>1</Pages><Words>469</Words>'
    '<Characters>2583</Characters><Application>Microsoft Office Word</Application><DocSecurity>0</DocSecurity>'
    '<Lines>21</Lines><Paragraphs>6</Paragraphs><ScaleCrop>false</ScaleCrop><HeadingPairs><vt:vector size="2" '
    'baseType="variant"><vt:variant><vt:lpstr>Title</vt:lpstr></vt:variant><vt:variant><vt:i4>1</vt:i4></vt:variant>'
    '</vt:vector></HeadingPairs><TitlesOfParts><vt:vector size="1" baseType="lpstr"><vt:lpstr>CV Persona Sintética'
    '</vt:lpstr></vt:vector></TitlesOfParts><Manager>Jefa Sintética</Manager><Company>Empresa Sintética</Company>'
    '<LinksUpToDate>false</LinksUpToDate><CharactersWithSpaces>3046</CharactersWithSpaces><SharedDoc>false</SharedDoc>'
    '<HyperlinkBase></HyperlinkBase><HyperlinksChanged>false</HyperlinksChanged><AppVersion>16.0000</AppVersion>'
    '</Properties>')


def _docx_con_datos(ruta: Path) -> Path:
    """Docx sintético con lo que tiene un CV real: párrafos, un hipervínculo y metadatos con nombre."""
    d = docx.Document()
    d.add_paragraph("Persona Sintética", style="Title")
    d.add_paragraph("Puesto de prueba", style="Subtitle")
    d.add_heading("EXPERIENCIA", level=1)
    d.add_paragraph("Empresa Sintética | 2001 a 2002")
    p = d.add_paragraph("contacto ")
    rid = p.part.relate_to("mailto:persona@example.test", REL_HIPERVINCULO, is_external=True)
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    h = OxmlElement("w:hyperlink")
    h.set(qn("r:id"), rid)
    r = OxmlElement("w:r")
    t = OxmlElement("w:t")
    t.text = "persona@example.test"
    r.append(t)
    h.append(r)
    p._p.append(h)
    cp = d.core_properties
    cp.author = "Persona Sintética"
    cp.last_modified_by = "Persona Sintética"
    cp.title = "CV Persona Sintética"
    cp.subject = "CV"
    cp.keywords = "clave"
    cp.comments = "comentario"
    d.save(str(ruta))
    _con_app_xml(ruta)
    return ruta


def _con_app_xml(ruta: Path) -> None:
    """Reescribe docProps/app.xml como lo deja Word: con el título del CV, la empresa y las estadísticas."""
    with zipfile.ZipFile(ruta) as z:
        partes = [(info, z.read(info.filename)) for info in z.infolist()]
    with zipfile.ZipFile(ruta, "w", zipfile.ZIP_DEFLATED) as z:
        for info, datos in partes:
            z.writestr(info, APP_CON_DATOS.encode("utf-8") if info.filename == "docProps/app.xml" else datos)
        if "docProps/app.xml" not in [i.filename for i, _ in partes]:
            z.writestr("docProps/app.xml", APP_CON_DATOS.encode("utf-8"))


@pytest.fixture
def par(tmp_path):
    entrada = _docx_con_datos(tmp_path / "entrada.docx")
    salida = tmp_path / "salida.docx"
    derivar_plantilla.derivar(entrada, salida)
    return entrada, salida


def test_partes_de_formato_identicas_byte_a_byte(par):
    entrada, salida = par
    za, zb = zipfile.ZipFile(entrada), zipfile.ZipFile(salida)
    assert sorted(za.namelist()) == sorted(zb.namelist())
    for nombre in za.namelist():
        if nombre not in CAMBIAN:
            assert za.read(nombre) == zb.read(nombre), nombre


def test_cuerpo_un_solo_parrafo_title_y_sectpr_intacto(par):
    entrada, salida = par
    d = docx.Document(str(salida))
    assert [(p.text, p.style.name) for p in d.paragraphs] == [("Alex Rivera", "Title")]
    xa = zipfile.ZipFile(entrada).read("word/document.xml").decode("utf-8")
    xb = zipfile.ZipFile(salida).read("word/document.xml").decode("utf-8")
    sect_a = xa[xa.index("<w:sectPr"):xa.index("</w:sectPr>")]
    sect_b = xb[xb.index("<w:sectPr"):xb.index("</w:sectPr>")]
    assert sect_a == sect_b
    assert xb.count("<w:p>") + xb.count("<w:p ") == 1
    assert "Persona Sintética" not in xb and "persona@example.test" not in xb


def test_sin_hipervinculos(par):
    entrada, salida = par
    antes = zipfile.ZipFile(entrada).read("word/_rels/document.xml.rels").decode("utf-8")
    despues = zipfile.ZipFile(salida).read("word/_rels/document.xml.rels").decode("utf-8")
    assert REL_HIPERVINCULO in antes
    assert REL_HIPERVINCULO not in despues
    assert "persona@example.test" not in despues
    # Las demás relaciones (estilos, numeración, tema...) quedan
    assert antes.count("<Relationship ") - 1 == despues.count("<Relationship ")


def test_metadatos_vacios(par):
    _, salida = par
    cp = docx.Document(str(salida)).core_properties
    assert cp.author == ""
    assert cp.last_modified_by == ""
    assert cp.title == ""
    assert cp.subject == ""
    assert cp.keywords == ""
    assert cp.comments == ""
    assert cp.revision == 1
    assert cp.created.year == 2026 and cp.created.month == 1 and cp.created.day == 1
    assert cp.modified.year == 2026 and cp.modified.month == 1 and cp.modified.day == 1
    core = zipfile.ZipFile(salida).read("docProps/core.xml").decode("utf-8")
    assert "Persona Sintética" not in core


def test_app_xml_sin_titulo_empresa_ni_estadisticas(par):
    _, salida = par
    app = zipfile.ZipFile(salida).read("docProps/app.xml").decode("utf-8")
    for dato in ["Persona Sintética", "Jefa Sintética", "Empresa Sintética", "469", "2583", "3046", "TitlesOfParts",
                 "HeadingPairs", "<TotalTime>21"]:
        assert dato not in app, dato
    assert "<Words>0</Words>" in app and "<Application>Microsoft Office Word</Application>" in app
    assert "<Template>Normal.dotm</Template>" in app


def test_la_plantilla_versionada_no_tiene_datos_en_app_xml():
    app = zipfile.ZipFile(RAIZ / "templates" / "cv_template_executive.docx").read("docProps/app.xml").decode("utf-8")
    assert "TitlesOfParts" not in app and "<Words>0</Words>" in app


def test_nombre_configurable(tmp_path):
    entrada = _docx_con_datos(tmp_path / "e.docx")
    salida = tmp_path / "s.docx"
    derivar_plantilla.derivar(entrada, salida, nombre="Otra Persona")
    assert [p.text for p in docx.Document(str(salida)).paragraphs] == ["Otra Persona"]


def test_idempotente(tmp_path, par):
    _, salida = par
    otra = tmp_path / "otra.docx"
    derivar_plantilla.derivar(salida, otra)
    za, zb = zipfile.ZipFile(salida), zipfile.ZipFile(otra)
    for nombre in za.namelist():
        assert za.read(nombre) == zb.read(nombre), nombre


def test_cli(tmp_path):
    entrada = _docx_con_datos(tmp_path / "e.docx")
    salida = tmp_path / "s.docx"
    r = subprocess.run([sys.executable, str(RAIZ / "tools" / "derivar_plantilla.py"), str(entrada), str(salida)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert salida.exists()
    assert [p.text for p in docx.Document(str(salida)).paragraphs] == ["Alex Rivera"]


def test_cli_sin_argumentos_falla():
    r = subprocess.run([sys.executable, str(RAIZ / "tools" / "derivar_plantilla.py")],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode != 0
