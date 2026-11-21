"""Regresiones de texto exportado; fixtures sintéticos, sin proveedores."""
import os
import importlib.util
import re
import zipfile
from html.parser import HTMLParser
from xml.etree import ElementTree as ET

import pytest
from docx import Document
from pypdf import PdfReader

from core import docx_generator
from core.pdf_generator import _compile_with_reportlab, render_cv_html
from core.prompts_evaluacion import ATS_CRITERIA


NAME = "ANA PRUEBA"
EMAIL = "ana@example.com"
URL = "https://linkedin.com/in/ana-prueba"
ROLE = "ANALISTA ORIGINAL"
CONTACT = f"Villa Test | {EMAIL} | {URL}"


def cv(role=ROLE, contact=CONTACT, prose="Trabajo técnico.", dates="2018-2025"):
    return (f"# {NAME}\n" + (f"{role}\n" if role else "") +
            f"{contact}\n\n## EXPERIENCIA\n### Analista | Empresa Test | {dates}\n"
            f"- {prose}\n")


class HtmlText(HTMLParser):
    def __init__(self, markup):
        super().__init__()
        self.text = []
        self.links = []
        self.feed(markup)

    def handle_data(self, data):
        self.text.append(data)

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.links.append(dict(attrs)["href"])


def pdf_text(path):
    reader = PdfReader(path)
    return reader, "\n".join(page.extract_text() for page in reader.pages)


def docx_content(path):
    doc = Document(str(path))
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        text = "\n".join("".join(p.itertext()) for p in root.findall(".//w:t", ns))
        rels = ET.fromstring(archive.read("word/_rels/document.xml.rels"))
        links = [r.attrib["Target"] for r in rels if r.attrib["Type"].endswith("/hyperlink")]
    return doc, text, links


@pytest.mark.parametrize("source,want", [
    ("Texto — inciso – final", "Texto - inciso - final"),
    ("a  —  b\t–\tc", "a  -  b\t-\tc"),
    ("2018–2025 y 01/2018 – 01/2025", "2018-2025 y 01/2018 - 01/2025"),
    ("2018–Presente y 01/2018–Actualidad", "2018-Presente y 01/2018-Actualidad"),
    ("[Ver — perfil](https://example.com/a–b_(x))", "[Ver - perfil](https://example.com/a–b_(x))"),
    ("https://example.com/a–b ana–qa@example.com ABC–123 `api—worker` — texto",
     "https://example.com/a–b ana–qa@example.com ABC–123 `api—worker` - texto"),
    ("# ANA–PRUEBA\nANALISTA — QA\nVilla — Test | ana@example.com\n## EXPERIENCIA\nTexto — final",
     "# ANA–PRUEBA\nANALISTA - QA\nVilla — Test | ana@example.com\n## EXPERIENCIA\nTexto - final"),
])
def test_idempotencia_y_tokens_protegidos(source, want, tmp_path):
    # invariante: identidad, contacto, encabezado y enlaces ASCII medidos en la ruta actual.
    markup = HtmlText(render_cv_html(cv()))
    visible = "\n".join(markup.text)
    assert NAME in visible and EMAIL in visible and "EXPERIENCIA" in visible
    assert URL in markup.links
    assert re.search(ATS_CRITERIA["date_format_regex"], "2018-2025")
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    assert importlib.util.find_spec("core.text_normalization") is not None, "Falta el normalizador compartido"
    from core.text_normalization import normalizar_guiones
    assert normalizar_guiones(source) == want
    assert normalizar_guiones(want) == want
    assert normalizar_guiones(CONTACT) == CONTACT
    assert normalizar_guiones("") == ""


@pytest.mark.parametrize("target_role", [ROLE, "OTRO TITULAR", None])
def test_reportlab_titular_unico_y_contacto(target_role, tmp_path):
    reader, text = pdf_text(_compile_with_reportlab(cv(), target_role, tmp_path / "cv.pdf"))
    # invariante
    assert NAME in text and EMAIL in text and "EXPERIENCIA" in text
    assert len(reader.pages) == 1
    assert float(reader.pages[0].mediabox.width) == pytest.approx(595.276, abs=.01)
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    lines = [line.strip() for line in text.splitlines()]
    assert lines.count(target_role or ROLE) == 1
    if target_role and target_role != ROLE:
        assert ROLE not in lines
    assert text.index(EMAIL) < text.index("EXPERIENCIA")
    html = render_cv_html(cv(), target_role)
    assert re.findall(r'<div class="target-role">(.*?)</div>', html) == [target_role or ROLE]
    path = docx_generator.compile_markdown_to_docx(cv(), target_role, str(tmp_path / "cv.docx"))
    paragraphs = [p.text for p in Document(str(path)).paragraphs]
    assert paragraphs.count(target_role or ROLE) == 1
    if target_role and target_role != ROLE:
        assert ROLE not in paragraphs


@pytest.mark.parametrize("contact", [CONTACT, EMAIL, URL, "+54 11 1234-5678"])
@pytest.mark.parametrize("target_role", [None, "OTRO TITULAR"])
def test_sin_titular_no_pierde_contacto(contact, target_role, tmp_path):
    baseline = cv(contact=contact)
    _, base_text = pdf_text(_compile_with_reportlab(baseline, None, tmp_path / "base.pdf"))
    # invariante: el contacto existe en la exportación de referencia con titular.
    assert contact in base_text
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    _, text = pdf_text(_compile_with_reportlab(cv(role=None, contact=contact), target_role, tmp_path / "cv.pdf"))
    assert contact in text
    markup = render_cv_html(cv(role=None, contact=contact), target_role)
    assert 'class="contact-line"' in markup
    assert f'class="target-role">{contact.upper()}' not in markup


@pytest.mark.parametrize("target_role", ["ANALISTA — QA", None])
@pytest.mark.parametrize("url", [URL, URL + "–perfil"])
def test_exportadores_sin_rayas_y_fechas_ats(target_role, url, tmp_path, monkeypatch):
    monkeypatch.setattr(docx_generator, "EXPORTS_DIR", tmp_path)
    contact = f"Villa Test | {EMAIL} | {url}"
    baseline = cv(contact=contact)
    path = docx_generator.compile_markdown_to_docx(baseline, output_filename="base.docx")
    doc, text, links = docx_content(path)
    html = HtmlText(render_cv_html(baseline))
    _, pdf = pdf_text(_compile_with_reportlab(baseline, None, tmp_path / "base.pdf"))
    # invariante: comparar los mismos campos protegidos en los tres formatos.
    for exported in (text, "\n".join(html.text), pdf):
        for token in (NAME, EMAIL, "EXPERIENCIA"):
            assert token in exported
    assert url in links and f"mailto:{EMAIL}" in links and url in html.links
    base_links = links
    base_html_links = html.links
    assert doc.paragraphs[0].text == NAME
    assert doc.sections[0].page_width.pt == pytest.approx(595.3, abs=.1)
    for dates in ("2018–2025", "01/2018 – 01/2025"):
        assert re.fullmatch(ATS_CRITERIA["date_format_regex"], dates)
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    markdown = cv(role="ANALISTA — QA", contact=contact, prose="Diseñé — con SQL – consultas.", dates="2018–2025")
    keywords = [{"nombre": "Datos — pruebas"}]
    html = render_cv_html(markdown, target_role, keywords=keywords)
    reader, pdf = pdf_text(_compile_with_reportlab(markdown, target_role, tmp_path / "cv.pdf", keywords=keywords))
    path = docx_generator.compile_markdown_to_docx(markdown, target_role, "cv.docx", keywords=keywords)
    doc, text, links = docx_content(path)
    for exported in (html, pdf, text, reader.metadata.title, doc.core_properties.title,
                     doc.core_properties.subject, doc.core_properties.keywords):
        # Q1: preservar el destino y su representación literal sin protocolo en HTML.
        exported = exported.replace(url, "").replace(url.removeprefix("https://"), "")
        assert "—" not in exported and "–" not in exported
    for exported in (html, pdf, text):
        assert "2018-2025" in exported
        assert "Diseñé - con SQL - consultas." in exported
        for token in (NAME, EMAIL, "EXPERIENCIA"):
            assert token in exported
    assert links == base_links
    assert HtmlText(html).links == base_html_links
    assert doc.paragraphs[0].text == NAME
    assert NAME in pdf.splitlines()
    assert re.search(ATS_CRITERIA["date_format_regex"], text)
