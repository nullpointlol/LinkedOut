"""tools/comparar_docx.py: dice si dos docx tienen el mismo formato, sin mostrar su texto.

Contrato:
    comparar(a: Path, b: Path, permitidas: frozenset[str] = frozenset()) -> list[str]
        Lista de diferencias (vacía = mismo formato). Compara el sha256 de cada parte del zip (salvo las permitidas)
        y, con python-docx, por párrafo: estilo, alineación, sangrías, espaciado y keep_with_next; por run: fuente,
        tamaño, negrita, cursiva, subrayado y color; por sección: tamaño de página y márgenes; cantidad de tablas;
        encabezados y pies. Cada diferencia nombra la parte o la ubicación (párrafo N, run M), nunca el texto.
    CLI: python tools/comparar_docx.py a.docx b.docx [--permitidas p1,p2] -> sale con 0 si no hay diferencias, 1 si hay.
"""
import importlib.util
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import docx
from docx.shared import Pt
import pytest

RAIZ = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("comparar_docx", RAIZ / "tools" / "comparar_docx.py")
comparar_docx = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(comparar_docx)

SECRETO = "Texto Secreto Sintético"


def _doc(ruta: Path, tamano=11, negrita=False, estilo="Normal", margen=72, autor="") -> Path:
    d = docx.Document()
    d.add_paragraph("Título", style="Title")
    p = d.add_paragraph(style=estilo)
    run = p.add_run(SECRETO)
    run.font.size = Pt(tamano)
    run.bold = negrita
    d.sections[0].left_margin = Pt(margen)
    d.core_properties.author = autor
    d.save(str(ruta))
    return ruta


def test_iguales_sin_diferencias(tmp_path):
    a = _doc(tmp_path / "a.docx")
    b = tmp_path / "b.docx"
    shutil.copy(a, b)
    assert comparar_docx.comparar(a, b) == []


def test_tamano_de_fuente_distinto(tmp_path):
    a = _doc(tmp_path / "a.docx", tamano=11)
    b = _doc(tmp_path / "b.docx", tamano=12)
    difs = comparar_docx.comparar(a, b, permitidas=frozenset({"word/document.xml", "docProps/core.xml"}))
    assert any("tamaño" in d and "párrafo 1" in d for d in difs), difs


def test_negrita_distinta(tmp_path):
    a = _doc(tmp_path / "a.docx", negrita=False)
    b = _doc(tmp_path / "b.docx", negrita=True)
    difs = comparar_docx.comparar(a, b, permitidas=frozenset({"word/document.xml", "docProps/core.xml"}))
    assert any("negrita" in d for d in difs), difs


def test_estilo_de_parrafo_distinto(tmp_path):
    a = _doc(tmp_path / "a.docx", estilo="Normal")
    b = _doc(tmp_path / "b.docx", estilo="Subtitle")
    difs = comparar_docx.comparar(a, b, permitidas=frozenset({"word/document.xml", "docProps/core.xml"}))
    assert any("estilo" in d for d in difs), difs


def test_margen_distinto(tmp_path):
    a = _doc(tmp_path / "a.docx", margen=72)
    b = _doc(tmp_path / "b.docx", margen=54)
    difs = comparar_docx.comparar(a, b, permitidas=frozenset({"word/document.xml", "docProps/core.xml"}))
    assert any("margen" in d and "sección 0" in d for d in difs), difs


def test_parte_distinta_se_informa_si_no_esta_permitida(tmp_path):
    a = _doc(tmp_path / "a.docx", autor="")
    b = _doc(tmp_path / "b.docx", autor="Otra")
    assert any("docProps/core.xml" in d for d in comparar_docx.comparar(a, b))
    difs = comparar_docx.comparar(a, b, permitidas=frozenset({"docProps/core.xml"}))
    assert not any("docProps/core.xml" in d for d in difs), difs


def test_parte_que_falta(tmp_path):
    a = _doc(tmp_path / "a.docx")
    b = tmp_path / "b.docx"
    with zipfile.ZipFile(a) as za, zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as zb:
        for n in za.namelist():
            if n != "docProps/app.xml":
                zb.writestr(n, za.read(n))
    assert any("docProps/app.xml" in d for d in comparar_docx.comparar(a, b))


def test_no_muestra_el_texto(tmp_path):
    a = _doc(tmp_path / "a.docx", tamano=11)
    b = _doc(tmp_path / "b.docx", tamano=14, negrita=True, estilo="Subtitle", margen=50)
    difs = comparar_docx.comparar(a, b)
    assert difs
    assert all(SECRETO not in d for d in difs)


def test_cli_codigos_de_salida(tmp_path):
    a = _doc(tmp_path / "a.docx")
    b = tmp_path / "b.docx"
    shutil.copy(a, b)
    c = _doc(tmp_path / "c.docx", tamano=20)
    script = str(RAIZ / "tools" / "comparar_docx.py")
    r = subprocess.run([sys.executable, script, str(a), str(b)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    r = subprocess.run([sys.executable, script, str(a), str(c), "--permitidas", "word/document.xml,docProps/core.xml"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 1
    assert SECRETO not in r.stdout + r.stderr
