"""Corrección de un CV que la auditoría no aprobó: solo las líneas que marcó, sobre el CV actual.

No se regenera de cero ni se eligen viñetas de nuevo: el CV se lee línea por línea (perfil `P1`, viñetas de
experiencia `V1…`, habilidades `S1…`), la auditoría ubica cada hallazgo en su línea (`cv_maestro.ubicar`) y:
1. si propone una reescritura con su cita, se aplica tal cual (`auditor.aplicar_reescrituras`);
2. lo que queda marcado lo corrige la IA con `cv_maestro.pedido_correccion`, y de su respuesta solo se toman esas
   líneas. Un hallazgo «general» (sin línea) no habilita a tocar ninguna.
Las demás líneas del archivo quedan byte a byte iguales. Volver a auditar, el dictamen final y el PDF los hace quien
llama (`server.py`), que conoce el auditor y el CV final.
"""
import json
import re
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple

from core import cv_maestro
from core.auditor import aplicar_reescrituras
from core.cv_maestro import Bloque, Borrador, Hallazgo, Linea
from core.idioma import idioma_de_cv, seccion_canonica

_RE_VINETA = re.compile(r"^(\s*[•\-]\s+)(\S.*)$")
_RE_EMPRESA = re.compile(r"\|\s*([^|(]+?)\s*(?:\(|$)")


class SinCorreccion(Exception):
    """La auditoría no marcó ninguna línea que se pueda corregir (o la corrección no cambió nada)."""


def _empresa(encabezado: str) -> str:
    """«### Cargo | Empresa (2020 - 2024)» → «Empresa»."""
    m = _RE_EMPRESA.search(encabezado)
    return m.group(1).strip() if m else encabezado.lstrip("# ").strip()


def borrador_de(cv: str) -> Tuple[Borrador, Dict[str, Tuple[int, str]]]:
    """El CV como borrador del flujo del maestro, y dónde está cada línea editable: id → (renglón, prefijo)."""
    nombre, cabecera, educacion, idiomas = "", [], [], []
    perfil: Optional[Linea] = None
    bloques: List[Bloque] = []
    habilidades: List[Linea] = []
    ubicacion: Dict[str, Tuple[int, str]] = {}
    seccion: Optional[str] = None
    vinetas = 0
    for i, renglon in enumerate(cv.splitlines()):
        texto = renglon.strip()
        if renglon.startswith("# ") and not nombre and seccion is None:
            nombre = renglon[2:].strip()
            continue
        if renglon.startswith("## "):
            seccion = seccion_canonica(renglon[3:])
            continue
        if not texto:
            continue
        if seccion is None:
            cabecera.append(texto)
            continue
        vineta = _RE_VINETA.match(renglon)
        if seccion == "PERFIL" and perfil is None and not renglon.startswith("#"):
            perfil = Linea("P1", texto, texto)
            ubicacion["P1"] = (i, renglon[:len(renglon) - len(renglon.lstrip())])
        elif seccion == "EXPERIENCIA":
            if vineta:
                if not bloques:
                    bloques.append(Bloque("", "", []))
                vinetas += 1
                linea = Linea(f"V{vinetas}", vineta.group(2), vineta.group(2), orden=len(bloques[-1].vinetas))
                bloques[-1].vinetas.append(linea)
                ubicacion[linea.id] = (i, vineta.group(1))
            else:
                bloques.append(Bloque(_empresa(texto), renglon, []))
        elif seccion == "HABILIDADES" and vineta:
            linea = Linea(f"S{len(habilidades) + 1}", vineta.group(2), vineta.group(2))
            habilidades.append(linea)
            ubicacion[linea.id] = (i, vineta.group(1))
        elif seccion == "EDUCACION" and vineta:
            educacion.append(vineta.group(2))
        elif seccion == "IDIOMAS" and vineta:
            idiomas.append(vineta.group(2))
    b = Borrador(nombre=nombre, titulo=cabecera[0] if cabecera else "", contacto=" | ".join(cabecera[1:]),
                 perfil=perfil or Linea("P1", "", ""), experiencia=bloques, habilidades=habilidades,
                 educacion=educacion, idiomas=idiomas, idioma=idioma_de_cv(cv))
    return b, ubicacion


def reconstruir(cv: str, b: Borrador, ubicacion: Dict[str, Tuple[int, str]]) -> str:
    """El CV original con solo las líneas editables reemplazadas por su texto actual."""
    renglones = cv.splitlines()
    for lid, (i, prefijo) in ubicacion.items():
        linea = b.por_id(lid)
        if linea is not None and linea.texto != linea.original:
            renglones[i] = prefijo + linea.texto
    return "\n".join(renglones) + ("\n" if cv.endswith("\n") else "")


def _json(respuesta: Any) -> Dict[str, Any]:
    if isinstance(respuesta, str):
        try:
            respuesta = json.loads(respuesta)
        except ValueError:
            respuesta = None
    if not isinstance(respuesta, dict):
        raise RuntimeError("La IA no devolvió el JSON de la corrección")
    return respuesta


def lineas_marcadas(b: Borrador, ubicacion: Dict[str, Tuple[int, str]], reporte: Dict[str, Any]) -> List[Hallazgo]:
    """Los hallazgos de la auditoría que caen en una línea editable del CV."""
    return [h for h in cv_maestro.ubicar(b, reporte) if h.linea and h.linea in ubicacion]


async def corregir(cv: str, reporte: Dict[str, Any], keywords: List[str],
                   ia: Callable[[str], Awaitable[Any]]) -> Tuple[str, Set[str]]:
    """El CV con las líneas marcadas corregidas y los ids de las que cambiaron. `SinCorreccion` si no hay nada que
    tocar o la corrección no cambió ninguna línea."""
    b, ubicacion = borrador_de(cv)
    hallazgos = lineas_marcadas(b, ubicacion, reporte)
    if not hallazgos:
        raise SinCorreccion("La auditoría no marcó líneas del CV que se puedan corregir")
    pendientes = []
    for h in hallazgos:
        linea = b.por_id(h.linea)
        # Prevalece la auditoría: su reescritura, si la cita está en la línea, va tal cual
        if h.senal and h.senal.get("reescritura"):
            nuevo = aplicar_reescrituras(linea.texto, [h.senal])[0]
            if nuevo != linea.texto:
                linea.texto = nuevo
                continue
        pendientes.append(h)
    if pendientes:
        respuesta = _json(await ia(cv_maestro.pedido_correccion(b, pendientes, keywords)))
        permitidas = {h.linea for h in pendientes}
        cambios: Dict[str, Any] = {"lineas": [c for c in respuesta.get("lineas") or [] if isinstance(c, dict)
                                              and str(c.get("id") or "").strip() in permitidas]}
        if "P1" in permitidas:
            cambios["perfil"] = respuesta.get("perfil")
        cv_maestro.aplicar(b, cambios)
    cambiadas = {lid for lid in ubicacion if b.por_id(lid).texto != b.por_id(lid).original}
    if not cambiadas:
        raise SinCorreccion("La corrección no cambió ninguna línea del CV")
    return reconstruir(cv, b, ubicacion), cambiadas


def senales_de_lineas(cv: str, senales: List[Dict[str, Any]], lineas: Set[str]) -> List[Dict[str, Any]]:
    """Las señales de la revisión con IA que caen en `lineas` (las que marcó la auditoría anterior): el resto del CV
    ya lo aprobó y no cambió (la revisión no es determinista: volver a juzgarlo encontraba otras cosas cada vez)."""
    b, _ = borrador_de(cv)
    return [s for s in senales or [] if isinstance(s, dict)
            and cv_maestro._linea_de(b, str(s.get("frase") or "")) in lineas]
