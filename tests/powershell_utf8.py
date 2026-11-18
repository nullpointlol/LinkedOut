"""Corre un script de PowerShell 5.1 y devuelve (código, salida) bien decodificada en la PC y en el CI (13/11).

La salida de PowerShell usa la página de códigos de la consola, que no es la misma en la PC (OEM 850) que en el runner
de GitHub (UTF-8): «Producción» llegaba como «Producci├│n» y los tests fallaban solo en el CI. Se decodifica como
UTF-8 y, si no lo es, con la página OEM de esta máquina."""
import subprocess
from pathlib import Path
from typing import Mapping, Sequence, Tuple


def decodificar(datos: bytes) -> str:
    try:
        return datos.decode("utf-8")
    except UnicodeDecodeError:
        return datos.decode("oem", errors="replace")


def correr(script: Path, argumentos: Sequence[str], entorno: Mapping[str, str], timeout: int = 120) -> Tuple[int, str]:
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script),
                        *argumentos], capture_output=True, env=dict(entorno), timeout=timeout)
    return r.returncode, decodificar(r.stdout) + decodificar(r.stderr)
