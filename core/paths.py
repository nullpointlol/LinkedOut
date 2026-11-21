"""Carpeta de datos del portal (base SQLite, perfil del candidato, exports, perfiles de navegador) y variables de entorno.

Viene siempre de `LINKEDOUT_DATA_DIR` (12-factor): producción la fija en su arranque. Sin la variable es
`<repo>/dev-datos`, una carpeta de desarrollo: nunca `<repo>/data`, que en el checkout principal son los datos
reales (separación de producción y desarrollo).

Las variables del portal se llaman `LINKEDOUT_<NOMBRE>` y se leen con `variable_entorno`: si falta la nueva, se lee
la del nombre viejo del proyecto, que es la que fija hoy el lanzador de producción.
"""
import os
from pathlib import Path
from typing import Mapping, Optional

PREFIJO_VARIABLES = "LINKEDOUT_"
PREFIJO_VARIABLES_VIEJO = "BUSCATRABAJO_"  # compatibilidad: se borra tras el deploy

# Marca que deja tools/login_cuenta.py en la carpeta del perfil de Edge cuando la sesión de LinkedIn quedó iniciada
MARCA_SESION = "linkedout_sesion_ok"
MARCA_SESION_VIEJA = "bt4_sesion_ok"  # compatibilidad: se borra tras el deploy


def variable_entorno(nombre: str, defecto: Optional[str] = None,
                     entorno: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """`LINKEDOUT_<nombre>`; si no está definida, la del nombre viejo; si tampoco, `defecto`.

    Una variable nueva definida aunque vacía gana: «MAIL_PRUEBA=» apaga el modo de prueba y nunca lo reactiva la vieja.
    """
    entorno = os.environ if entorno is None else entorno
    valor = entorno.get(PREFIJO_VARIABLES + nombre)
    if valor is None:
        valor = entorno.get(PREFIJO_VARIABLES_VIEJO + nombre)  # compatibilidad: se borra tras el deploy
    return defecto if valor is None else valor


def sesion_marcada(perfil: Path) -> bool:
    """¿La carpeta de perfil tiene la marca de sesión iniciada (con el nombre nuevo o el viejo)?"""
    perfil = Path(perfil)
    return (perfil / MARCA_SESION).is_file() or (perfil / MARCA_SESION_VIEJA).is_file()  # compatibilidad: se borra tras el deploy


def marcar_sesion(perfil: Path, texto: str) -> Path:
    """Deja la marca de sesión iniciada (siempre con el nombre nuevo) y devuelve su ruta."""
    marca = Path(perfil) / MARCA_SESION
    marca.write_text(texto, encoding="utf-8")
    return marca


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR_DESARROLLO = BASE_DIR / "dev-datos"
DATA_DIR = Path(variable_entorno("DATA_DIR") or DATA_DIR_DESARROLLO)
