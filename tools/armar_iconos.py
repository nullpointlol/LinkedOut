"""Arma static/iconos.svg (sprite de <symbol>) con los íconos Lucide copiados en static/vendor (rediseño, plan.md §4).

Sin CDN ni compilación en runtime: el sprite se commitea y el portal lo usa con <svg><use href="/static/iconos.svg#mail">.
Para sumar un ícono: copiar su .svg del tarball de lucide-static a static/vendor/lucide-static-<versión>/icons/,
registrar su SHA-256 en static/vendor/VERSIONES.json y correr este script. tests/test_rediseno_base.py verifica que el
sprite esté al día.

    python tools/armar_iconos.py              # escribe static/iconos.svg
    python tools/armar_iconos.py --verificar  # sale con 1 si no está al día
"""
import argparse
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
ICONOS = RAIZ / "static" / "vendor" / "lucide-static-1.66.0" / "icons"
SPRITE = RAIZ / "static" / "iconos.svg"
# Trazo del plan (§4): 1.75 en vez del 2 de Lucide; el resto, como Lucide
ATRIBUTOS = ('viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" '
             'stroke-linecap="round" stroke-linejoin="round"')


def _simbolo(archivo: Path) -> str:
    svg = archivo.read_text(encoding="utf-8")
    interior = re.search(r"<svg\b[^>]*>(.*)</svg>", svg, re.S)
    if not interior:
        raise ValueError(f"{archivo.name}: no es un SVG de Lucide")
    hijos = " ".join(l.strip() for l in interior.group(1).splitlines() if l.strip())
    return f'<symbol id="{archivo.stem}" {ATRIBUTOS}>{hijos}</symbol>'


def armar() -> str:
    archivos = sorted(ICONOS.glob("*.svg"))
    licencia = re.match(r"<!--.*?-->", archivos[0].read_text(encoding="utf-8")).group(0)
    lineas = [licencia,
              "<!-- Generado por tools/armar_iconos.py desde static/vendor/lucide-static-1.66.0: no editar a mano. -->",
              '<svg xmlns="http://www.w3.org/2000/svg">',
              *(_simbolo(a) for a in archivos),
              "</svg>"]
    return "\n".join(lineas) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--verificar", action="store_true", help="no escribe; sale con 1 si el sprite no está al día")
    args = ap.parse_args()
    nuevo = armar()
    actual = SPRITE.read_text(encoding="utf-8") if SPRITE.exists() else ""
    if args.verificar:
        print("al día" if actual == nuevo else "desactualizado: correr python tools/armar_iconos.py")
        return 0 if actual == nuevo else 1
    SPRITE.write_text(nuevo, encoding="utf-8")
    print(f"{SPRITE.relative_to(RAIZ)}: {nuevo.count('<symbol ')} íconos")
    return 0


if __name__ == "__main__":
    sys.exit(main())
