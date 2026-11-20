"""Enlaces internos de la documentación: todo `.md` versionado apunta a archivos que existen.

Revisa los enlaces Markdown relativos (`[texto](ruta)`) y las rutas del repo citadas entre comillas invertidas
(`docs/x.md`, `tools/y.py`). Ignora URLs, anclas, rutas con comodines o marcadores (`<DATA_DIR>`, `*`, `AAAA`) y lo
que vive fuera del repo (`data/`, `~`).
"""
import re
import subprocess
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
# Carpetas del repo cuyas rutas citadas entre comillas invertidas se verifican
RAICES_DEL_REPO = ("docs/", "tools/", "core/", "tests/", "scripts/", "config/", "static/", "templates/")
ENLACE_MD = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
RUTA_CITADA = re.compile(r"`([A-Za-z0-9_./-]+\.(?:md|py|ps1|json|txt|sh|html|js|css))(?::\d+)?`")
MARCADORES = ("<", ">", "*", "AAAA", "{", "$")
# Estado de una rama: cita archivos por crear o ya borrados a propósito
NO_VIGENTES = re.compile(r"^ESTADO\.md$")


def _md_versionados():
    salida = subprocess.run(["git", "ls-files", "*.md"], cwd=RAIZ, capture_output=True, text=True, check=True)
    return [linea for linea in salida.stdout.splitlines() if linea and not NO_VIGENTES.match(linea)]


def _referencias(texto: str):
    for destino in ENLACE_MD.findall(texto):
        if re.match(r"^[a-z]+:", destino) or destino.startswith("#"):
            continue
        yield "enlace", destino.split("#")[0]
    for ruta in RUTA_CITADA.findall(texto):
        if ruta.startswith(RAICES_DEL_REPO):
            yield "cita", ruta


def _rotas(archivo: str):
    ruta_md = RAIZ / archivo
    rotas = []
    for tipo, destino in _referencias(ruta_md.read_text(encoding="utf-8")):
        if not destino or any(m in destino for m in MARCADORES):
            continue
        base = ruta_md.parent if tipo == "enlace" else RAIZ
        if not (base / destino).exists():
            rotas.append(destino)
    return rotas


def test_hay_documentacion_para_revisar():
    assert "AGENTS.md" in _md_versionados()


@pytest.mark.parametrize("archivo", _md_versionados())
def test_enlaces_internos_existen(archivo):
    assert _rotas(archivo) == [], f"{archivo} apunta a archivos que no existen"


RUTA_DE_LA_PC = re.compile(r"[A-Za-z]:[\\/]Users[\\/][\w.-]", re.IGNORECASE)


def test_docs_sin_rutas_de_la_pc():
    """Ningún `.md` versionado (planes incluidos) nombra la carpeta de un usuario: se escribe `~\\LinkedOut\\...`."""
    salida = subprocess.run(["git", "ls-files", "*.md"], cwd=RAIZ, capture_output=True, text=True, check=True)
    con_rutas = [f for f in salida.stdout.splitlines()
                 if f and RUTA_DE_LA_PC.search((RAIZ / f).read_text(encoding="utf-8"))]
    assert con_rutas == []


def test_reconoce_una_ruta_de_la_pc():
    assert RUTA_DE_LA_PC.search("`C:" + r"\Users\ana\LinkedOut`")
    assert not RUTA_DE_LA_PC.search("rutas como `~\\LinkedOut`, nunca `C:" + r"\Users\…`")


def test_detecta_un_enlace_roto(tmp_path, monkeypatch):
    monkeypatch.setattr(sys.modules[__name__], "RAIZ", tmp_path)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "existe.md").write_text("x", encoding="utf-8")
    (tmp_path / "a.md").write_text(
        "[bien](docs/existe.md) [mal](docs/no.md) `docs/falta.md` `tools/<x>.py` [web](https://x.y)",
        encoding="utf-8")
    assert _rotas("a.md") == ["docs/no.md", "docs/falta.md"]
