"""Historial de procesos del Radar (Task 4: GET /api/radar/procesos).

Arma la respuesta agregando trabajos en curso, colas de generación y análisis,
estado del backlog, últimos terminados, topes diarios y estado de la cuota de AGY.
Sin estado propio: lee de `radar_store`, del `RadarLoop` y de `CUOTA`.
"""
from typing import Any, Dict, List, Optional, Set, Tuple

from core import colas, radar_store
from core.bridge import CUOTA, estado_admision
from core.radar import orden_generacion
from core.prioridades import clave_analisis


def clave_cola_proceso(p: Dict[str, Any]) -> tuple:
    """Criterio de orden de la cola de análisis: coincide con `RadarLoop._siguiente_trabajo`.

    Nivel 0 priorizado / 1 feed / 2 búsqueda; clasificar antes de analizar.
    Dentro de cada grupo usa la misma clave de contacto, rol y fecha que el motor.
    """
    nivel = 0 if p.get("prioridad") else (1 if p.get("fuente") == "feed" else 2)
    tipo_orden = 0 if p.get("estado") == "NUEVO" else 1
    return (nivel, tipo_orden, *clave_analisis(p))


def _armar_item(
    post: Dict[str, Any],
    tipo: str,
    posicion: Optional[int] = None,
    resultado: Optional[str] = None,
    duracion_s: Optional[float] = None,
    terminado_at: Optional[str] = None,
) -> Dict[str, Any]:
    triage = post.get("triage") if isinstance(post.get("triage"), dict) else {}
    prog = post.get("progreso") if isinstance(post.get("progreso"), dict) else None
    puesto = str(post.get("puesto") or triage.get("puesto") or "")
    if not puesto and tipo in ("analisis", "clasificacion"):
        autor = post.get("autor")
        if autor:
            puesto = f"Oferta de {autor}"
    return {
        "post_id": int(post["id"]),
        "puesto": puesto,
        "empresa": str(post.get("empresa") or triage.get("empresa") or ""),
        "estado": str(post.get("estado") or ""),
        "fuente": str(post.get("fuente") or ""),
        "tipo": tipo,
        "posicion": posicion,
        "progreso": prog,
        "resultado": resultado,
        "duracion_s": duracion_s,
        "terminado_at": terminado_at,
    }


def armar_procesos(radar: Any = None) -> Dict[str, Any]:
    """Construye la respuesta para `GET /api/radar/procesos`."""
    radar_state = radar_store.get_radar_state()
    generando_raw = getattr(radar, "generando_ids", None)
    if isinstance(generando_raw, (set, frozenset, list, tuple)) and all(
        type(pid) is int for pid in generando_raw
    ):
        generando_ids = set(generando_raw)
    else:
        generando_id = getattr(radar, "generando_ahora", None)
        generando_ids = {generando_id} if generando_id is not None else set()
    en_vuelo_raw = getattr(radar, "en_vuelo", None)
    en_vuelo_ids: Set[int] = set(en_vuelo_raw) if isinstance(en_vuelo_raw, (set, list, tuple)) else set()

    # 1. En curso: todas las generaciones y después ANALIZANDO + NUEVO en en_vuelo (hasta 3)
    en_curso: List[Dict[str, Any]] = []
    for pid in sorted(generando_ids):
        post_gen = radar_store.get_post(int(pid))
        if post_gen:
            en_curso.append(_armar_item(post_gen, tipo="generacion", posicion=None))

    en_vuelo_items: List[Dict[str, Any]] = []
    for pid in sorted(en_vuelo_ids):
        if pid in generando_ids:
            continue
        p = radar_store.get_post(pid)
        if p and p.get("estado") in ("ANALIZANDO", "NUEVO"):
            tipo = "clasificacion" if p["estado"] == "NUEVO" else "analisis"
            en_vuelo_items.append(_armar_item(p, tipo=tipo, posicion=None))
    en_curso.extend(en_vuelo_items[:3])

    # 2. Cola generación: orden_generacion(GENERANDO) sin el en curso, posicion 1..n
    posts_generando = radar_store.list_posts(estado="GENERANDO")
    posts_gen_ordenados = orden_generacion(posts_generando)
    cola_generacion: List[Dict[str, Any]] = []
    pos = 1
    for p in posts_gen_ordenados:
        if p["id"] in generando_ids:
            continue
        cola_generacion.append(_armar_item(p, tipo="generacion", posicion=pos))
        pos += 1

    # 3. Cola análisis: NUEVO/OFERTA no en vuelo, ordenada por clave_cola_proceso (máx. 50)
    nuevos = radar_store.list_posts(estado="NUEVO")
    ofertas = radar_store.list_posts(estado="OFERTA")
    pendientes_analisis = [p for p in (nuevos + ofertas)
                          if p["id"] not in en_vuelo_ids and p["id"] not in generando_ids]
    pendientes_analisis.sort(key=clave_cola_proceso)
    cola_analisis: List[Dict[str, Any]] = []
    for p in pendientes_analisis[:50]:
        tipo = "clasificacion" if p["estado"] == "NUEVO" else "analisis"
        cola_analisis.append(_armar_item(p, tipo=tipo, posicion=None))

    # 4. Backlog: activo, quedan, y los primeros 20 items de backlog_pendiente()
    backlog_activo = bool(radar_state.get("backlog_activo"))
    pendientes_backlog = [p for p in radar_store.backlog_pendiente() if p["id"] not in generando_ids]
    quedan_backlog = len(pendientes_backlog)
    items_backlog = [_armar_item(p, tipo="generacion", posicion=None) for p in pendientes_backlog[:20]]
    backlog = {
        "activo": backlog_activo,
        "quedan": quedan_backlog,
        "items": items_backlog,
    }

    # 4b. Colas dinámicas: prioritaria y de fondo, recalculadas en cada consulta
    colas_panel = colas.panel([p for p in radar_store.candidatas_cola() if p["id"] not in generando_ids],
                              backlog_activo)

    # 5. Terminados: últimos 10 posts con progreso.fin, más reciente primero, duracion_s = suma de pasos[].duracion_s
    conn = radar_store._connect()
    try:
        rows = conn.execute("SELECT * FROM linkedin_posts WHERE progreso IS NOT NULL").fetchall()
    finally:
        conn.close()

    terminados_candidatos: List[Tuple[str, Dict[str, Any]]] = []
    for row in rows:
        p = radar_store._post_row_to_dict(row)
        prog = p.get("progreso")
        if isinstance(prog, dict) and prog.get("fin"):
            terminados_candidatos.append((str(prog["fin"]), p))
    terminados_candidatos.sort(key=lambda x: x[0], reverse=True)

    terminados: List[Dict[str, Any]] = []
    for _, p in terminados_candidatos[:10]:
        prog = p["progreso"]
        pasos = prog.get("pasos") or []
        duracion_s: Optional[float] = None
        if pasos:
            duracion_s = round(sum(float(paso.get("duracion_s") or 0.0) for paso in pasos if isinstance(paso, dict)), 1)
        terminados.append(
            _armar_item(
                p,
                tipo="generacion",
                posicion=None,
                resultado=prog.get("resultado"),
                duracion_s=duracion_s,
                terminado_at=prog.get("fin"),
            )
        )

    # 6. Topes del día
    contadores = radar_state.get("contadores_dia") or {}
    # Los topes son del perfil (reglas_radar.json); sin reglas válidas no hay número que mostrar
    try:
        from core.reglas_radar import leer_reglas
        reglas = leer_reglas()
        tope_envios = reglas.tope_envios_dia
    except Exception:
        tope_envios = None
    topes = {
        # Posts nuevos del día (sin tope: )
        "leidos_busquedas": int(contadores.get("leidos_busquedas") or 0),
        "leidos_feed": int(contadores.get("leidos_feed") or 0),
        "enviados": int(contadores.get("enviados") or 0),
        "tope_envios": tope_envios,
        "postulados": int(contadores.get("postulados") or 0),
        "ofertas": int(contadores.get("match") or contadores.get("ofertas") or 0),
    }

    # 7. Cuota de AGY
    cuota_estado = CUOTA.estado()
    cuota = {
        "agotada": bool(cuota_estado.get("agotada")),
        "renueva": cuota_estado.get("renueva"),
        "reintento": cuota_estado.get("reintento"),
    }

    return {
        "en_curso": en_curso,
        "cola_generacion": cola_generacion,
        "cola_analisis": cola_analisis,
        "backlog": backlog,
        "colas": colas_panel,
        "errores": radar_store.errores_pendientes_por_tipo()[:8],
        "terminados": terminados,
        "topes": topes,
        "cuota": cuota,
        "agy": estado_admision(),
    }
