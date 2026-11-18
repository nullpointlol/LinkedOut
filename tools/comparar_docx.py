"""Compara el formato de dos docx sin mostrar su texto.

    python tools/comparar_docx.py a.docx b.docx [--permitidas parte1,parte2]

Sale con 0 si tienen el mismo formato y con 1 si hay diferencias (las lista por parte o por ubicación).
Compara el sha256 de cada parte del zip (salvo las permitidas) y, con python-docx, por párrafo: estilo,
alineación, sangrías, espaciado y keep_with_next; por run: fuente, tamaño, negrita, cursiva, subrayado y color;
por sección: tamaño de página y márgenes; cantidad de tablas; encabezados y pies.
"""
import hashlib
import sys
import zipfile
from pathlib import Path

import docx


def _partes(ruta: Path) -> dict[str, str]:
    with zipfile.ZipFile(ruta) as z:
        return {n: hashlib.sha256(z.read(n)).hexdigest() for n in z.namelist()}


def _formato_parrafo(p) -> dict:
    f = p.paragraph_format
    return {
        "estilo": p.style.name if p.style is not None else None,
        "alineación": f.alignment,
        "sangría izquierda": f.left_indent,
        "sangría derecha": f.right_indent,
        "sangría primera línea": f.first_line_indent,
        "espacio antes": f.space_before,
        "espacio después": f.space_after,
        "interlineado": f.line_spacing,
        "keep_with_next": f.keep_with_next,
    }


def _formato_run(r) -> dict:
    color = r.font.color
    return {
        "fuente": r.font.name,
        "tamaño": r.font.size,
        "negrita": r.bold,
        "cursiva": r.italic,
        "subrayado": r.underline,
        "color": str(color.rgb) if color is not None and color.type is not None else None,
        "largo": len(r.text),
    }


def _formato_seccion(s) -> dict:
    return {
        "ancho de página": s.page_width,
        "alto de página": s.page_height,
        "margen superior": s.top_margin,
        "margen inferior": s.bottom_margin,
        "margen izquierdo": s.left_margin,
        "margen derecho": s.right_margin,
        "párrafos de encabezado": len(s.header.paragraphs),
        "párrafos de pie": len(s.footer.paragraphs),
        "largo de encabezado": sum(len(p.text) for p in s.header.paragraphs),
        "largo de pie": sum(len(p.text) for p in s.footer.paragraphs),
    }


def _comparar_dict(lugar: str, a: dict, b: dict, difs: list[str]) -> None:
    for clave in a:
        if a[clave] != b.get(clave):
            difs.append(f"{lugar}: {clave} distinto ({a[clave]!r} → {b.get(clave)!r})")


def comparar(a: Path, b: Path, permitidas: frozenset = frozenset()) -> list[str]:
    difs: list[str] = []
    pa, pb = _partes(Path(a)), _partes(Path(b))
    for parte in sorted(set(pa) | set(pb)):
        if parte in permitidas:
            continue
        if parte not in pa:
            difs.append(f"parte {parte}: solo está en el segundo")
        elif parte not in pb:
            difs.append(f"parte {parte}: falta en el segundo")
        elif pa[parte] != pb[parte]:
            difs.append(f"parte {parte}: sha256 distinto")

    try:
        da, db = docx.Document(str(a)), docx.Document(str(b))
    except Exception as e:  # una parte que falta puede impedir abrirlo: ya quedó informada arriba
        difs.append(f"no se pudo abrir con python-docx ({type(e).__name__})")
        return difs
    if len(da.paragraphs) != len(db.paragraphs):
        difs.append(f"cantidad de párrafos distinta ({len(da.paragraphs)} → {len(db.paragraphs)})")
    for i, (x, y) in enumerate(zip(da.paragraphs, db.paragraphs)):
        _comparar_dict(f"párrafo {i}", _formato_parrafo(x), _formato_parrafo(y), difs)
        if len(x.runs) != len(y.runs):
            difs.append(f"párrafo {i}: cantidad de runs distinta ({len(x.runs)} → {len(y.runs)})")
        for j, (rx, ry) in enumerate(zip(x.runs, y.runs)):
            _comparar_dict(f"párrafo {i}, run {j}", _formato_run(rx), _formato_run(ry), difs)
    if len(da.tables) != len(db.tables):
        difs.append(f"cantidad de tablas distinta ({len(da.tables)} → {len(db.tables)})")
    if len(da.sections) != len(db.sections):
        difs.append(f"cantidad de secciones distinta ({len(da.sections)} → {len(db.sections)})")
    for k, (sx, sy) in enumerate(zip(da.sections, db.sections)):
        _comparar_dict(f"sección {k}", _formato_seccion(sx), _formato_seccion(sy), difs)
    return difs


def main(argv: list[str]) -> int:
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    args = list(argv)
    permitidas: frozenset = frozenset()
    if "--permitidas" in args:
        i = args.index("--permitidas")
        permitidas = frozenset(p for p in args[i + 1].split(",") if p)
        del args[i:i + 2]
    if len(args) != 2:
        print("uso: python tools/comparar_docx.py a.docx b.docx [--permitidas p1,p2]", file=sys.stderr)
        return 2
    difs = comparar(Path(args[0]), Path(args[1]), permitidas)
    for d in difs:
        print(d)
    print(f"{len(difs)} diferencias" + (f" (permitidas: {', '.join(sorted(permitidas))})" if permitidas else ""))
    return 1 if difs else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
