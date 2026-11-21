"""Unitarios de `core/docx_generator.py`: slug del rol, nombre del candidato, línea de contacto,
runs con negritas, metadatos con límite y compilación sin plantilla (todo en carpetas temporales)."""
from docx import Document
from docx.shared import Pt

from core import docx_generator as dg

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _textos_de_links(parrafo):
    return [t.text for h in parrafo._p.iter(f"{W}hyperlink") for t in h.iter(f"{W}t")]


def _destinos(parrafo):
    rels = parrafo.part.rels
    return [rels[h.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")].target_ref
            for h in parrafo._p.iter(f"{W}hyperlink")]


def test_slug_de_rol_largo_se_limita_a_tres_palabras():
    """Un rol con más de tres palabras significativas queda en tres (sin seniority)."""
    assert dg.summarize_role_slug("Senior Analista Funcional Datos Pagos") == "ANALISTA-FUNCIONAL-DATOS"


def test_nombre_cuando_personales_no_se_puede_leer(monkeypatch, tmp_path):
    """Si personales.txt existe pero no se puede abrir, el nombre es «Candidato»."""
    (tmp_path / "candidato" / "personales.txt").mkdir(parents=True)  # una carpeta: open() falla
    monkeypatch.setattr(dg, "DATA_DIR", tmp_path)
    assert dg.get_candidate_name("sin titulo") == "Candidato"


def test_borde_inferior_agrega_el_xml_con_color():
    """El borde divisor agrega `w:pBdr/w:bottom` con el color y grosor pedidos."""
    p = Document().add_paragraph("x")
    dg.add_bottom_border(p, color_hex="ABCDEF", sz="9")
    borde = p._p.pPr.find(f"{W}pBdr").find(f"{W}bottom")
    assert borde.get(f"{W}color") == "ABCDEF" and borde.get(f"{W}sz") == "9" and borde.get(f"{W}val") == "single"


def test_runs_con_fuente_minima_y_negritas():
    """Una fuente menor a 8,5 pt sube a 9,5; las negritas markdown se vuelven runs en negrita."""
    p = Document().add_paragraph()
    dg._add_formatted_runs(p, "**SQL** y Python", font_size=Pt(7))
    assert [(r.text, bool(r.bold)) for r in p.runs] == [("SQL", True), (" y Python", False)]
    assert all(r.font.size == Pt(9.5) for r in p.runs)


def test_contacto_con_links_sin_esquema():
    """LinkedIn y WhatsApp sin «https://» se completan; el teléfono sale del número de WhatsApp."""
    doc = Document()
    p = dg.render_contact_paragraph(doc, "Villa Test | wa.me/5491100000000 | linkedin.com/in/sam-ortega")
    assert _textos_de_links(p) == ["+5491100000000", "LinkedIn"]
    assert _destinos(p) == ["https://wa.me/5491100000000", "https://linkedin.com/in/sam-ortega"]
    assert p.runs[0].text == "Villa Test"


def test_contacto_sin_datos_reconocibles_queda_como_texto():
    """Una línea sin ningún dato reconocible se escribe tal cual."""
    p = dg.render_contact_paragraph(Document(), " | ")
    assert "".join(r.text for r in p.runs) == " | "


def test_metadatos_seguros():
    """Vacío da vacío; listas saltean ítems vacíos y, si el primero no entra, se corta; textos largos
    se cortan en un separador o en seco."""
    assert dg._safe_meta_string(None) == ""
    assert dg._safe_meta_string(["", "SQL", {"nombre": "Python"}]) == "SQL, Python"
    assert dg._safe_meta_string(["x" * 300], max_len=10) == "x" * 10
    assert dg._safe_meta_string("palabra " * 10, max_len=30) == "palabra palabra palabra"
    assert dg._safe_meta_string("y" * 40, max_len=30) == "y" * 30


def test_compilar_sin_plantilla_con_palabras_clave_en_texto(monkeypatch, tmp_path):
    """Sin plantilla se usa un documento en blanco; las palabras clave en texto van a los metadatos y
    las que faltan en el CV al apéndice oculto."""
    monkeypatch.setattr(dg, "TEMPLATE_PATH", tmp_path / "no_existe.docx")
    monkeypatch.setattr(dg, "EXPORTS_DIR", tmp_path / "exports")
    cv = "# Sam Ortega\n## PERFIL\nAnalista con SQL.\n"
    ruta = dg.compile_markdown_to_docx(cv, target_role="Analista", keywords="SQL, Kubernetes",
                                       output_filename="cv.docx")
    assert ruta == tmp_path / "exports" / "cv.docx"
    doc = Document(str(ruta))
    assert doc.core_properties.keywords == "SQL, Kubernetes"
    assert doc.paragraphs[-1].text == "Kubernetes"
