"""Búsqueda del navegador Chromium con el que `core/pdf_generator.py` imprime el CV a PDF.

Vivía en `core/desktop.py` (ventana de escritorio, retirada); el PDF es su único uso."""
import pytest

from core import pdf_generator


@pytest.fixture
def sin_navegadores_instalados(monkeypatch):
    """Sin variables de Program Files: solo cuenta lo que diga `shutil.which`."""
    for var in ("ProgramFiles(x86)", "ProgramFiles", "LocalAppData"):
        monkeypatch.delenv(var, raising=False)


def test_prefiere_edge_instalado(monkeypatch, tmp_path, sin_navegadores_instalados):
    edge = tmp_path / "Microsoft" / "Edge" / "Application" / "msedge.exe"
    edge.parent.mkdir(parents=True)
    edge.write_text("", encoding="utf-8")
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    monkeypatch.setattr(pdf_generator.shutil, "which", lambda n: "C:/bin/chrome.exe")
    assert pdf_generator.find_browser_executable() == str(edge)


def test_navegador_desde_el_path(monkeypatch, sin_navegadores_instalados):
    """Sin instalaciones estándar, se usa el primer navegador que esté en el PATH."""
    monkeypatch.setattr(pdf_generator.shutil, "which", lambda n: "C:/bin/chrome.exe" if n == "chrome" else None)
    assert pdf_generator.find_browser_executable() == "C:/bin/chrome.exe"


def test_sin_navegador_devuelve_none(monkeypatch, sin_navegadores_instalados):
    monkeypatch.setattr(pdf_generator.shutil, "which", lambda n: None)
    assert pdf_generator.find_browser_executable() is None
