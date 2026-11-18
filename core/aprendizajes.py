import os
import re
import json
import logging
import tempfile
import threading
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from core.paths import DATA_DIR

logger = logging.getLogger(__name__)

DATA_DIR = DATA_DIR / "candidato"
APRENDIZAJES_FILE = DATA_DIR / "aprendizajes_candidato.json"
LOCK = threading.RLock()

def _ensure_dir():
    APRENDIZAJES_FILE.parent.mkdir(parents=True, exist_ok=True)

def obtener_aprendizajes() -> Dict[str, Dict[str, Any]]:
    """
    Retorna el diccionario de aprendizajes confirmados por el usuario.
    Clave normalizada en minúsculas.
    """
    _ensure_dir()
    if not APRENDIZAJES_FILE.exists():
        return {}
    try:
        with open(APRENDIZAJES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.error(f"Error al leer aprendizajes de {APRENDIZAJES_FILE}: {e}")
        return {}

def _guardar_aprendizajes(data: Dict[str, Dict[str, Any]]) -> bool:
    _ensure_dir()
    temp_file = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                         dir=APRENDIZAJES_FILE.parent,
                                         delete=False, suffix=".tmp") as f:
            temp_file = Path(f.name)
            json.dump(data, f, indent=2, ensure_ascii=False)
        # Path.replace usa os.replace; cerrar antes permite reemplazar en Windows.
        temp_file.replace(APRENDIZAJES_FILE)
        return True
    except Exception as e:
        logger.error(f"Error al guardar aprendizajes en {APRENDIZAJES_FILE}: {e}")
        if temp_file is not None:
            try:
                temp_file.unlink(missing_ok=True)
            except OSError as cleanup_error:
                logger.error(f"Error al borrar temporal {temp_file}: {cleanup_error}")
        return False

def _clave(texto: str) -> str:
    """Nombre normalizado para comparar requisitos: minúsculas, sin tildes, espacios simples."""
    texto = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode()
    return " ".join(texto.lower().split())

def registrar_aprendizaje(tag: str, estado: str, origen: str = "usuario", contexto: Optional[str] = None,
                         eliminatorio: Optional[bool] = None) -> Dict[str, Any]:
    """
    Registra o actualiza el estado de una competencia aprendida.
    Estados válidos: 'MATCH_DIRECTO', 'TRANSFERIBLE', 'BRECHA_REAL'.
    BRECHA_REAL se guarda con igual estructura que los otros estados.
    Cualquier decisión de descarte (true o false) sobrevive al ciclo de estados.
    """
    if not tag or not isinstance(tag, str):
        raise ValueError("Tag debe ser una cadena no vacía.")

    estado_upper = estado.upper().strip()
    if estado_upper not in ("MATCH_DIRECTO", "TRANSFERIBLE", "BRECHA_REAL"):
        raise ValueError(f"Estado desconocido: {estado}")

    tag_clean = tag.strip()
    tag_key = tag_clean.lower()

    with LOCK:
        aprendizajes = obtener_aprendizajes()
        existing_keys = [k for k, v in aprendizajes.items() if _clave(v.get("tag") or k) == _clave(tag_clean)]
        anterior = {}
        for k in existing_keys:
            if not anterior:
                anterior = aprendizajes[k]
            elif anterior.get("eliminatorio") is None and aprendizajes[k].get("eliminatorio") is not None:
                anterior = aprendizajes[k]

        decision = eliminatorio if eliminatorio is not None else anterior.get("eliminatorio")

        for k in existing_keys:
            if k != tag_key:
                del aprendizajes[k]

        now_iso = datetime.now(timezone.utc).isoformat()

        entry = {
            "tag": tag_clean,
            "estado": estado_upper,
            "origen": origen,
            "contexto": contexto if contexto is not None else anterior.get("contexto", ""),
            "updated_at": now_iso
        }
        if isinstance(decision, bool):
            entry["eliminatorio"] = decision
        aprendizajes[tag_key] = entry
        _guardar_aprendizajes(aprendizajes)
        return entry

# Palabras que no dicen el rubro: un descarte aprendido se compara por las otras
_GENERICAS = set(("experiencia experiencias conocimiento conocimientos manejo uso nivel capacidad habilidad habilidades "
                  "trabajo tareas gestion area areas rol puesto perfil requisito excluyente deseable excluyentes "
                  "minimo minima anos ano de del la las el los en con para por y o a al un una sus su "
                  "experience knowledge years year in with of and or the for to").split())


def _terminos(texto: str) -> set:
    palabras = re.findall(r"[a-z0-9]+", _clave(texto))
    return {p[:-1] if len(p) > 4 and p.endswith("s") else p for p in palabras if p not in _GENERICAS and len(p) > 2}


def _mismo_rubro(aprendido: set, propios: set) -> bool:
    """Comparte al menos dos términos del descarte aprendido (o el único que tiene) y la mayoría de ellos."""
    comunes = aprendido & propios
    return len(comunes) >= min(2, len(aprendido)) and len(comunes) * 5 >= len(aprendido) * 3


def aplicar_aprendizajes_a_analisis(analisis: Dict[str, Any]) -> Dict[str, Any]:
    """
    Actualiza la taxonomía de un análisis JSON existente basándose en
    los aprendizajes persistentes acumulados.
    """
    if not isinstance(analisis, dict):
        return analisis

    aprendizajes = obtener_aprendizajes()
    if not aprendizajes:
        return analisis

    coincidencias = list(analisis.get("coincidencias") or [])
    transferibles = list(analisis.get("transferibles") or [])
    brechas = list(analisis.get("brechas") or [])

    # Index map for existing items (lower -> original)
    def clean_and_map(lst):
        res = {}
        for item in lst:
            name = item if isinstance(item, str) else item.get("nombre", "")
            if name:
                res[name.strip().lower()] = item
        return res

    for key_lower, entry in aprendizajes.items():
        tag_original = entry.get("tag", key_lower)
        target_state = entry.get("estado")

        # Remove from all 3 lists first if present
        estaba_presente = any((item if isinstance(item, str) else item.get("nombre", "")).strip().lower() == key_lower
                              for item in (*coincidencias, *transferibles, *brechas))
        coincidencias = [c for c in coincidencias if (c if isinstance(c, str) else c.get("nombre", "")).strip().lower() != key_lower]
        transferibles = [t for t in transferibles if (t if isinstance(t, str) else t.get("nombre", "")).strip().lower() != key_lower]
        brechas = [b for b in brechas if (b if isinstance(b, str) else b.get("nombre", "")).strip().lower() != key_lower]

        # Insert into the target state
        if target_state == "MATCH_DIRECTO":
            coincidencias.append(tag_original)
        elif target_state == "TRANSFERIBLE":
            transferibles.append(tag_original)
        elif target_state == "BRECHA_REAL" and estaba_presente:
            brechas.append(tag_original)

    analisis["coincidencias"] = coincidencias
    analisis["transferibles"] = transferibles
    analisis["brechas"] = brechas
    return analisis


def aplicar_aprendizajes_a_requisitos(requisitos: Any) -> int:
    """Aplica a cada requisito con el mismo nombre (sin tildes ni mayúsculas) el estado que el
    usuario confirmó en otra postulación. Modifica la lista en el lugar; devuelve cuántos cambió."""
    if not isinstance(requisitos, list):
        return 0
    aprendidos = {_clave(e.get("tag") or k): e for k, e in obtener_aprendizajes().items()}
    descartes = [(_terminos(c), e) for c, e in aprendidos.items() if e.get("eliminatorio") is True and _terminos(c)]
    cambiados = 0
    for req in requisitos:
        if not isinstance(req, dict):
            continue
        c_req = _clave(req.get("nombre"))
        if not c_req:
            continue
        if c_req not in aprendidos:
            # «Me descarta» aprende el rubro, no solo el nombre exacto (creador, 15/11): una brecha que comparte
            # sus términos con un descarte aprendido también descarta. Solo marca el descarte, nunca el estado
            if req.get("estado") == "BRECHA_REAL" and req.get("eliminatorio_usuario") is None:
                propios = _terminos(f"{req.get('nombre') or ''} {req.get('texto') or ''}")
                if any(_mismo_rubro(t, propios) for t, _ in descartes):
                    req["eliminatorio"] = req["eliminatorio_usuario"] = True
                    cambiados += 1
            continue
        entrada = aprendidos[c_req]
        estado = entrada.get("estado")
        decision = entrada.get("eliminatorio")

        antes = dict(req)

        if isinstance(decision, bool):
            req["eliminatorio"] = decision
            req["eliminatorio_usuario"] = decision
            if not decision:
                req.pop("categoria_descarte", None)

        if estado in ("MATCH_DIRECTO", "TRANSFERIBLE", "BRECHA_REAL"):
            req["estado"] = estado
            req["approved"] = (estado != "BRECHA_REAL")
            req["aprendido"] = True

        if req != antes:
            cambiados += 1

    return cambiados


def aprendizajes_para_prompt() -> str:
    """Lista para el Estratega: lo que el usuario ya confirmó tener (vale para requisitos equivalentes)."""
    aprendizajes = obtener_aprendizajes()
    lineas = []
    for k, e in aprendizajes.items():
        decision = e.get("eliminatorio")
        if e.get("estado") in ("MATCH_DIRECTO", "TRANSFERIBLE", "BRECHA_REAL") or isinstance(decision, bool):
            linea = f"- {e.get('tag') or k}: {e.get('estado')}"
            if isinstance(decision, bool):
                linea += f"; eliminatorio: {str(decision).lower()}"
            lineas.append(linea)
    if any(isinstance(e.get("eliminatorio"), bool) for e in aprendizajes.values()):
        lineas.append("Aplicá la decisión de descarte también a redacciones equivalentes del mismo rubro "
                      "(gastronomía / rubro gastronómico), nunca a otros rubros. False explícito veta "
                      "el descarte; true no convierte un MATCH_DIRECTO o TRANSFERIBLE en BRECHA_REAL.")
    return "\n".join(lineas)
