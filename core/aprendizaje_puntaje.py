"""Aprendizaje de las ofertas con puntaje bajo: qué tienen en común las ofertas cuyo análisis dio por
debajo del umbral y NO tienen las demás (palabras del puesto, brechas reales y palabras del aviso). Solo informa: no
descarta nada. La configuración vive en el perfil (`aprendizaje_puntaje.json`): el código no trae valores."""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

ARCHIVO = "aprendizaje_puntaje.json"
# Un término cuenta si es al menos tantas veces más frecuente en las de puntaje bajo que en el resto
RAZON_MINIMA = 2.0
MAX_TERMINOS = 40
EJEMPLOS = 2
ORIGENES = ("puesto", "brecha", "aviso")
_STOP = set("""para como este esta estos estas pero sobre entre desde hasta cada donde cuando todo toda todos todas
tiene tener tengas tenes buscamos busca buscando estamos somos sera será empresa equipo trabajo puesto perfil
experiencia conocimientos requisitos excluyente deseable sumate postulate enviar envianos interesados interesadas
with that this from your have will that para what more about their they them into also años anos year years
hiring looking role team work company position""".split())


class ConfigInvalida(ValueError):
    pass


def validar(dato: Any) -> List[str]:
    """Errores de `aprendizaje_puntaje.json`: {"umbral": 0-100, "min_ofertas": entero >= 2}."""
    if not isinstance(dato, dict):
        return ["Tiene que ser un objeto con «umbral» y «min_ofertas»."]
    errores = [f"Campo desconocido: «{k}»." for k in dato if k not in ("umbral", "min_ofertas")]
    umbral, minimo = dato.get("umbral"), dato.get("min_ofertas")
    if isinstance(umbral, bool) or not isinstance(umbral, (int, float)) or not 0 < umbral < 100:
        errores.append("«umbral» tiene que ser un número entre 0 y 100.")
    if isinstance(minimo, bool) or not isinstance(minimo, int) or minimo < 2:
        errores.append("«min_ofertas» tiene que ser un entero de 2 o más.")
    return errores


def _norm(texto: str) -> str:
    sin = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    return " ".join(sin.casefold().split())


def _palabras(texto: str) -> Set[str]:
    return {p for p in re.findall(r"[a-z0-9+#]{4,}", _norm(texto)) if p not in _STOP and not p.isdigit()}


def terminos_de(oferta: Dict[str, Any]) -> Set[Tuple[str, str]]:
    """{(origen, término)} de una oferta: las palabras del puesto, cada brecha real entera y las palabras del aviso."""
    salida = {("puesto", p) for p in _palabras(oferta.get("puesto") or "")}
    salida |= {("brecha", _norm(b)) for b in oferta.get("brechas") or [] if _norm(b)}
    salida |= {("aviso", p) for p in _palabras(oferta.get("texto") or "")}
    return salida


def ofertas_de_posts(posts: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Las ofertas analizadas (con puntaje), con lo que el aprendizaje mira de cada una."""
    salida = []
    for p in posts:
        analisis = p.get("analisis") if isinstance(p.get("analisis"), dict) else {}
        try:
            puntaje = float(analisis.get("match_score"))
        except (TypeError, ValueError):
            continue
        brechas = [str(r.get("nombre") or "") for r in analisis.get("requisitos") or []
                   if isinstance(r, dict) and r.get("estado") == "BRECHA_REAL"]
        salida.append({"id": p.get("id"), "puntaje": puntaje, "puesto": analisis.get("puesto") or "",
                       "brechas": brechas, "texto": p.get("texto") or ""})
    return salida


def senales(ofertas: List[Dict[str, Any]], umbral: float, min_ofertas: int) -> Dict[str, Any]:
    """Los términos típicos de las ofertas con puntaje menor que `umbral`: aparecen en al menos `min_ofertas` de ellas
    y son RAZON_MINIMA veces más frecuentes ahí que en el resto. Ordenados por esa razón."""
    bajas = [o for o in ofertas if o["puntaje"] < umbral]
    resto = [o for o in ofertas if o["puntaje"] >= umbral]
    en_bajas: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for o in bajas:
        for t in terminos_de(o):
            en_bajas.setdefault(t, []).append(o)
    en_resto: Dict[Tuple[str, str], int] = {}
    for o in resto:
        for t in terminos_de(o):
            en_resto[t] = en_resto.get(t, 0) + 1
    lista = []
    for (origen, termino), con in en_bajas.items():
        nb, nr = len(con), en_resto.get((origen, termino), 0)
        if nb < min_ofertas:
            continue
        razon = (nb / len(bajas)) / ((nr + 1) / (len(resto) + 1))
        if razon < RAZON_MINIMA:
            continue
        ejemplos = [o["puesto"] for o in sorted(con, key=lambda o: o["puntaje"]) if o["puesto"]][:EJEMPLOS]
        lista.append({"termino": termino, "origen": origen, "bajas": nb, "resto": nr, "razon": round(razon, 1),
                      "ejemplos": ejemplos})
    lista.sort(key=lambda x: (-x["razon"], -x["bajas"], x["termino"]))
    return {"umbral": umbral, "bajas": len(bajas), "resto": len(resto), "terminos": lista[:MAX_TERMINOS]}


def para_perfil(config: Optional[Dict[str, Any]], posts: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Lo que muestra la tarjeta de Mi perfil. Sin configuración válida, solo el error."""
    errores = validar(config) if config is not None else ["Falta configurar el umbral y el mínimo de ofertas."]
    if errores:
        return {"error": " ".join(errores)}
    return senales(ofertas_de_posts(posts), float(config["umbral"]), int(config["min_ofertas"]))
