"""Colas dinámicas de generación de CV (creador, 08/11).

Todo se analiza primero; una oferta elegible queda ANALIZADA con su lugar calculado y recién se toma
cuando el generador queda libre. El orden se recalcula en cada turno, así que una oferta mejor (más
reciente, con email, mejor rol o más score) pasa adelante sola mientras la otra no empezó.

- Prioritaria: lo forzado a mano, o elegible (score > 70, abierta, captura suficiente) de grupo de rol
  a o b publicada hace DIAS_PRIORITARIA días o menos.
  Orden: forzada → más reciente (por día) → email antes que link → grupo a antes que b → score.
- De fondo: absolutamente todo lo demás que espera CV (creador, 09/11), sin umbral de afinidad ni de
  antigüedad. Solo corre si no hay ninguna prioritaria y la cola de fondo está activa. Orden:
  1. afinidad > 70 (grupo C o con más de DIAS_PRIORITARIA días): más reciente → email → score;
  2. afinidad de 70 o menos: más afinidad → más reciente → email;
  3. al final, cerradas o con captura incompleta: más afinidad → más reciente.

La antigüedad es la de publicación: `created_at` (cuándo lo leyó el Radar) menos `fecha_rel`
(«3 h», «2 días», «1 sem») que mostraba LinkedIn en ese momento.
Un análisis no_apto con motivos queda fuera de ambas colas, salvo que se fuerce a mano.
"""
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from core.prioridades import aviso_util, elegible_automatico, grupo_rol, memo_captura, puesto_util, tiene_email

DIAS_PRIORITARIA = 14  # creador, 08/11 (antes 5)
VISIBLES_PRIORITARIA = 3
# Temporal (creador, 09/11): lo publicado hace un mes o más se guarda como IGNORADA para validar que la
# antigüedad se lee bien; no se analiza ni entra a las colas.
DIAS_IGNORADA = 30

PRIORITARIA = "prioritaria"
FONDO = "fondo"

_UNIDADES = (
    (re.compile(r"^(?:s|seg|segundos?|sec|seconds?)$"), timedelta(seconds=1)),
    (re.compile(r"^(?:m|min|mins|minutos?|minutes?)$"), timedelta(minutes=1)),
    (re.compile(r"^(?:h|hr|hrs|horas?|hours?)$"), timedelta(hours=1)),
    (re.compile(r"^(?:d|dias?|días?|days?)$"), timedelta(days=1)),
    (re.compile(r"^(?:sem|w|semanas?|weeks?)$"), timedelta(weeks=1)),
    (re.compile(r"^(?:mes|meses|mo|months?)$"), timedelta(days=30)),
    (re.compile(r"^(?:a|años?|anos?|y|yr|yrs|years?)$"), timedelta(days=365)),
)
_RE_REL = re.compile(r"(\d+)\s*([^\d\s•·.]+)")


def _delta_relativo(fecha_rel: Any) -> Optional[timedelta]:
    if not isinstance(fecha_rel, str):
        return None
    m = _RE_REL.search(fecha_rel.strip().lower())
    if not m:
        return None
    for patron, unidad in _UNIDADES:
        if patron.match(m.group(2)):
            return unidad * int(m.group(1))
    return None


def es_antigua(fecha_rel: Any) -> bool:
    """¿LinkedIn la mostraba como publicada hace DIAS_IGNORADA días o más («1 mes», «5 sem», «1 a»)?
    Sin fecha legible, no."""
    delta = _delta_relativo(fecha_rel)
    return delta is not None and delta >= timedelta(days=DIAS_IGNORADA)


def _leida_en(post: Dict[str, Any]) -> Optional[datetime]:
    try:
        creada = datetime.fromisoformat(str(post.get("created_at")))
    except (TypeError, ValueError, OverflowError):
        return None
    # CURRENT_TIMESTAMP de SQLite guarda UTC sin zona.
    return creada if creada.tzinfo else creada.replace(tzinfo=timezone.utc)


def publicada_en(post: Dict[str, Any]) -> Optional[datetime]:
    """Momento aproximado de publicación (UTC); sin `fecha_rel` legible, cuándo la leyó el Radar."""
    leida = _leida_en(post)
    if leida is None:
        return None
    delta = _delta_relativo(post.get("fecha_rel"))
    return leida - delta if delta else leida


def edad_dias(post: Dict[str, Any], ahora: Optional[datetime] = None) -> Optional[float]:
    publicada = publicada_en(post)
    if publicada is None:
        return None
    ahora = ahora or datetime.now(timezone.utc)
    return max(0.0, (ahora - publicada).total_seconds() / 86400)


def es_forzada(post: Dict[str, Any]) -> bool:
    return post.get("gen_prioridad") == 2 or post.get("fuente") == "manual"


def _score(post: Dict[str, Any]) -> float:
    analisis = post.get("analisis") if isinstance(post.get("analisis"), dict) else {}
    try:
        valor = float(analisis.get("match_score"))
    except (TypeError, ValueError):
        return 0.0
    return valor if math.isfinite(valor) else 0.0


def _canal_ord(post: Dict[str, Any]) -> int:
    if tiene_email(post):
        return 0
    links = post.get("links") if isinstance(post.get("links"), (list, tuple)) else []
    if post.get("canal") == "link" or any(isinstance(u, str) and u.strip() for u in links):
        return 1
    return 2


def nivel(post: Dict[str, Any], ahora: Optional[datetime] = None) -> Optional[str]:
    """PRIORITARIA o FONDO; no_apto queda fuera de ambas salvo fuerza manual."""
    if es_forzada(post):
        return PRIORITARIA
    analisis = post.get("analisis") if isinstance(post.get("analisis"), dict) else {}
    no_apto = analisis.get("no_apto")
    if isinstance(no_apto, dict) and no_apto.get("motivos"):
        return None
    if elegible_automatico(post) and grupo_rol(post) in ("a", "b"):
        edad = edad_dias(post, ahora)
        if edad is not None and edad <= DIAS_PRIORITARIA:
            return PRIORITARIA
    return FONDO


def _con_problema(post: Dict[str, Any]) -> bool:
    """Cerrada o con captura incompleta: va al final de la cola de fondo."""
    analisis = post.get("analisis") if isinstance(post.get("analisis"), dict) else {}
    return bool(analisis.get("oferta_cerrada")) or not aviso_util(post)


def clave(post: Dict[str, Any], tier: str, ahora: Optional[datetime] = None) -> tuple:
    edad = edad_dias(post, ahora)
    dia = int(edad) if edad is not None else 10 ** 6
    if tier == PRIORITARIA:
        rol = grupo_rol(post)
        return (0 if es_forzada(post) else 1, dia, _canal_ord(post), rol, -_score(post), int(post["id"]))
    if _con_problema(post):
        return (2, -_score(post), dia, _canal_ord(post), int(post["id"]))
    if elegible_automatico(post):
        return (0, dia, _canal_ord(post), -_score(post), int(post["id"]))
    return (1, -_score(post), dia, _canal_ord(post), int(post["id"]))


def armar_colas(posts: List[Dict[str, Any]], ahora: Optional[datetime] = None) -> Dict[str, List[Dict[str, Any]]]:
    """Reparte y ordena las candidatas; no muta la entrada."""
    ahora = ahora or datetime.now(timezone.utc)
    colas: Dict[str, List[Dict[str, Any]]] = {PRIORITARIA: [], FONDO: []}
    with memo_captura():
        for post in posts:
            tier = nivel(post, ahora)
            if tier:
                colas[tier].append(post)
        for tier, lista in colas.items():
            lista.sort(key=lambda p: clave(p, tier, ahora))
    return colas


def describir(post: Dict[str, Any], tier: str, posicion: int, ahora: Optional[datetime] = None) -> Dict[str, Any]:
    """Datos de una candidata para el panel «Colas» y la tarjeta."""
    triage = post.get("triage") if isinstance(post.get("triage"), dict) else {}
    edad = edad_dias(post, ahora)
    canal = _canal_ord(post)
    return {
        "post_id": int(post["id"]),
        "nivel": tier,
        "posicion": posicion,
        "puesto": puesto_util(post, analisis_primero=True) or str(triage.get("puesto") or ""),
        "empresa": str(post.get("empresa") or triage.get("empresa") or ""),
        "canal": ("email", "link", "otro")[canal],
        "grupo": grupo_rol(post),
        "edad_dias": round(edad, 3) if edad is not None else None,
        "score": _score(post),
        "forzada": es_forzada(post),
    }


def posiciones(posts: List[Dict[str, Any]], ahora: Optional[datetime] = None) -> Dict[int, Dict[str, Any]]:
    """{post_id: {nivel, posicion (1..n), total}} para las tarjetas («Prioritaria, 2.º»)."""
    armadas = armar_colas(posts, ahora)
    return {int(p["id"]): {"nivel": tier, "posicion": i, "total": len(lista)}
            for tier, lista in armadas.items() for i, p in enumerate(lista, 1)}


def panel(posts: List[Dict[str, Any]], fondo_activo: bool, ahora: Optional[datetime] = None) -> Dict[str, Any]:
    """Bloque «Colas» de GET /api/radar/procesos: totales y las próximas de cada nivel."""
    ahora = ahora or datetime.now(timezone.utc)
    armadas = armar_colas(posts, ahora)
    return {
        "dias_prioritaria": DIAS_PRIORITARIA,
        PRIORITARIA: {"total": len(armadas[PRIORITARIA]),
                      "proximas": [describir(p, PRIORITARIA, i, ahora)
                                   for i, p in enumerate(armadas[PRIORITARIA][:VISIBLES_PRIORITARIA], 1)]},
        FONDO: {"activo": bool(fondo_activo), "total": len(armadas[FONDO]),
                "proximas": [describir(p, FONDO, i, ahora) for i, p in enumerate(armadas[FONDO][:3], 1)]},
    }
