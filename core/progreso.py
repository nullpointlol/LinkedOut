"""Progreso de la generación de un CV del Radar (estructura JSON y reporte de pasos; sin I/O de DB).

La forma se guarda en `linkedin_posts.progreso`, viaja en el SSE `radar_progreso` y la lee la API:

    {"paso": "redactar", "etiqueta": "Redactando el CV", "sub": None, "pct": 15,
     "inicio": "2026-11-04T10:00:00", "inicio_paso": "2026-11-04T10:00:05", "activo_s": 312.0,
     "esperando_cuota": False, "cuota_renueva": None,
     "pasos": [{"paso": "preparar", "etiqueta": "...", "inicio": "...", "fin": "...", "duracion_s": 5.0}],
     "fin": None, "resultado": None}

Las funciones no modifican el dict recibido: devuelven uno nuevo.

Quien ejecuta el trabajo (el `RadarLoop`) instala un reportero con `usar_reportero(fn)`; el código del
pipeline (server.py) solo llama `await reportar("redactar")`, que no hace nada sin reportero (p. ej.
una postulación manual).
"""
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, Iterator, Optional, Tuple

logger = logging.getLogger(__name__)

PASOS: Tuple[Tuple[str, str], ...] = (
    ("preparar", "Preparando la postulación"),
    ("analizar", "Analizando la oferta"),
    ("redactar", "Redactando el CV"),
    ("auditar", "Revisando y corrigiendo el CV"),
    ("completar", "Completando puesto, CV, presentación y canales"),
    ("mensaje", "Redactando el mensaje"),
    ("completar_mensaje", "Revisando el mensaje"),
)
ETIQUETAS: Dict[str, str] = dict(PASOS)
PORCENTAJE: Dict[str, int] = {
    "preparar": 3, "analizar": 8, "redactar": 15, "auditar": 60,
    "completar": 85, "mensaje": 92, "completar_mensaje": 97,
}
RESULTADOS = ("LISTO", "ERROR", "cancelado", "vencido")


def _iso(ahora: datetime) -> str:
    return ahora.isoformat(timespec="seconds")


def _segundos(desde: Optional[str], hasta: datetime) -> Optional[float]:
    try:
        return round(max(0.0, (hasta - datetime.fromisoformat(str(desde))).total_seconds()), 1)
    except (TypeError, ValueError):
        return None


def _cerrar_paso_abierto(prog: Dict[str, Any], ahora: datetime) -> None:
    pasos = [dict(p) for p in prog.get("pasos") or [] if isinstance(p, dict)]
    if pasos and pasos[-1].get("fin") is None:
        pasos[-1]["fin"] = _iso(ahora)
        pasos[-1]["duracion_s"] = _segundos(pasos[-1].get("inicio"), ahora)
    prog["pasos"] = pasos


def nuevo(ahora: datetime) -> Dict[str, Any]:
    """Progreso de un trabajo que recién empieza (todavía sin paso)."""
    return {
        "paso": None, "etiqueta": None, "sub": None, "pct": 0,
        "inicio": _iso(ahora), "inicio_paso": None, "activo_s": 0.0,
        "esperando_cuota": False, "cuota_renueva": None,
        "pasos": [], "fin": None, "resultado": None,
    }


def avanzar(prog: Dict[str, Any], paso: str, ahora: datetime, sub: Optional[str] = None) -> Dict[str, Any]:
    """Cierra el paso abierto (con `fin` y `duracion_s`) y abre `paso`. Repetir el paso actual solo
    actualiza `sub`. `ValueError` si el paso no es uno de `PASOS`."""
    if paso not in ETIQUETAS:
        raise ValueError(f"Paso de progreso desconocido: {paso!r}")
    nuevo_prog = dict(prog)
    if prog.get("paso") == paso:
        nuevo_prog["sub"] = sub
        return nuevo_prog
    _cerrar_paso_abierto(nuevo_prog, ahora)
    nuevo_prog["pasos"].append({"paso": paso, "etiqueta": ETIQUETAS[paso], "inicio": _iso(ahora),
                                "fin": None, "duracion_s": None})
    nuevo_prog.update(paso=paso, etiqueta=ETIQUETAS[paso], sub=sub, pct=PORCENTAJE[paso],
                      inicio_paso=_iso(ahora))
    return nuevo_prog


def marcar_cuota(prog: Dict[str, Any], esperando: bool, renueva: Optional[str]) -> Dict[str, Any]:
    """El trabajo espera (o dejó de esperar) que vuelva la cuota de AGY."""
    return {**prog, "esperando_cuota": bool(esperando), "cuota_renueva": renueva if esperando else None}


def sumar_activo(prog: Dict[str, Any], segundos: float) -> Dict[str, Any]:
    """Suma tiempo de trabajo efectivo (sin la espera de cuota)."""
    try:
        previo = float(prog.get("activo_s") or 0.0)
    except (TypeError, ValueError):
        previo = 0.0
    return {**prog, "activo_s": round(previo + max(0.0, segundos), 3)}


def terminar(prog: Dict[str, Any], resultado: str, ahora: datetime) -> Dict[str, Any]:
    """Cierra el trabajo: `resultado` ∈ RESULTADOS."""
    if resultado not in RESULTADOS:
        raise ValueError(f"Resultado de progreso desconocido: {resultado!r}")
    nuevo_prog = dict(prog)
    _cerrar_paso_abierto(nuevo_prog, ahora)
    nuevo_prog.update(fin=_iso(ahora), resultado=resultado, esperando_cuota=False, cuota_renueva=None)
    if resultado == "LISTO":
        nuevo_prog["pct"] = 100
    return nuevo_prog


# ------------------------------------------------------------------ reporte de pasos

Reportero = Callable[[str, Optional[str]], Awaitable[None]]
_REPORTERO: ContextVar[Optional[Reportero]] = ContextVar("reportero_progreso", default=None)


async def reportar(paso: str, sub: Optional[str] = None) -> None:
    """Informa el paso al reportero del contexto (no-op si no hay). Nunca rompe el trabajo."""
    fn = _REPORTERO.get()
    if fn is None:
        return
    try:
        await fn(paso, sub)
    except Exception:
        logger.exception(f"[Progreso] No se pudo informar el paso {paso!r}")


@contextmanager
def usar_reportero(fn: Reportero) -> Iterator[None]:
    """Instala `fn` como reportero de los `reportar(...)` de este contexto async."""
    token = _REPORTERO.set(fn)
    try:
        yield
    finally:
        _REPORTERO.reset(token)
