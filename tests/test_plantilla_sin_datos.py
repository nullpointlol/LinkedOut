"""La plantilla del CV no lleva datos de nadie y conserva el formato de siempre.

El generador (core/docx_generator.py) abre la plantilla, borra sus párrafos y conserva el resto: estilos,
numeración, tema, sectPr, relaciones y metadatos. Por eso la plantilla no puede traer hipervínculos ni nombres.
"""
import hashlib
import zipfile
from pathlib import Path

import docx

PLANTILLA = Path(__file__).resolve().parent.parent / "templates" / "cv_template_executive.docx"
REL_HIPERVINCULO = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"

# invariante: sha256 de las partes de formato, medidos el 12/11/2026 sobre la plantilla original
FORMATO = {
    "word/styles.xml": "f60094007deee283da3a0ec2a37b615878a8ea90e17af786af1d6e7142285281",
    "word/numbering.xml": "8adf9e9bd8ac90c41b1bdea3dad7f38b247a0ea194408bd1fbc3ddb7b7ad8969",
    "word/theme/theme1.xml": "b2295d3198893d2c03f5e584c749a15751b798aefdcd9bee2889f13903d68cb2",
    "word/settings.xml": "0d9cc4e62821d61364917505d37c72f06b0c6d3d7f07eef29b51a15a35c1377c",
    "word/fontTable.xml": "8347ec3f1e125775e039d64c2c597b3435ef991c6bd9984da27ccccb9e677f51",
}


def test_formato_identico_al_original():
    # invariante
    z = zipfile.ZipFile(PLANTILLA)
    for parte, sha in FORMATO.items():
        assert hashlib.sha256(z.read(parte)).hexdigest() == sha, parte


def test_misma_seccion_a4():
    # invariante: A4 y márgenes de la plantilla original
    x = zipfile.ZipFile(PLANTILLA).read("word/document.xml").decode("utf-8")
    assert '<w:pgSz w:w="11906" w:h="16838"/>' in x
    assert '<w:pgMar w:top="1440" w:right="1080" w:bottom="1440" w:left="1080" w:header="720" w:footer="720" w:gutter="0"/>' in x


def test_sin_hipervinculos():
    rels = zipfile.ZipFile(PLANTILLA).read("word/_rels/document.xml.rels").decode("utf-8")
    assert REL_HIPERVINCULO not in rels
    assert "mailto:" not in rels and "linkedin" not in rels.lower()


def test_metadatos_sin_nombre():
    cp = docx.Document(str(PLANTILLA)).core_properties
    assert cp.author == ""
    assert cp.last_modified_by == ""
    assert cp.title == ""
    assert cp.subject == ""
    assert cp.keywords == ""


def test_cuerpo_solo_con_el_candidato_de_ejemplo():
    d = docx.Document(str(PLANTILLA))
    assert [(p.text, p.style.name) for p in d.paragraphs] == [("Alex Rivera", "Title")]
    assert len(d.tables) == 0


def test_estilos_que_usa_el_generador():
    # invariante: 164 estilos, incluidos los que usa el generador
    d = docx.Document(str(PLANTILLA))
    nombres = {s.name for s in d.styles}
    assert len(d.styles) == 164
    assert {"Title", "Subtitle", "Heading 1", "Heading 2", "Normal"} <= nombres
