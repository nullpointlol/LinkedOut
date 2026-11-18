"""Resultados por búsqueda para Mi perfil (creador, 12/11): el promedio por lectura de lo que trajo cada consulta.

Cuenta solo las lecturas productivas (`radar_store.lecturas_productivas`): búsquedas del Radar real, de cualquier
cuenta, que terminaron de leerse, en los últimos `radar_store.DIAS_LECTURAS` días. El feed no cuenta y las
pruebas de sintaxis de la cuenta 2 no escriben en `radar_lecturas`. El historial se asocia por consulta exacta:
si el usuario cambia la consulta, es otra búsqueda y no muestra nada hasta que el Radar la lea.

Se calcula al pedirlo (los estados avanzan: las ofertas y los matches suben a medida que se procesa). Una consulta
sin lecturas productivas no aparece: nunca se muestra un dato que no se midió.
"""
import logging
from typing import Any, Dict, Iterable, List, Optional

from core import radar_store
from core.radar_consultas import cargar_busquedas, errores_del_archivo

logger = logging.getLogger(__name__)

# Estados de un post que ya no es una oferta en curso de proceso
_DESCARTADOS = ("DESCARTADO", "IGNORADA")


def _clasificar(destino: str, post: Optional[Dict[str, Any]], umbral: Optional[float]) -> List[str]:
    """Las métricas que suma un post nuevo de una lectura."""
    if destino in ("exterior", "ignorada_vieja"):
        return ["descartados"]
    if post is None:
        return ["no_oferta"]  # el único borrado es el del clasificador: no era una oferta
    estado = post.get("estado") or ""
    if estado in _DESCARTADOS:
        return ["descartados"]
    if estado == "NUEVO" or (estado == "ERROR" and not post.get("clasificado")):
        return ["pendientes"]
    metricas = ["ofertas"]
    try:
        if umbral is not None and float(post.get("match_score")) >= float(umbral):
            metricas.append("matches")
    except (TypeError, ValueError):
        pass
    return metricas


def resultados_por_consulta(consultas: Iterable[str], db_path=None) -> Dict[str, Dict[str, Any]]:
    """{consulta: {"lecturas", "dias", "promedio": {...}}} de las consultas con lecturas productivas.

    Promedio por lectura, con un decimal: leidos, nuevos, ofertas, matches (afinidad ≥ el umbral configurado en
    el Radar), descartados, no_oferta; con_mail solo si alguna lectura lo midió (se promedia sobre esas) y
    pendientes solo si hay posts sin procesar."""
    lecturas = radar_store.lecturas_productivas(consultas, db_path=db_path)
    if not lecturas:
        return {}
    nuevos_por_lectura = [
        {u: d for u, d in lec["destinos"].items() if d in radar_store.DESTINOS_NUEVOS} for lec in lecturas
    ]
    posts = radar_store.estados_por_urn(
        (u for nuevos in nuevos_por_lectura for u in nuevos), db_path=db_path)
    umbral = radar_store.get_radar_state(db_path=db_path).get("umbral_match")

    sumas: Dict[str, Dict[str, Any]] = {}
    for lec, nuevos in zip(lecturas, nuevos_por_lectura):
        s = sumas.setdefault(lec["query"], {
            "lecturas": 0, "leidos": 0, "nuevos": 0, "ofertas": 0, "matches": 0, "descartados": 0,
            "no_oferta": 0, "pendientes": 0, "con_mail": 0, "con_mail_medidas": 0,
        })
        s["lecturas"] += 1
        s["leidos"] += lec["parseados"]
        s["nuevos"] += len(nuevos)
        if lec["con_mail"] is not None:
            s["con_mail"] += int(lec["con_mail"])
            s["con_mail_medidas"] += 1
        for urn, destino in nuevos.items():
            for metrica in _clasificar(destino, posts.get(urn), umbral):
                s[metrica] += 1

    resultado: Dict[str, Dict[str, Any]] = {}
    for consulta, s in sumas.items():
        n = s["lecturas"]
        promedio = {m: round(s[m] / n, 1)
                    for m in ("leidos", "nuevos", "ofertas", "matches", "descartados", "no_oferta")}
        if s["con_mail_medidas"]:
            promedio["con_mail"] = round(s["con_mail"] / s["con_mail_medidas"], 1)
        if s["pendientes"]:
            promedio["pendientes"] = round(s["pendientes"] / n, 1)
        resultado[consulta] = {"lecturas": n, "dias": radar_store.DIAS_LECTURAS, "promedio": promedio}
    return resultado


def busquedas_para_perfil() -> Dict[str, Any]:
    """Para Mi perfil: {"busquedas": [{nombre, activa, consulta, resultados?}], "errores": [...]}, en el
    orden del archivo. `resultados` solo está si la consulta tiene lecturas productivas; si la base falla, no
    está en ninguna (nunca un número inventado)."""
    busquedas = cargar_busquedas()
    try:
        resultados = resultados_por_consulta([b["consulta"] for b in busquedas])
    except Exception:
        logger.exception("[Mi perfil] No se pudieron calcular los resultados de las búsquedas")
        resultados = {}
    items = [dict(b, resultados=resultados[b["consulta"]]) if b["consulta"] in resultados else dict(b)
             for b in busquedas]
    # Portales (14/11): casillas y palabras de cada búsqueda, y la versión del archivo para guardarlas
    from core import perfil_candidato
    from core.radar_consultas import PORTALES, portales_de_busquedas
    de_portales = portales_de_busquedas()
    for item in items:
        item.update(de_portales.get(item["nombre"], {"portales": [], "palabras": ""}))
    return {"busquedas": items, "errores": errores_del_archivo(), "portales": list(PORTALES),
            "version": perfil_candidato.version_busquedas()}
