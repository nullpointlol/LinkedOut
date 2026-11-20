"""Reglas del Radar en el perfil del candidato: `<DATA_DIR>/candidato/reglas_radar.json`.

Todas las reglas de lectura (presupuesto de búsquedas, pausas, largo de cada lectura, pausa por bloqueo y topes del
día de envíos) están en ese archivo, visible y editable en Mi perfil. El código no trae ningún valor por defecto: sin archivo,
con un campo de menos o inválido, `leer_reglas()` lanza `ReglasInvalidas` y el Radar no lee nada (lo seguro para
la cuenta), muestra el motivo y abre un ticket.

Formato (todos los números enteros mayores que 0):
    busquedas_por_hora, busquedas_por_dia                 de cada cuenta de LinkedIn
    porcentaje_si_las_dos_cuentas_se_pausaron (1–100)     el presupuesto que queda si LinkedIn pausó a las dos en 24 h
    pausa_entre_lecturas_min {desde, hasta}               minutos, al azar entre los dos
    pausa_entre_vueltas_min {desde, hasta}                minutos, al azar entre los dos
    lectura_busqueda {posts, scrolls, pantallas_sin_nuevos}   igual para toda búsqueda
    lectura_feed {posts, scrolls}
    pausa_bloqueo_horas {primera, maxima}                 se duplica con cada bloqueo en 24 h hasta la máxima
    tope_envios_dia                                       envíos del Radar por día
    cuenta_feed, cuenta_busquedas ("principal"/"secundaria")  con qué cuenta de LinkedIn se lee cada cosa.
                                                          Pueden faltar: entonces esa lectura no se hace (el
                                                          Radar avisa); nunca se usa otra cuenta.
"""
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import core.paths

ARCHIVO = "reglas_radar.json"

_ENTEROS = ("busquedas_por_hora", "busquedas_por_dia", "porcentaje_si_las_dos_cuentas_se_pausaron",
            "tope_envios_dia")
_OBJETOS = {
    "pausa_entre_lecturas_min": ("desde", "hasta"),
    "pausa_entre_vueltas_min": ("desde", "hasta"),
    "lectura_busqueda": ("posts", "scrolls", "pantallas_sin_nuevos"),
    "lectura_feed": ("posts", "scrolls"),
    "pausa_bloqueo_horas": ("primera", "maxima"),
}
# Las cuentas de LinkedIn que conoce el Radar (core/radar_store.py: CUENTA_PRINCIPAL, CUENTA_SECUNDARIA)
CUENTAS = ("principal", "secundaria")
_CUENTAS_CAMPOS = ("cuenta_feed", "cuenta_busquedas")
_ORDENADOS = {"pausa_entre_lecturas_min": ("desde", "hasta"), "pausa_entre_vueltas_min": ("desde", "hasta"),
              "pausa_bloqueo_horas": ("primera", "maxima")}


class ReglasInvalidas(Exception):
    """El archivo de reglas falta o no es válido: el Radar no lee nada hasta que se corrija."""


@dataclass(frozen=True)
class Reglas:
    busquedas_por_hora: int
    busquedas_por_dia: int
    porcentaje_dos_pausadas: int
    pausa_entre_lecturas_s: Tuple[float, float]
    pausa_entre_vueltas_s: Tuple[float, float]
    busqueda_posts: int
    busqueda_scrolls: int
    busqueda_pantallas_sin_nuevos: int
    feed_posts: int
    feed_scrolls: int
    bloqueo_primera_h: int
    bloqueo_maxima_h: int
    tope_envios_dia: int
    cuenta_feed: Optional[str] = None
    cuenta_busquedas: Optional[str] = None

    def presupuesto(self, dos_pausadas: bool) -> Tuple[int, int]:
        """(por hora, por día) de una cuenta; si LinkedIn pausó a las dos en 24 h, el porcentaje (hacia abajo)."""
        if not dos_pausadas:
            return self.busquedas_por_hora, self.busquedas_por_dia
        return (self.busquedas_por_hora * self.porcentaje_dos_pausadas // 100,
                self.busquedas_por_dia * self.porcentaje_dos_pausadas // 100)


def _entero(valor: Any) -> bool:
    return isinstance(valor, int) and not isinstance(valor, bool) and valor > 0


def validar_reglas(datos: Any) -> List[str]:
    """Los errores del contenido de reglas_radar.json (vacía = válido), en castellano y con el campo."""
    if not isinstance(datos, dict):
        return ["Las reglas del Radar tienen que ser un objeto JSON."]
    errores: List[str] = []
    for clave in datos:
        if clave not in _ENTEROS and clave not in _OBJETOS and clave not in _CUENTAS_CAMPOS:
            errores.append(f"Campo desconocido: «{clave}».")
    for clave in _ENTEROS:
        if clave not in datos:
            errores.append(f"Falta «{clave}».")
        elif not _entero(datos[clave]):
            errores.append(f"«{clave}» tiene que ser un entero mayor que 0.")
    for clave, campos in _OBJETOS.items():
        if clave not in datos:
            errores.append(f"Falta «{clave}».")
            continue
        objeto = datos[clave]
        if not isinstance(objeto, dict):
            errores.append(f"«{clave}» tiene que ser un objeto con {', '.join(campos)}.")
            continue
        for sub in objeto:
            if sub not in campos:
                errores.append(f"Campo desconocido: «{clave}.{sub}».")
        for sub in campos:
            if sub not in objeto:
                errores.append(f"Falta «{clave}.{sub}».")
            elif not _entero(objeto[sub]):
                errores.append(f"«{clave}.{sub}» tiene que ser un entero mayor que 0.")
        if clave in _ORDENADOS:
            menor, mayor = _ORDENADOS[clave]
            if _entero(objeto.get(menor)) and _entero(objeto.get(mayor)) and objeto[menor] > objeto[mayor]:
                errores.append(f"«{clave}»: «{menor}» no puede ser mayor que «{mayor}».")
    for clave in _CUENTAS_CAMPOS:
        if clave in datos and datos[clave] not in CUENTAS:
            errores.append(f"«{clave}» tiene que ser «principal» o «secundaria».")
    porcentaje = datos.get("porcentaje_si_las_dos_cuentas_se_pausaron")
    if _entero(porcentaje) and porcentaje > 100:
        errores.append("«porcentaje_si_las_dos_cuentas_se_pausaron» tiene que estar entre 1 y 100.")
    hora, dia = datos.get("busquedas_por_hora"), datos.get("busquedas_por_dia")
    if _entero(hora) and _entero(dia) and dia < hora:
        errores.append("«busquedas_por_dia»: no pueden ser menos que las búsquedas por hora.")
    return errores


def leer_reglas() -> Reglas:
    """Las reglas del perfil. `ReglasInvalidas` con el motivo si el archivo falta o no es válido."""
    ruta = core.paths.DATA_DIR / "candidato" / ARCHIVO
    if not ruta.is_file():
        raise ReglasInvalidas(f"No existe {ARCHIVO} en el perfil: el Radar no lee nada hasta que se cargue.")
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReglasInvalidas(f"{ARCHIVO} no es un JSON válido: {exc}") from exc
    errores = validar_reglas(datos)
    if errores:
        raise ReglasInvalidas(f"{ARCHIVO}: " + " ".join(errores))
    d: Dict[str, Any] = datos
    return Reglas(
        busquedas_por_hora=d["busquedas_por_hora"],
        busquedas_por_dia=d["busquedas_por_dia"],
        porcentaje_dos_pausadas=d["porcentaje_si_las_dos_cuentas_se_pausaron"],
        pausa_entre_lecturas_s=(d["pausa_entre_lecturas_min"]["desde"] * 60.0,
                                d["pausa_entre_lecturas_min"]["hasta"] * 60.0),
        pausa_entre_vueltas_s=(d["pausa_entre_vueltas_min"]["desde"] * 60.0,
                               d["pausa_entre_vueltas_min"]["hasta"] * 60.0),
        busqueda_posts=d["lectura_busqueda"]["posts"],
        busqueda_scrolls=d["lectura_busqueda"]["scrolls"],
        busqueda_pantallas_sin_nuevos=d["lectura_busqueda"]["pantallas_sin_nuevos"],
        feed_posts=d["lectura_feed"]["posts"],
        feed_scrolls=d["lectura_feed"]["scrolls"],
        bloqueo_primera_h=d["pausa_bloqueo_horas"]["primera"],
        bloqueo_maxima_h=d["pausa_bloqueo_horas"]["maxima"],
        tope_envios_dia=d["tope_envios_dia"],
        cuenta_feed=d.get("cuenta_feed"),
        cuenta_busquedas=d.get("cuenta_busquedas"),
    )
