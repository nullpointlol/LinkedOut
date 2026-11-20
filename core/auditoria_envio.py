"""Dictamen de la auditoría del CV visto desde el envío: si no aprobó y por qué, en pocas palabras.

La lista y el detalle del Radar muestran «Revisar» con estos motivos, así el usuario se entera antes de tocar
Enviar. Sale de `deducciones` («-15 pts en Redacción: … («frase») …») y de
`hallazgos_unificados.lista_observaciones`, sin IA.
"""
import re
from typing import Any, Dict, List, Optional

MAX_MOTIVOS = 3
LARGO_MOTIVO = 90
MOTIVO_GENERICO = "La auditoría no aprobó el CV"

_RE_DEDUCCION = re.compile(r"^\s*-?\d+(?:[.,]\d+)?\s*pts?\s+en\s+([^:]+):\s*(.*)$", re.IGNORECASE | re.DOTALL)
_RE_CITA = re.compile(r"«[^»]+»")


def no_aprobada(reporte: Optional[Dict[str, Any]]) -> bool:
    return isinstance(reporte, dict) and reporte.get("approved") is False


def _corto(texto: str) -> str:
    texto = " ".join(str(texto).split()).rstrip(".")
    return texto if len(texto) <= LARGO_MOTIVO else texto[:LARGO_MOTIVO - 1].rstrip() + "…"


def motivos_cortos(reporte: Optional[Dict[str, Any]]) -> List[str]:
    """Hasta MAX_MOTIVOS motivos de una línea: «Categoría: «frase citada»» o la observación recortada."""
    if not no_aprobada(reporte):
        return []
    motivos: List[str] = []
    citas: List[str] = []
    for deduccion in reporte.get("deducciones") or []:
        m = _RE_DEDUCCION.match(str(deduccion))
        if not m:
            continue
        cita = _RE_CITA.search(m.group(2))
        if cita:
            citas.append(cita.group(0))
        motivos.append(_corto(f"{m.group(1).strip()}: {cita.group(0) if cita else m.group(2)}"))
    observaciones = (reporte.get("hallazgos_unificados") or {}).get("lista_observaciones") or []
    for obs in observaciones:
        if not any(c in str(obs) for c in citas):
            motivos.append(_corto(obs))
    motivos = list(dict.fromkeys(m for m in motivos if m))
    return motivos[:MAX_MOTIVOS] or [MOTIVO_GENERICO]
