"""Pisos de seguridad de las dependencias (A3 de docs/seguridad/auditoria-20261117.md, 17/11).

Una dependencia con CVE corregida en cierta versión queda fijada en `requirements.txt` en esa versión o más.
Que lo instalado cumpla el pin lo controla el deploy (`tools/requisitos_instalados.py`).
"""
import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

# paquete: (versión mínima, motivo)
PISOS = {
    "pillow": ((13, 0, 0), "el OCR abre imágenes de terceros; 12.3.0 tiene fallas de memoria al decodificar"),
}


def _pins():
    pins = {}
    for linea in (RAIZ / "requirements.txt").read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*==\s*([0-9][^\s;#]*)", linea)
        if m:
            pins[re.sub(r"[-_.]+", "-", m.group(1)).lower()] = m.group(2)
    return pins


def _version(texto):
    return tuple(int(p) for p in re.findall(r"\d+", texto)[:3])


def test_cada_piso_de_seguridad_esta_fijado_en_requirements():
    pins = _pins()
    for paquete, (minima, motivo) in PISOS.items():
        assert paquete in pins, f"{paquete} sin pin en requirements.txt ({motivo})"
        assert _version(pins[paquete]) >= minima, f"{paquete}=={pins[paquete]} < {minima} ({motivo})"
