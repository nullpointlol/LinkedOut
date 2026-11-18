"""Datos del candidato que usa el motor, fuera del código (creador, 13/11).

Viven en `<DATA_DIR>/candidato/` y se editan en Mi perfil (`core/perfil_candidato.py`, que valida
con estas mismas funciones). Sin el archivo, o con un archivo inválido, cada lector devuelve vacío:
el motor no inventa ni completa nada del candidato.

- `stack.json`: coincidencias directas y transferibles del análisis sin IA, y habilidades base del
  CV de respaldo.
- `roles_objetivo.json`: roles que busca el candidato (grupo A) y sus áreas de trayectoria.
- `personales.txt`: el `Puesto:` es el rol por defecto del CV y del mail.
"""
import copy
import json
import re
import threading
import unicodedata
from pathlib import Path
from typing import Callable, Iterable, Optional

import core.paths

ARCHIVO_STACK = "stack.json"
ARCHIVO_ROLES = "roles_objetivo.json"
MAX_ITEMS = 200
MAX_TEXTO = 300

_LOCK = threading.Lock()
_CACHE: dict = {}


def _carpeta() -> Path:
    return core.paths.DATA_DIR / "candidato"


def normalizar(texto: Optional[str]) -> str:
    """Minúsculas, sin acentos y con espacios simples."""
    if not texto:
        return ""
    descompuesto = unicodedata.normalize("NFD", str(texto).casefold())
    return " ".join("".join(c for c in descompuesto if not unicodedata.combining(c)).split())


# ---------------------------------------------------------------- validación

def _texto(valor, campo: str, errores: list) -> bool:
    if not isinstance(valor, str) or not valor.strip():
        errores.append(f"«{campo}» tiene que ser un texto no vacío.")
        return False
    if len(valor) > MAX_TEXTO:
        errores.append(f"«{campo}» no puede superar los {MAX_TEXTO} caracteres.")
        return False
    return True


def _lista(valor, campo: str, errores: list, *, minimo: int = 0) -> bool:
    if not isinstance(valor, list) or len(valor) > MAX_ITEMS:
        errores.append(f"«{campo}» tiene que ser una lista de hasta {MAX_ITEMS} textos.")
        return False
    if len(valor) < minimo:
        errores.append(f"«{campo}» necesita al menos {minimo} texto.")
        return False
    vistos = set()
    for item in valor:
        if not _texto(item, campo, errores):
            return False
        if item.strip().lstrip("=").strip() == "":
            errores.append(f"«{campo}» tiene un texto vacío.")
            return False
        clave = normalizar(item)
        if clave in vistos:
            errores.append(f"«{campo}» tiene «{item}» repetido.")
            return False
        vistos.add(clave)
    return True


def _claves(dato: dict, permitidas: Iterable[str], nombre: str, errores: list) -> None:
    for clave in dato:
        if clave not in permitidas:
            errores.append(f"{nombre}: «{clave}» no es un campo conocido.")


def _filas(dato, campo: str, columnas: tuple, errores: list) -> None:
    """Lista de objetos con `nombre`, `palabras` y las columnas de texto pedidas."""
    filas = dato.get(campo, [])
    if not isinstance(filas, list) or len(filas) > MAX_ITEMS:
        errores.append(f"«{campo}» tiene que ser una lista de hasta {MAX_ITEMS} filas.")
        return
    nombres = set()
    for numero, fila in enumerate(filas, 1):
        etiqueta = f"{campo} {numero}"
        if not isinstance(fila, dict):
            errores.append(f"{etiqueta}: tiene que ser un objeto.")
            continue
        _claves(fila, ("nombre", "palabras") + columnas, etiqueta, errores)
        if _texto(fila.get("nombre"), f"{etiqueta}: nombre", errores):
            clave = normalizar(fila["nombre"])
            if clave in nombres:
                errores.append(f"{etiqueta}: «{fila['nombre']}» está repetido.")
            nombres.add(clave)
        _lista(fila.get("palabras"), f"{etiqueta}: palabras", errores, minimo=1)
        for columna in columnas:
            _texto(fila.get(columna), f"{etiqueta}: {columna}", errores)


def validar_stack(dato) -> list:
    """Errores de `stack.json`; lista vacía si es válido."""
    errores: list = []
    if not isinstance(dato, dict):
        return ["El stack tiene que ser un objeto JSON."]
    _claves(dato, ("nota", "coincidencias", "transferibles", "habilidades_base"), "Stack", errores)
    if "nota" in dato and not isinstance(dato["nota"], str):
        errores.append("«nota» tiene que ser texto.")
    _filas(dato, "coincidencias", ("justificacion",), errores)
    _filas(dato, "transferibles", ("equivalente",), errores)
    base = dato.get("habilidades_base", {})
    if not isinstance(base, dict):
        errores.append("«habilidades_base» tiene que ser un objeto con «gestion» y «tecnicas».")
    else:
        _claves(base, ("gestion", "tecnicas"), "habilidades_base", errores)
        for clave in ("gestion", "tecnicas"):
            _lista(base.get(clave, []), f"habilidades_base: {clave}", errores)
    return errores


def validar_roles(dato) -> list:
    """Errores de `roles_objetivo.json`; lista vacía si es válido."""
    errores: list = []
    if not isinstance(dato, dict):
        return ["Los roles objetivo tienen que ser un objeto JSON."]
    _claves(dato, ("nota", "roles_objetivo", "areas"), "Roles", errores)
    if "nota" in dato and not isinstance(dato["nota"], str):
        errores.append("«nota» tiene que ser texto.")
    _lista(dato.get("roles_objetivo", []), "roles_objetivo", errores)
    areas = dato.get("areas", [])
    if not isinstance(areas, list) or len(areas) > MAX_ITEMS:
        errores.append(f"«areas» tiene que ser una lista de hasta {MAX_ITEMS} áreas.")
        return errores
    vistas = set()
    for numero, area in enumerate(areas, 1):
        etiqueta = f"área {numero}"
        if not isinstance(area, dict):
            errores.append(f"{etiqueta}: tiene que ser un objeto.")
            continue
        _claves(area, ("area", "titulos", "sinonimos", "nombres", "perfil_actual"), etiqueta, errores)
        if _texto(area.get("area"), f"{etiqueta}: area", errores):
            clave = normalizar(area["area"])
            if clave in vistas:
                errores.append(f"{etiqueta}: «{area['area']}» está repetida.")
            vistas.add(clave)
        _lista(area.get("titulos"), f"{etiqueta}: titulos", errores, minimo=1)
        if _lista(area.get("sinonimos", []), f"{etiqueta}: sinonimos", errores):
            for patron in area.get("sinonimos", []):
                try:
                    re.compile(patron)
                except re.error:
                    errores.append(f"{etiqueta}: el sinónimo «{patron}» no es una expresión válida.")
        _lista(area.get("nombres", []), f"{etiqueta}: nombres", errores)
        if "perfil_actual" in area and type(area["perfil_actual"]) is not bool:
            errores.append(f"{etiqueta}: «perfil_actual» tiene que ser verdadero o falso.")
    return errores


def validar_conocimientos_ausentes(dato) -> list:
    """Errores de `conocimientos_ausentes.json` (lo lee `core/cv_guardas.py`); lista vacía si es válido."""
    errores: list = []
    if not isinstance(dato, dict):
        return ["Los conocimientos ausentes tienen que ser un objeto JSON."]
    _claves(dato, ("nota", "conocimientos"), "Conocimientos ausentes", errores)
    if "nota" in dato and not isinstance(dato["nota"], str):
        errores.append("«nota» tiene que ser texto.")
    _lista(dato.get("conocimientos", []), "conocimientos", errores)
    return errores


# ---------------------------------------------------------------- lectura

def _leer_json(nombre: str, validar: Callable[[object], list], normalizar_dato: Callable[[dict], dict]) -> dict:
    ruta = _carpeta() / nombre
    try:
        estado = ruta.stat()
    except OSError:
        return normalizar_dato({})
    clave = (str(ruta), estado.st_mtime_ns, estado.st_size)
    with _LOCK:
        guardado = _CACHE.get(nombre)
        if guardado and guardado[0] == clave:
            return copy.deepcopy(guardado[1])
    try:
        dato = json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        dato = None
    valor = normalizar_dato(dato if dato is not None and not validar(dato) else {})
    with _LOCK:
        _CACHE[nombre] = (clave, valor)
    return copy.deepcopy(valor)


def _stack_normalizado(dato: dict) -> dict:
    base = dato.get("habilidades_base") or {}
    return {
        "coincidencias": list(dato.get("coincidencias", [])),
        "transferibles": list(dato.get("transferibles", [])),
        "habilidades_base": {"gestion": list(base.get("gestion", [])), "tecnicas": list(base.get("tecnicas", []))},
    }


def _roles_normalizado(dato: dict) -> dict:
    areas = []
    for area in dato.get("areas", []):
        areas.append({
            "area": area["area"],
            "titulos": list(area["titulos"]),
            "sinonimos": list(area.get("sinonimos", [])),
            "nombres": list(area.get("nombres") or [area["area"]]),
            "perfil_actual": area.get("perfil_actual", True),
        })
    return {"roles_objetivo": list(dato.get("roles_objetivo", [])), "areas": areas}


def stack() -> dict:
    """Stack del candidato; vacío si falta o no es válido."""
    return _leer_json(ARCHIVO_STACK, validar_stack, _stack_normalizado)


def roles() -> dict:
    """Roles objetivo y áreas del candidato; vacío si falta o no es válido."""
    return _leer_json(ARCHIVO_ROLES, validar_roles, _roles_normalizado)


def puesto() -> str:
    """`Puesto:` de personales.txt; vacío si no está declarado."""
    from core import profile_parser

    try:
        texto = (_carpeta() / "personales.txt").read_text(encoding="utf-8")
    except OSError:
        return ""
    return profile_parser._extract_puesto(texto)


def palabras_stack() -> set:
    """Palabras de herramientas del stack (coincidencias y transferibles), en minúsculas."""
    datos = stack()
    return {p.strip().lower() for fila in datos["coincidencias"] + datos["transferibles"]
            for p in fila["palabras"]}


# ---------------------------------------------------------------- coincidencias

_REGEX: dict = {}


def coincide_frase(texto: Optional[str], frases: Iterable[str]) -> bool:
    """¿El texto contiene alguna frase como palabras completas? «=frase» exige el texto completo."""
    normalizado = normalizar(texto)
    if not normalizado:
        return False
    exactas, parciales = [], []
    for frase in frases:
        if frase.strip().startswith("="):
            exactas.append(normalizar(frase.strip()[1:]))
        else:
            parciales.append(normalizar(frase))
    if normalizado in exactas:
        return True
    parciales = tuple(p for p in parciales if p)
    if not parciales:
        return False
    patron = _REGEX.get(parciales)
    if patron is None:
        patron = re.compile(r"\b(?:" + "|".join(re.escape(p) for p in parciales) + r")\b")
        _REGEX[parciales] = patron
    return bool(patron.search(normalizado))
