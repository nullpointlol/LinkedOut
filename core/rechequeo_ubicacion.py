"""Re-chequeo de lo ya analizado con la regla «en inglés y sin ubicación compatible» (creador, 19/11).

Revisa las ofertas en LISTO y ANALIZADA con el perfil vigente. Una que no es apta pasa a IGNORADA con el motivo
y su estado de origen (`no_apto.desde`), igual que al analizar: se recupera con «Postular igual». Nada se borra.
No cambian las aprobadas (ya son una orden de enviar), las cargadas a mano ni las que el creador forzó: esas solo
quedan marcadas con la condición. Sin `aplicar`, solo cuenta y no escribe.
"""
import copy
from typing import Any, Dict, Optional

from core import descartes, radar_store
from core.prioridades import texto_capturado
from core.triage import _senal_argentina

ESTADOS_RECHEQUEO = ("LISTO", "ANALIZADA")


def rechequear(aplicar: bool = False, restricciones: Optional[dict] = None) -> Dict[str, Any]:
    """{"cambiarian": n, "ids": [...], "solo_condicion": n, "ids_condicion": [...], "aplicado": bool}."""
    restricciones = descartes.cargar_restricciones() if restricciones is None else restricciones
    resultado: Dict[str, Any] = {"aplicado": aplicar, "cambiarian": 0, "ids": [], "solo_condicion": 0,
                                 "ids_condicion": []}
    for estado in ESTADOS_RECHEQUEO:
        for post in radar_store.list_posts(estado=estado):
            analisis = post.get("analisis")
            if not isinstance(analisis, dict) or analisis.get("condicion_ubicacion"):
                continue  # sin análisis, o ya evaluada con esta regla
            nuevo = copy.deepcopy(analisis)
            # Una aprobada ya es la orden de enviar: como la manual, solo queda marcada
            manual = post.get("fuente") == radar_store.FUENTE_MANUAL or bool(post.get("aprobado_at"))
            descartes.aplicar_condicion_ubicacion(nuevo, texto_capturado(post), restricciones, manual=manual,
                                                  contacto_local=_senal_argentina(post))
            if not nuevo.get("condicion_ubicacion"):
                continue
            archiva = bool((nuevo.get("no_apto") or {}).get("motivos")) and not post.get("aprobado_at")
            if archiva:
                nuevo["no_apto"]["desde"] = estado
                resultado["cambiarian"] += 1
                resultado["ids"].append(post["id"])
            else:
                resultado["solo_condicion"] += 1
                resultado["ids_condicion"].append(post["id"])
            if aplicar:
                # Con la versión leída: si el Radar lo tocó mientras tanto, no se pisa
                radar_store.update_post(post["id"], updated_at_esperado=post.get("updated_at"), analisis=nuevo,
                                        **({"estado": "IGNORADA"} if archiva else {}))
    return resultado
