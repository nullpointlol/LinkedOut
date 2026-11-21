"""¿Lo instalado cumple un requirements.txt?

El deploy lo usa cuando `requirements.txt` cambió entre la versión que corre y la nueva: si cada paquete está instalado
con la versión del pin, se puede desplegar; si no, lista lo que falta y sale con 1.

    git show <commit>:requirements.txt | python tools/requisitos_instalados.py -
"""
from __future__ import annotations

import importlib.metadata
import re
import sys
from typing import List

_LINEA = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:==\s*([^\s;#]+))?")


def _normal(nombre: str) -> str:
    return re.sub(r"[-_.]+", "-", nombre).lower()


def faltantes(texto: str) -> List[str]:
    """Las líneas del requirements que lo instalado no cumple, con el motivo."""
    salida = []
    for linea in texto.splitlines():
        linea = linea.split("#", 1)[0].strip()
        m = _LINEA.match(linea)
        if not m:
            continue
        nombre, pin = m.group(1), m.group(2)
        try:
            instalado = importlib.metadata.version(_normal(nombre))
        except importlib.metadata.PackageNotFoundError:
            salida.append(f"{linea} (no está instalado)")
            continue
        if pin and instalado != pin:
            salida.append(f"{linea} (instalado {instalado})")
    return salida


def main(argv: List[str]) -> int:
    origen = argv[1] if len(argv) > 1 else "-"
    texto = sys.stdin.read() if origen == "-" else open(origen, encoding="utf-8").read()
    lista = faltantes(texto)
    for linea in lista:
        print(linea)
    return 1 if lista else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
