"""Los scripts de despliegue tienen que parsear en Windows PowerShell 5.1, el que viene con Windows.

El primer deploy real falló antes de empezar: `"$sha:"` es una referencia de unidad inválida y, sin BOM, PowerShell 5.1
lee el UTF-8 como ANSI y una «Ó» se convierte en comillas tipográficas que cortan el texto.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPTS = sorted((Path(__file__).resolve().parent.parent / "tools").glob("*.ps1"))
POWERSHELL = shutil.which("powershell")


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_el_script_tiene_bom_utf8(script):
    assert script.read_bytes().startswith(b"\xef\xbb\xbf"), f"{script.name} sin BOM: PowerShell 5.1 rompe los acentos"


@pytest.mark.skipif(POWERSHELL is None, reason="sin Windows PowerShell")
@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_el_script_parsea_en_powershell_5(script):
    comando = (
        "$e = $null; [void][System.Management.Automation.Language.Parser]::ParseFile("
        f"'{script}', [ref]$null, [ref]$e); $e | ForEach-Object {{ $_.Extent.StartLineNumber.ToString() + ': ' + $_.Message }}"
    )
    salida = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", comando],
                            capture_output=True, text=True, timeout=60)
    assert salida.stdout.strip() == "", f"{script.name} no parsea:\n{salida.stdout}"
