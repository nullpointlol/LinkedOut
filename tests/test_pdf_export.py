import os
from pathlib import Path
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient

from core.database import create_postulacion, update_postulacion
from core.pdf_generator import (
    render_cv_html,
    compile_markdown_to_pdf,
    generate_cv_pdf_filename,
)
from server import app

SAMPLE_CV = """# ALEX RIVERA
SENIOR TECHNICAL BUSINESS ANALYST
San Isidro, Buenos Aires, Argentina | +54 9 11 5555-0100 | alex.rivera@example.com | https://linkedin.com/in/alex-rivera

## PERFIL
Analista Técnico Funcional con 7 años de experiencia optimizando procesos y arquitecturas de integración.

## EXPERIENCIA
### Analista Técnico Funcional | Freelance | 2025 - 2026
• Redujo tiempos de procesamiento en 40% mediante automatización en Python.
• Elevó la trazabilidad de operaciones al 100%.

### Analista Técnico y Soporte | VELORA SISTEMAS | 2018 - 2025
• Centralizó la observabilidad en más de 90 aplicaciones.

## HABILIDADES TÉCNICAS
• Python, SQL, Docker, APIs REST/SOAP, Linux.

## EDUCACIÓN
• Tecnicatura en Informática - 2016

## IDIOMAS
• Inglés avanzado, Español nativo
"""


def test_render_cv_html_contains_a4_geometry_and_corporate_styling():
    html = render_cv_html(SAMPLE_CV, target_role="Senior Technical Business Analyst")
    assert isinstance(html, str)
    # Geometría A4 estándar ISO 216
    assert "@page" in html
    assert "size: A4" in html or "size: a4" in html
    assert "margin:" in html
    # Paleta corporativa Navy y Slate
    assert "#1A365D" in html or "#1a365d" in html
    # Font stack sans-serif
    assert "font-family" in html
    assert "sans-serif" in html
    # Regla anti-rotura de página en experiencia
    assert "page-break-inside: avoid" in html or "break-inside: avoid" in html
    # Contenido renderizado
    assert "ALEX RIVERA" in html
    assert "VELORA SISTEMAS" in html
    assert "PERFIL" in html


def test_generate_cv_pdf_filename():
    from core.docx_generator import get_candidate_name, clean_text
    expected_name = clean_text(get_candidate_name())
    filename = generate_cv_pdf_filename("Senior Technical Business Analyst")
    assert filename.startswith(f"CV - {expected_name} - ")
    assert filename.endswith(".pdf")
    assert "TECHNICAL-BUSINESS-ANALYST" in filename


def test_compile_markdown_to_pdf_produces_file_in_exports():
    out_name = "test_export_cv.pdf"
    pdf_path = compile_markdown_to_pdf(
        SAMPLE_CV,
        target_role="Senior Technical Business Analyst",
        output_filename=out_name
    )
    try:
        assert isinstance(pdf_path, Path)
        assert pdf_path.exists()
        assert pdf_path.is_file()
        assert pdf_path.name == out_name
        assert pdf_path.parent.name == "exports"
        assert pdf_path.stat().st_size > 0
    finally:
        if pdf_path.exists():
            pdf_path.unlink()


def test_compile_markdown_to_pdf_headless_fallback_reportlab():
    # Simula ausencia de navegador para forzar fallback (reportlab)
    with patch("core.pdf_generator.find_browser_executable", return_value=None):
        out_name = "test_fallback_cv.pdf"
        pdf_path = compile_markdown_to_pdf(
            SAMPLE_CV,
            target_role="Data Engineer",
            output_filename=out_name
        )
        try:
            assert isinstance(pdf_path, Path)
            assert pdf_path.exists()
            assert pdf_path.stat().st_size > 0
        finally:
            if pdf_path.exists():
                pdf_path.unlink()


def test_api_export_pdf_success():
    client = TestClient(app)
    postulacion_id = create_postulacion(
        titulo="Ingeniero de Software Fullstack",
        empresa="Empresa Test PDF",
        contenido="Requisitos para software engineer.",
        url="https://example.com/job/pdf-test"
    )
    update_postulacion(
        postulacion_id=postulacion_id,
        cv_markdown=SAMPLE_CV,
        pitch="Pitch de prueba."
    )

    response = client.get(f"/api/ofertas/{postulacion_id}/export/pdf")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert "attachment" in response.headers.get("content-disposition", "")
    assert "CV - Alex Rivera -" in response.headers.get("content-disposition", "")
    assert len(response.content) > 0


def test_api_export_pdf_404_when_missing():
    client = TestClient(app)
    # Oferta inexistente
    response = client.get("/api/ofertas/999999/export/pdf")
    assert response.status_code == 404

    # Oferta sin cv_markdown
    postulacion_id = create_postulacion(
        titulo="Oferta Sin CV",
        empresa="Empresa Test 404",
        contenido="Sin markdown asignado",
        url="https://example.com/job/sin-cv"
    )
    response2 = client.get(f"/api/ofertas/{postulacion_id}/export/pdf")
    assert response2.status_code == 404


def test_ui_contains_btn_descargar_pdf_and_js_function():
    # Verifica index.html
    index_path = Path("static/index.html")
    index_html = index_path.read_text(encoding="utf-8")
    assert 'id="btnDescargarPdf"' in index_html
    assert "descargarPdf()" in index_html
    assert 'id="cv-actions"' in index_html

    # Verifica app.js
    app_js_path = Path("static/app.js")
    app_js = app_js_path.read_text(encoding="utf-8")
    assert "function descargarPdf(" in app_js
    assert "/export/pdf" in app_js


def test_cv_contact_phone_links_to_whatsapp_without_a_separate_link():
    from core.pdf_generator import render_cv_html
    md = ("# ANA TEST\nANALISTA\nSan Isidro | +54 9 11 5555-0100 | https://wa.me/5491155550100 | "
          "ana@test.test | https://www.linkedin.com/in/ana-test/\n\n## PERFIL\nAnalista.\n")
    h = render_cv_html(md)
    assert '<a href="https://wa.me/5491155550100" target="_blank" rel="noopener noreferrer">+54 9 11 5555-0100</a>' in h
    assert h.count("wa.me/5491155550100") == 1


def test_cv_contact_bare_linkedin_url_is_clickable():
    from core.pdf_generator import _contacto_con_whatsapp
    assert _contacto_con_whatsapp("Ana | linkedin.com/in/ana-test") == "Ana | [linkedin.com/in/ana-test](https://linkedin.com/in/ana-test)"


def test_puesto_del_cv_reads_the_line_under_the_name():
    from core.pdf_generator import puesto_del_cv
    assert puesto_del_cv(SAMPLE_CV) == "SENIOR TECHNICAL BUSINESS ANALYST"
    assert puesto_del_cv("# ANA\nana@x.test | +54 11\n## PERFIL\nTexto") == ""
    assert puesto_del_cv("") == ""


def test_export_header_and_filename_use_the_cv_role_not_the_offer_title():
    client = TestClient(app)
    pid = create_postulacion(titulo="Convocatoria Abierta de Talentos / Base de Postulantes IT y Multiárea",
                             empresa="Acme", contenido="Búsqueda abierta.", url="https://example.com/job/rol")
    update_postulacion(postulacion_id=pid, cv_markdown=SAMPLE_CV, pitch="Pitch.")
    capturado = {}

    def fake_compile(markdown_text, target_role=None, output_filename=None, keywords=None):
        capturado["rol"], capturado["archivo"] = target_role, output_filename
        destino = Path(os.environ.get("TMP", ".")) / "cv_rol_test.pdf"
        destino.write_bytes(b"%PDF-1.4")
        return destino

    with patch("server.compile_markdown_to_pdf", side_effect=fake_compile):
        r = client.get(f"/api/ofertas/{pid}/export/pdf")
    assert r.status_code == 200
    assert capturado["rol"] == "SENIOR TECHNICAL BUSINESS ANALYST"
    assert "CONVOCATORIA" not in capturado["archivo"].upper()


def test_edge_que_devuelve_antes_de_escribir_no_cae_a_reportlab(tmp_path, monkeypatch):
    """El lanzador de Edge devuelve 0 antes de escribir el PDF: se espera el archivo en vez de caer a ReportLab
    (después Edge pisaba el PDF de ReportLab con el suyo)."""
    import threading
    import core.pdf_generator as pg

    monkeypatch.setattr(pg, "EXPORTS_DIR", tmp_path)
    monkeypatch.setattr(pg, "find_browser_executable", lambda: "edge.exe")
    destino = tmp_path / "cv_edge.pdf"

    def lanzador(cmd, **kw):
        threading.Timer(0.4, lambda: destino.write_bytes(b"%PDF-1.4 edge")).start()
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr(pg.subprocess, "run", lanzador)
    monkeypatch.setattr(pg, "_compile_with_reportlab", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ReportLab")))
    assert pg.compile_markdown_to_pdf("# A\nB\n\n## PERFIL\nx", output_filename="cv_edge.pdf") == destino
    assert destino.read_bytes() == b"%PDF-1.4 edge"
