"""Tests rápidos en la PC: solo los tests de los archivos tocados, con un worker y en prioridad baja.

La suite completa (tools/verificar.py) corre en el CI de GitHub en cada push y pull request: en la PC, aun con 2 workers en prioridad baja,
trababa al portal real (disco saturado y Defender escaneando). Acá:
- archivos tocados = los cambios de la rama contra origin/main, más lo modificado y lo nuevo sin commitear;
- un test tocado se corre él; un módulo o archivo tocado corre los tests que lo nombran (si son más de
  TOPE_POR_ARCHIVO, solo los que llevan su nombre: el resto lo cubre el CI);
- sin navegador ni rendimiento (los corre el CI), con -n 1, en BelowNormal y con el candado de la compuerta.

    python tools/tests_rapidos.py            # contra origin/main
    python tools/tests_rapidos.py --base main
"""
import argparse
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable, List

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import verificar  # noqa: E402
from candado_compuerta import candado  # noqa: E402

TOPE_POR_ARCHIVO = 25
BASE = "origin/main"


def _git(*args: str) -> List[str]:
    r = subprocess.run(["git", "-C", str(RAIZ), *args], capture_output=True, text=True, encoding="utf-8")
    return [l.strip() for l in r.stdout.splitlines() if l.strip()] if r.returncode == 0 else []


def archivos_tocados(base: str = BASE) -> List[str]:
    tocados = (_git("diff", "--name-only", f"{base}...HEAD") + _git("diff", "--name-only")
               + _git("diff", "--name-only", "--cached") + _git("ls-files", "--others", "--exclude-standard"))
    return sorted(set(t.replace("\\", "/") for t in tocados))


def tests_para(archivos: Iterable[str], raiz: Path = RAIZ) -> List[str]:
    """Los archivos de test (relativos, con /) que corresponden a los archivos tocados."""
    raiz = Path(raiz)
    tests = sorted((raiz / "tests").glob("test_*.py"))
    textos = {t: t.read_text(encoding="utf-8", errors="replace") for t in tests}
    elegidos = set()
    for archivo in archivos:
        ruta = raiz / archivo
        if archivo.startswith("tests/test_") and archivo.endswith(".py"):
            if ruta.is_file():
                elegidos.add(archivo)
            continue
        if archivo.startswith(("docs/", ".github/")) or archivo.endswith(".md"):
            continue
        nombre = Path(archivo).stem if archivo.endswith(".py") else Path(archivo).name
        patron = re.compile(rf"(?<![\w.]){re.escape(nombre)}(?!\w)")
        usan = [t for t, texto in textos.items() if patron.search(texto)]
        if len(usan) > TOPE_POR_ARCHIVO:
            usan = [t for t in usan if Path(nombre).stem in t.stem]
        elegidos.update(t.relative_to(raiz).as_posix() for t in usan)
    return sorted(elegidos)


def _pytest(args: List[str]) -> int:
    entorno = dict(os.environ, PYTHONUTF8="1")
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *args], cwd=RAIZ,
                          env=entorno, **verificar._prioridad_baja()).returncode


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Tests de los archivos tocados, con un worker y en prioridad baja.")
    p.add_argument("--base", default=BASE)
    a = p.parse_args(argv)
    verificar.bajar_prioridad()
    tocados = archivos_tocados() if a.base == BASE else archivos_tocados(a.base)
    tests = tests_para(tocados)
    if not tests:
        print("No hay tests para los archivos tocados. La suite completa la corre el CI de GitHub.")
        return 0
    print(f"{len(tests)} archivos de test para {len(tocados)} archivos tocados (la suite completa corre en el CI):")
    for t in tests:
        print(f"  {t}")
    with candado(f"tests_rapidos.py en {RAIZ}"):
        return _pytest(["-n", "1", "-m", "not ui and not rendimiento", *tests])


if __name__ == "__main__":
    sys.exit(main())
