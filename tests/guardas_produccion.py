"""Guardas de producción para la suite (13/11): la carpeta de datos, el `.env` y los puertos de los tests nunca son
los de producción, ni los de hoy ni los de después del corte (plan de separación). Las rutas salen del hogar del
usuario y del checkout principal del repo, nunca escritas a mano.

Antes del corte:  UBICACION_VIEJA (producción y su config), <checkout principal>/data y <checkout principal>/.env
Después del corte: ~/LinkedOut/produccion, ~/LinkedOut/datos y ~/LinkedOut/produccion.env, más los archivos sueltos
                   de producción en ~/LinkedOut (lanzador, vigilante, config y candado: ARCHIVOS_DE_PRODUCCION)
Puerto del portal real: 1111.
"""
import socket
import subprocess
from pathlib import Path
from typing import Dict, List, Mapping, Optional

PUERTOS_DE_PRODUCCION = frozenset({1111})
VARIABLES_DE_PUERTO = ("PORT", "LINKEDOUT_PUERTO", "UVICORN_PORT",
                       "BUSCATRABAJO_PUERTO")  # compatibilidad: se borra tras el deploy
# Carpeta de producción y config de antes del corte (14/11), con el nombre viejo del proyecto. Sigue existiendo en la
# PC (el CONGELADO de ahí todavía frena los despliegues): la guarda la cuida mientras exista.
UBICACION_VIEJA = ".buscatrabajo4"
HOSTS_LOCALES = frozenset({"127.0.0.1", "localhost", "::1", "0.0.0.0", ""})
# Producción después del corte (14/11): la tarea LinkedOut-portal corre portal.vbs -> portal.cmd, y el vigilante
# y la config de despliegue están al lado, en ~/LinkedOut (donde también vive el dev: dev, dev-datos, dev.env).
ARCHIVOS_DE_PRODUCCION = ("portal.cmd", "portal.vbs", "portal.log", "vigilante.ps1", "vigilante.log",
                          "despliegue.json", "despliegues.log", "CONGELADO")


class ProduccionProhibida(RuntimeError):
    """Un test intentó tocar producción."""


def repo_principal(desde: Path) -> Optional[Path]:
    """El checkout principal del repo (el que tiene `data/` y `.env` reales), también desde un worktree."""
    try:
        r = subprocess.run(["git", "-C", str(desde), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    comun = r.stdout.strip()
    return Path(comun).parent if r.returncode == 0 and comun else None


def rutas_de_produccion(hogar: Path, principal: Optional[Path]) -> Dict[str, List[Path]]:
    carpetas = [hogar / UBICACION_VIEJA, hogar / "LinkedOut" / "produccion", hogar / "LinkedOut" / "datos"]
    envs = [hogar / "LinkedOut" / "produccion.env"]
    archivos = [hogar / "LinkedOut" / nombre for nombre in ARCHIVOS_DE_PRODUCCION]
    if principal is not None:
        carpetas.append(principal / "data")
        envs.append(principal / ".env")
    return {"carpetas": carpetas, "env": envs, "archivos": archivos}


def _completa(ruta: Path) -> Path:
    return Path(ruta).expanduser().resolve()


def _relacionadas(a: Path, b: Path) -> bool:
    a, b = _completa(a), _completa(b)
    return a == b or b in a.parents or a in b.parents


def problemas(datos: Path, env: Path, variables: Mapping[str, str], rutas: Dict[str, List[Path]]) -> List[str]:
    """Qué apunta a producción (vacío si nada)."""
    encontrados = []
    for carpeta in rutas["carpetas"]:
        if _relacionadas(datos, carpeta):
            encontrados.append(f"La carpeta de datos de los tests ({datos}) apunta a producción ({carpeta})")
    for real in rutas["env"]:
        if _completa(env) == _completa(real):
            encontrados.append(f"El .env de los tests ({env}) es el de producción")
    for archivo in rutas.get("archivos", []):
        if _completa(env) == _completa(archivo):
            encontrados.append(f"El .env de los tests ({env}) es un archivo de producción ({archivo.name})")
        if _relacionadas(datos, archivo):
            encontrados.append(f"La carpeta de datos de los tests ({datos}) es o contiene {archivo.name} de producción")
    for nombre in VARIABLES_DE_PUERTO:
        valor = str(variables.get(nombre) or "").strip()
        if valor.isdigit() and int(valor) in PUERTOS_DE_PRODUCCION:
            encontrados.append(f"{nombre}={valor}: es el puerto del portal real")
    return encontrados


def bloquear_portal_real() -> None:
    """Durante los tests, conectarse a un puerto de producción en esta PC falla (nunca llega al portal real)."""
    if getattr(socket.socket.connect, "_guarda_produccion", False):
        return
    original_connect, original_connect_ex = socket.socket.connect, socket.socket.connect_ex

    def revisar(direccion):
        if isinstance(direccion, tuple) and len(direccion) >= 2:
            host, puerto = str(direccion[0]).lower(), direccion[1]
            if puerto in PUERTOS_DE_PRODUCCION and host in HOSTS_LOCALES:
                raise ProduccionProhibida(f"Un test intentó conectarse al portal real ({host}:{puerto})")

    def connect(self, direccion):
        revisar(direccion)
        return original_connect(self, direccion)

    def connect_ex(self, direccion):
        revisar(direccion)
        return original_connect_ex(self, direccion)

    connect._guarda_produccion = True
    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
