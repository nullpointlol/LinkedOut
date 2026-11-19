#!/usr/bin/env python3
"""Re-chequeo de LISTO y ANALIZADA con la regla «en inglés y sin ubicación compatible» (creador, 19/11).

    python tools/rechequear_ubicacion.py --data-dir <copia de datos>            # solo cuenta
    python tools/rechequear_ubicacion.py --data-dir <copia de datos> --aplicar  # además las pasa a IGNORADA

Sobre los datos de producción no corre: se prueba sobre una copia y la aplica el coordinador con el OK del
creador, tras el deploy. Imprime un JSON con cuántas cambiarían y sus ids. Nada se borra.
"""
import argparse
import json
import os
import sys
from pathlib import Path
from typing import List, Optional

RAIZ = Path(__file__).resolve().parent.parent
DATOS_PRODUCCION = Path.home() / "LinkedOut" / "datos"


def es_produccion(carpeta: Path) -> bool:
    """La carpeta de datos de producción, o una dentro de ella."""
    real, prod = carpeta.resolve(), DATOS_PRODUCCION.resolve()
    return real == prod or prod in real.parents


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", required=True, help="carpeta de datos (una copia, nunca producción)")
    parser.add_argument("--aplicar", action="store_true", help="aplica el cambio; sin esto solo cuenta")
    args = parser.parse_args(argv)
    carpeta = Path(args.data_dir)
    if es_produccion(carpeta):
        print("Se niega: son los datos de producción. Probá sobre una copia.", file=sys.stderr)
        return 2
    if not (carpeta / "candidato" / "restricciones.json").is_file():
        print(f"No hay perfil en {carpeta}: falta candidato/restricciones.json", file=sys.stderr)
        return 2
    os.environ["LINKEDOUT_DATA_DIR"] = str(carpeta.resolve())
    sys.path.insert(0, str(RAIZ))
    from core.rechequeo_ubicacion import rechequear  # después de fijar la carpeta de datos

    print(json.dumps(rechequear(aplicar=args.aplicar), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
