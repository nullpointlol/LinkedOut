"""Persistencia del perfil: bytes exactos, versiones y respaldo previo.

Los locks coordinan este módulo y el registro de aprendizajes en un único
proceso. No coordinan editores externos.
"""

import hashlib
import json
import shutil
import tempfile
import threading
from datetime import datetime
from pathlib import Path

import core.paths
from core import (aprendizaje_puntaje, aprendizajes, candidato_datos, descartes, estructura_cv, perfil_areas,
                  profile_parser, radar_consultas, reglas_portales, reglas_radar)


_ARCHIVOS = {
    "experiencia": "experiencia.txt",
    "personales": "personales.txt",
    "restricciones": "restricciones.json",
    "anios_por_area_creador": "anios_por_area_creador.json",
    "voz_email": "voz_email.md",
    # Reglas de lectura del Radar (creador, 12/11): las valida core/reglas_radar.py, igual que el Radar
    "reglas_radar": "reglas_radar.json",
    # Reglas de cada portal de empleo (Portales, 14/11): las valida core/reglas_portales.py
    "reglas_portales": reglas_portales.ARCHIVO,
    # Datos del candidato que el motor leía del código (creador, 13/11): los valida core/candidato_datos.py
    "stack": candidato_datos.ARCHIVO_STACK,
    "roles_objetivo": candidato_datos.ARCHIVO_ROLES,
    "conocimientos_ausentes": "conocimientos_ausentes.json",
    # Cómo se muestran los cargos de cada empresa en el CV (creador, 15/11): los valida core/estructura_cv.py
    "estructura_cv": estructura_cv.ARCHIVO,
    # Umbral del aprendizaje de las ofertas con puntaje bajo (creador, 15/11): core/aprendizaje_puntaje.py
    "aprendizaje_puntaje": aprendizaje_puntaje.ARCHIVO,
}
_JSON = ("restricciones", "anios_por_area_creador", "reglas_radar", "reglas_portales", "stack", "roles_objetivo",
         "conocimientos_ausentes", "estructura_cv", "aprendizaje_puntaje")
# Validadores de los JSON del perfil que viven en core/candidato_datos.py
_VALIDADORES = {
    "stack": ("Stack inválido", candidato_datos.validar_stack),
    "roles_objetivo": ("Roles objetivo inválidos", candidato_datos.validar_roles),
    "conocimientos_ausentes": ("Conocimientos ausentes inválidos", candidato_datos.validar_conocimientos_ausentes),
    "estructura_cv": ("Estructura del CV inválida", estructura_cv.validar),
    "aprendizaje_puntaje": ("Configuración del aprendizaje inválida", aprendizaje_puntaje.validar),
}
_LOCK = threading.RLock()


class PerfilError(Exception):
    """Error de contrato que el adaptador HTTP puede devolver al usuario."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _archivo(clave: str) -> Path:
    if not isinstance(clave, str) or clave not in _ARCHIVOS:
        raise PerfilError(404, "Dato del perfil inexistente.")
    return core.paths.DATA_DIR / "candidato" / _ARCHIVOS[clave]


def _leer_bytes(archivo: Path) -> tuple[bytes, bool]:
    try:
        return archivo.read_bytes(), True
    except FileNotFoundError:
        return b"", False


def _version(contenido: bytes) -> str:
    return hashlib.sha256(contenido).hexdigest()


def leer_perfil(clave: str) -> dict:
    with _LOCK:
        archivo = _archivo(clave)
        contenido, existe = _leer_bytes(archivo)
        return {
            "clave": clave,
            "archivo": archivo.name,
            "contenido": contenido.decode("utf-8"),
            "version": _version(contenido),
            "existe": existe,
        }


def _error_area(entrada, total) -> PerfilError:
    area = entrada.get("area", "") if isinstance(entrada, dict) else ""
    anios = entrada.get("anios", "") if isinstance(entrada, dict) else ""
    limite = (f"sin superar el total de {total} años" if total is not None
              else "sin total calculable")
    return PerfilError(
        422, f'Área inválida: “{area}” ({anios}). Usá “N” o “más de N”, {limite}.')


def _validar(clave: str, contenido: str, candidato_dir: Path) -> bytes:
    if not isinstance(contenido, str):
        raise PerfilError(422, "El contenido tiene que ser texto.")
    if clave not in _JSON:
        if not contenido.strip():
            raise PerfilError(422, "El contenido no puede quedar vacío.")
        if len(contenido) > 200000:
            raise PerfilError(422, "El contenido no puede superar los 200000 caracteres.")
    else:
        try:
            dato = json.loads(contenido)
        except ValueError as exc:
            raise PerfilError(422, f"No es un JSON válido: {exc}") from exc
        if clave == "restricciones":
            if not descartes._validas(dato):
                raise PerfilError(422, "Restricciones inválidas.")
        elif clave == "reglas_radar":
            errores = reglas_radar.validar_reglas(dato)
            if errores:
                raise PerfilError(422, "Reglas del Radar inválidas: " + " ".join(errores))
        elif clave == "reglas_portales":
            errores = reglas_portales.validar_reglas_portales(dato)
            if errores:
                raise PerfilError(422, "Reglas de Portales inválidas: " + " ".join(errores))
        elif clave in _VALIDADORES:
            titulo, validar = _VALIDADORES[clave]
            errores = validar(dato)
            if errores:
                raise PerfilError(422, f"{titulo}: " + " ".join(errores[:5]))
        else:
            lista = dato.get("anios_por_area") if isinstance(dato, dict) else dato
            if not isinstance(lista, list):
                raise PerfilError(422, "Los años por área tienen que ser una lista.")
            experiencia, _ = _leer_bytes(candidato_dir / "experiencia.txt")
            total = profile_parser.calcular_anios_experiencia(experiencia.decode("utf-8"))
            for indice, entrada in enumerate(lista):
                if (not isinstance(entrada, dict)
                        or not isinstance(entrada.get("area"), str)
                        or not isinstance(entrada.get("anios"), str)):
                    raise _error_area(entrada, total)
                if "reemplaza" in entrada and (
                        not isinstance(entrada["reemplaza"], list)
                        or any(not isinstance(valor, str) for valor in entrada["reemplaza"])):
                    raise PerfilError(422, "Reemplaza tiene que ser una lista de textos.")
                # Los prefijos identifican también el primer duplicado y la novena fila.
                try:
                    aceptadas = perfil_areas._validar_areas(lista[:indice + 1], total)
                except ValueError as exc:
                    raise _error_area(entrada, total) from exc
                if len(aceptadas) != indice + 1:
                    raise _error_area(entrada, total)
    try:
        return contenido.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise PerfilError(422, "El contenido no es texto UTF-8 válido.") from exc


def crear_backup(candidato_dir: Path) -> str:
    """Reserva un nombre exclusivo y respalda solo archivos del primer nivel.

    Si recibe un archivo (no una carpeta), respalda solo ese archivo."""
    with _LOCK:
        backups = core.paths.DATA_DIR / "backups"
        backups.mkdir(parents=True, exist_ok=True)
        base = f"candidato-{datetime.now():%Y%m%d-%H%M%S}"
        numero = 1
        while True:
            nombre = base if numero == 1 else f"{base}-{numero}"
            destino = backups / nombre
            try:
                destino.mkdir(exist_ok=False)
                break
            except FileExistsError:
                numero += 1
        try:
            if candidato_dir.is_file():
                archivos = [candidato_dir]
            elif candidato_dir.exists():
                archivos = list(candidato_dir.iterdir())
            else:
                archivos = []
            if archivos:
                for archivo in archivos:
                    if archivo.is_file():
                        copia = destino / archivo.name
                        shutil.copyfile(archivo, copia)
                        shutil.copystat(archivo, copia)
        except OSError:
            # Solo se elimina la carpeta recién reservada, nunca un respaldo previo.
            shutil.rmtree(destino, ignore_errors=True)
            raise
        return nombre


def _escribir_atomico(destino: Path, contenido: bytes) -> None:
    temporal = None
    try:
        with tempfile.NamedTemporaryFile(dir=destino.parent, delete=False) as archivo:
            temporal = Path(archivo.name)
            archivo.write(contenido)
        # Windows requiere cerrar el descriptor antes del reemplazo.
        temporal.replace(destino)
    finally:
        if temporal is not None:
            temporal.unlink(missing_ok=True)


def guardar_perfil(clave: str, contenido: str, version: str) -> dict:
    with _LOCK:
        archivo = _archivo(clave)
        anterior, _ = _leer_bytes(archivo)
        if version != _version(anterior):
            raise PerfilError(
                409, "El archivo cambió desde que lo abriste. Recargá antes de guardar.")
        nuevo = _validar(clave, contenido, archivo.parent)
        backup = crear_backup(archivo.parent)
        archivo.parent.mkdir(parents=True, exist_ok=True)
        _escribir_atomico(archivo, nuevo)
        aviso = None
        if clave in ("experiencia", "personales") and nuevo != anterior:
            aviso = "Los años por área se recalculan con IA en el próximo CV."
        return {"ok": True, "version": _version(nuevo), "backup": backup, "aviso": aviso}


def _archivo_busquedas() -> Path:
    return core.paths.DATA_DIR / "candidato" / radar_consultas.ARCHIVO


def version_busquedas() -> str:
    """La versión de busquedas.json (sha256 de sus bytes; la de un archivo vacío si no existe)."""
    with _LOCK:
        return _version(_leer_bytes(_archivo_busquedas())[0])


def guardar_portales_de_busquedas(cambios: list, version: str) -> dict:
    """Cambia solo `portales` y `palabras` de las búsquedas nombradas en `cambios` ([{nombre, portales, palabras}]).
    El resto del archivo queda igual. Valida todo el archivo como el Radar, rechaza versiones viejas (409) y deja
    antes un respaldo de la carpeta del candidato."""
    with _LOCK:
        archivo = _archivo_busquedas()
        anterior, existe = _leer_bytes(archivo)
        if version != _version(anterior):
            raise PerfilError(409, "Las búsquedas cambiaron desde que las abriste. Recargá antes de guardar.")
        if not existe:
            raise PerfilError(404, "No hay búsquedas en el perfil.")
        try:
            datos = json.loads(anterior.decode("utf-8"))
        except ValueError as exc:
            raise PerfilError(422, f"busquedas.json no es un JSON válido: {exc}") from exc
        if not isinstance(datos, dict) or not isinstance(datos.get("busquedas"), list):
            raise PerfilError(422, "busquedas.json no tiene la lista «busquedas».")
        por_nombre = {b["nombre"].strip(): b for b in datos["busquedas"]
                      if isinstance(b, dict) and isinstance(b.get("nombre"), str) and b["nombre"].strip()}
        if not isinstance(cambios, list):
            raise PerfilError(422, "Los cambios tienen que ser una lista.")
        for cambio in cambios:
            if not isinstance(cambio, dict) or not isinstance(cambio.get("nombre"), str):
                raise PerfilError(422, "Cada cambio lleva el nombre de la búsqueda.")
            busqueda = por_nombre.get(cambio["nombre"].strip())
            if busqueda is None:
                raise PerfilError(422, f"No existe la búsqueda «{cambio['nombre']}».")
            for clave in ("portales", "palabras"):
                if clave in cambio:
                    busqueda[clave] = cambio[clave]
            if busqueda.get("portales") == [] and not str(busqueda.get("palabras") or "").strip():
                busqueda.pop("portales", None)
                busqueda.pop("palabras", None)
        errores = [e for i, b in enumerate(datos["busquedas"], start=1)
                   for e in radar_consultas.errores_de_portales(b, i)]
        if errores:
            raise PerfilError(422, "Búsquedas inválidas: " + " ".join(errores[:5]))
        nuevo = (json.dumps(datos, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        backup = crear_backup(archivo.parent)
        _escribir_atomico(archivo, nuevo)
        return {"ok": True, "version": _version(nuevo), "backup": backup}


def leer_anios_por_area() -> dict:
    with _LOCK:
        areas = perfil_areas.anios_por_area_en_cache(
            candidato_dir=core.paths.DATA_DIR / "candidato")
        return {"areas": areas, "vigente": bool(areas)}


def borrar_aprendizaje(clave: str) -> dict:
    with _LOCK:
        # Siempre después de _LOCK: crear_backup vuelve a tomar ese RLock.
        with aprendizajes.LOCK:
            datos = aprendizajes.obtener_aprendizajes()
            if clave not in datos:
                raise PerfilError(404, "Aprendizaje inexistente.")
            # Solo el archivo de aprendizajes: su carpeta es DATA_DIR (base, .env, etc.)
            backup = crear_backup(aprendizajes.APRENDIZAJES_FILE)
            del datos[clave]
            if not aprendizajes._guardar_aprendizajes(datos):
                raise PerfilError(500, "No se pudo borrar el aprendizaje.")
            return {"ok": True, "backup": backup}
