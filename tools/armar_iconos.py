"""Arma static/iconos.svg (sprite de <symbol>) y static/css/iconos.css con los íconos Lucide de static/vendor (plan.md §4).

Sin CDN ni compilación en runtime: el sprite se commitea y el portal lo usa con <svg><use href="/static/iconos.svg#mail">.
Para sumar un ícono: copiar su .svg del tarball de lucide-static a static/vendor/lucide-static-<versión>/icons/,
registrar su SHA-256 en static/vendor/VERSIONES.json y correr este script. tests/test_rediseno_base.py verifica que el
sprite esté al día.

static/css/iconos.css trae los mismos íconos como máscaras CSS (<span class="ico icono-mail">): las listas largas
del Radar los usan así porque no crean un subárbol SVG por tarjeta (con 2760 tarjetas, el <use> sumaba ~900 ms).

    python tools/armar_iconos.py              # escribe static/iconos.svg
    python tools/armar_iconos.py --verificar  # sale con 1 si no está al día
"""
import argparse
import re
import sys
from pathlib import Path
from urllib.parse import quote

RAIZ = Path(__file__).resolve().parent.parent
ICONOS = RAIZ / "static" / "vendor" / "lucide-static-1.66.0" / "icons"
SPRITE = RAIZ / "static" / "iconos.svg"
CSS = RAIZ / "static" / "css" / "iconos.css"
# Trazo del plan (§4): 1.75 en vez del 2 de Lucide; el resto, como Lucide
ATRIBUTOS = ('viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" '
             'stroke-linecap="round" stroke-linejoin="round"')


def _hijos(archivo: Path) -> str:
    svg = archivo.read_text(encoding="utf-8")
    interior = re.search(r"<svg\b[^>]*>(.*)</svg>", svg, re.S)
    if not interior:
        raise ValueError(f"{archivo.name}: no es un SVG de Lucide")
    return " ".join(l.strip() for l in interior.group(1).splitlines() if l.strip())


def _simbolo(archivo: Path) -> str:
    return f'<symbol id="{archivo.stem}" {ATRIBUTOS}>{_hijos(archivo)}</symbol>'


def armar_css() -> str:
    """Una clase .icono-<nombre> por ícono: máscara con el mismo trazo del sprite, pintada con currentColor."""
    reglas = [
        "/* Generado por tools/armar_iconos.py desde static/vendor/lucide-static-1.66.0 (ISC): no editar a mano. */",
        '.ico[class*="icono-"] { display: inline-block; flex-shrink: 0; vertical-align: middle; '
        'background-color: currentColor; -webkit-mask: var(--icono) center / contain no-repeat; '
        'mask: var(--icono) center / contain no-repeat; }',
    ]
    for archivo in sorted(ICONOS.glob("*.svg")):
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" {ATRIBUTOS.replace("currentColor", "black")}>'
               f'{_hijos(archivo)}</svg>')
        reglas.append(f'.icono-{archivo.stem} {{ --icono: url("data:image/svg+xml,{quote(svg, safe=" =:/,")}"); }}')
    return "\n".join(reglas) + "\n"


def armar() -> str:
    archivos = sorted(ICONOS.glob("*.svg"))
    # Comentario de licencia del primer ícono (sin regex: CodeQL py/bad-tag-filter)
    texto = archivos[0].read_text(encoding="utf-8")
    licencia = texto[:texto.index("-->") + 3] if texto.startswith("<!--") else ""
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
    nuevo, nuevo_css = armar(), armar_css()
    actual = SPRITE.read_text(encoding="utf-8") if SPRITE.exists() else ""
    actual_css = CSS.read_text(encoding="utf-8") if CSS.exists() else ""
    if args.verificar:
        al_dia = actual == nuevo and actual_css == nuevo_css
        print("al día" if al_dia else "desactualizado: correr python tools/armar_iconos.py")
        return 0 if al_dia else 1
    SPRITE.write_text(nuevo, encoding="utf-8")
    CSS.write_text(nuevo_css, encoding="utf-8")
    print(f"{SPRITE.relative_to(RAIZ)} y {CSS.relative_to(RAIZ)}: {nuevo.count('<symbol ')} íconos")
    return 0


if __name__ == "__main__":
    sys.exit(main())
