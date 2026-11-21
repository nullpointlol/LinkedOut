"""La salida de PowerShell se lee bien en la PC (OEM) y en el runner de GitHub (UTF-8)."""
import sys

import pytest

from tests.powershell_utf8 import decodificar


def test_utf8_del_runner():
    assert decodificar("Producción congelada".encode("utf-8")) == "Producción congelada"


@pytest.mark.skipif(sys.platform != "win32", reason="página de códigos OEM de Windows")
def test_oem_de_la_pc():
    assert decodificar("Producción congelada".encode("oem")) == "Producción congelada"
