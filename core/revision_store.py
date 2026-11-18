"""Revisión pendiente: lo que el sistema completó o corrigió sin datos suficientes (tabla `revision_pendiente`).

Nada sale con campos a rellenar: cuando falta un dato (pretensión salarial, nombre del reclutador…)
se completa con un valor razonable y se registra acá; también las contradicciones corregidas y los
CVs de respaldo. El usuario lo revisa desde el panel del Radar y lo marca como visto.
"""
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from core import database

TIPOS = ("dato_inventado", "contradiccion", "placeholder", "fallback", "senal_ia")
MAX_TEXTO = 1000
PREFIJO_SIN_CORREGIR = "No se pudo corregir"
# `revisado`: 0 pendiente, 1 lo marcó el usuario, 2 se cerró solo porque se corrigió (si reaparece, se reabre)
REVISADO_AUTO = 2
# Regla de armado del puesto objetivo (texto de prompt, no para el usuario): los ítems viejos la traen pegada al motivo
_RE_REGLA_PUESTO = re.compile(r"\.?\s*La línea bajo el nombre.*", re.DOTALL)


def limpiar_detalle(detalle: Any) -> str:
    """Saca del detalle el texto de prompt que se coló: queda solo el motivo."""
    return _RE_REGLA_PUESTO.sub("", str(detalle or "")).strip()


def init_tabla(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS revision_pendiente (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            postulacion_id INTEGER,
            post_id INTEGER,
            tipo TEXT NOT NULL,
            campo TEXT NOT NULL DEFAULT '',
            valor_usado TEXT NOT NULL DEFAULT '',
            detalle TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            revisado INTEGER NOT NULL DEFAULT 0
        );
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_revision_pendiente ON revision_pendiente(revisado, id);")


def _connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    conn = database.get_db_connection(db_path)
    try:
        with conn:
            init_tabla(conn)
    except Exception:
        conn.close()
        raise
    return conn


def registrar(tipo: str, campo: str = "", valor_usado: str = "", detalle: str = "", *,
              postulacion_id: Optional[int] = None, post_id: Optional[int] = None,
              db_path: Optional[Path] = None) -> int:
    """Registra un ítem a revisar. El mismo (postulación, post, tipo, campo, valor) no se duplica."""
    if tipo not in TIPOS:
        raise ValueError(f"Tipo de revisión desconocido: '{tipo}'. Válidos: {list(TIPOS)}")
    valores = (postulacion_id, post_id, tipo, str(campo)[:200], str(valor_usado)[:MAX_TEXTO])
    conn = _connect(db_path)
    try:
        with conn:
            fila = conn.execute(
                "SELECT id, revisado FROM revision_pendiente WHERE postulacion_id IS ? AND post_id IS ? AND tipo = ? "
                "AND campo = ? AND valor_usado = ?", valores).fetchone()
            if fila:
                if fila[1] == REVISADO_AUTO:
                    # Se había cerrado por «corregido», pero sigue estando: vuelve a pendiente
                    conn.execute("UPDATE revision_pendiente SET revisado = 0, detalle = ? WHERE id = ?",
                                 (limpiar_detalle(detalle)[:MAX_TEXTO], fila[0]))
                return int(fila[0])
            cur = conn.execute(
                "INSERT INTO revision_pendiente (postulacion_id, post_id, tipo, campo, valor_usado, detalle) "
                "VALUES (?, ?, ?, ?, ?, ?)", (*valores, limpiar_detalle(detalle)[:MAX_TEXTO]))
            return int(cur.lastrowid)
    finally:
        conn.close()


def listar(pendientes: bool = True, limit: int = 200, db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    conn = _connect(db_path)
    try:
        filtro = "WHERE revisado = 0" if pendientes else ""
        filas = conn.execute(
            f"SELECT * FROM revision_pendiente {filtro} ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
    finally:
        conn.close()
    items = [dict(f) for f in filas]
    for it in items:
        it["detalle"] = limpiar_detalle(it["detalle"])
        it["frase"] = frase_revision(it)
    return items


# ---------------------------------------------------------------- la frase del panel (creador, 15/11)
# «Revisá X porque Y»: qué revisar y por qué, en una frase breve, sin el texto completo ni el detalle técnico.

# Por qué, según el detalle técnico de la señal (la primera que aplica)
_PORQUE_SENAL = (
    (r"impacto vago", "promete un resultado sin un dato que lo respalde"),
    (r"\braya\b", "tiene un guion largo"),
    (r"Score ATS|keywords aprobadas faltantes|falta la keyword", "le faltan palabras clave de la oferta"),
    (r"tiene \d+ palabras \(m[aá]ximo", "es más largo de lo permitido"),
    (r"t[eé]rmino concreto de la evidencia", "no menciona nada concreto de tu experiencia"),
    (r"nivel declarado|nivel de ingl[eé]s", "el nivel de inglés no coincide con tu perfil"),
    (r"fall[oó] la reescritura", "la corrección automática falló"),
    (r"apertura formulaica|cierre formulaico", "el perfil arranca con una fórmula típica de IA"),
    (r"simetr[ií]a sint[aá]ctica", "varias viñetas terminan con la misma estructura"),
    (r"repetici[oó]n cercana", "repite palabras en frases cercanas"),
    (r"exceso de negritas", "tiene demasiadas negritas"),
    (r"inconsistencia de voz", "mezcla la primera y la tercera persona"),
    (r"resumen profesional debe ser", "el resumen no tiene el formato pedido"),
    (r"campo sin completar", "quedó un campo sin completar"),
    (r"cambi[oó] al sanearlo|volv[eé] a aprobar", "cambió después de que lo aprobaste"),
    (r"auditor[ií]a del CV no aprob", "la auditoría del CV no lo aprobó"),
    (r"clich[eé]s|muletillas|frase de IA|lenguaje (?:inflado|promocional)", "tiene frases típicas de IA"),
)


def _objeto(it: Dict[str, Any]) -> str:
    """Qué hay que revisar: el CV, el mensaje, el título del CV…"""
    campo = str(it.get("campo") or "").strip().lower()
    detalle = str(it.get("detalle") or "")
    if it.get("tipo") == "contradiccion":
        if campo == "puesto objetivo":
            return "el título del CV"
        if campo == "nivel de inglés":
            return "el nivel de inglés del CV"
        return "una viñeta del CV" if len(campo) > 40 else "el CV"
    if it.get("tipo") == "dato_inventado":
        if re.search(r"en el (?:mensaje|pitch)", detalle):
            return "el mensaje"
        return "el CV"
    if campo == "envío":
        return "el envío"
    if campo.startswith(("mensaje", "pitch", "body", "subject")) or re.match(r"(?:Revisá el mensaje|body|pitch):", detalle):
        return "el mensaje"
    return "el CV"


def _porque(it: Dict[str, Any]) -> str:
    tipo, campo = it.get("tipo"), str(it.get("campo") or "").strip()
    detalle = str(it.get("detalle") or "")
    sin_corregir = detalle.startswith(PREFIJO_SIN_CORREGIR)
    if tipo == "dato_inventado":
        if re.search(r"pretensi[oó]n|salari|remuneraci", campo, re.IGNORECASE):
            return "contiene una remuneración estimada"
        if re.search(r"nombre", campo, re.IGNORECASE):
            return "se completó el nombre del destinatario sin tenerlo"
        return f"se completó «{campo}» sin un dato de tu perfil" if campo and campo != "todo" \
            else "se completó un dato que no está en tu perfil"
    if tipo == "placeholder":
        return "quedó un campo sin completar"
    if tipo == "fallback":
        return "se usó el CV de respaldo"
    if tipo == "contradiccion":
        if campo.lower() == "puesto objetivo":
            return "no cumple las reglas y no se pudo corregir" if sin_corregir \
                else "se corrigió para que coincida con tu perfil"
        if campo.lower() == "nivel de inglés":
            return "no coincide con el de tu perfil y no se pudo corregir" if sin_corregir \
                else "se corrigió para que coincida con tu perfil"
        return "contradecía tu perfil y no se pudo corregir" if sin_corregir else "contradecía tu perfil y se corrigió"
    for patron, porque in _PORQUE_SENAL:
        if re.search(patron, detalle, re.IGNORECASE):
            return porque
    return "tiene una señal de redacción artificial"


# Señales que no son de redacción: el objeto es el dato, no «la redacción del CV»
_PORQUE_DE_DATO = {
    "el nivel de inglés no coincide con tu perfil": ("el nivel de inglés del CV", "no coincide con el de tu perfil"),
    "quedó un campo sin completar": (None, "quedó un campo sin completar"),
    "cambió después de que lo aprobaste": (None, "cambió después de que lo aprobaste"),
    "la auditoría del CV no lo aprobó": (None, "la auditoría del CV no lo aprobó"),
}


def frase_revision(it: Dict[str, Any]) -> str:
    """Una frase breve: «Revisá <qué> porque <por qué>.»"""
    objeto, porque = _objeto(it), _porque(it)
    if it.get("tipo") == "senal_ia":
        if porque in _PORQUE_DE_DATO:
            otro, porque = _PORQUE_DE_DATO[porque]
            objeto = otro or objeto
        elif objeto in ("el CV", "el mensaje"):
            objeto = "la redacción del " + objeto[3:]
    return f"Revisá {objeto} porque {porque}."


def resolver_anteriores(postulacion_id: Optional[int], campo: str, db_path: Optional[Path] = None) -> int:
    """Cierra los «No se pudo corregir» pendientes de ese (postulación, campo): ya se corrigió.
    Si el chequeo final la vuelve a encontrar, `registrar` los reabre. Devuelve cuántos cerró."""
    if postulacion_id is None:
        return 0
    conn = _connect(db_path)
    try:
        with conn:
            cur = conn.execute(
                "UPDATE revision_pendiente SET revisado = ? WHERE revisado = 0 AND postulacion_id = ? "
                "AND tipo = 'contradiccion' AND campo = ? AND detalle LIKE ?",
                (REVISADO_AUTO, int(postulacion_id), str(campo)[:200], PREFIJO_SIN_CORREGIR + "%"))
        return cur.rowcount
    finally:
        conn.close()


def contar_pendientes(db_path: Optional[Path] = None) -> int:
    conn = _connect(db_path)
    try:
        return int(conn.execute("SELECT COUNT(*) FROM revision_pendiente WHERE revisado = 0").fetchone()[0])
    finally:
        conn.close()


def marcar_revisado(item_id: int, db_path: Optional[Path] = None) -> bool:
    conn = _connect(db_path)
    try:
        with conn:
            cur = conn.execute("UPDATE revision_pendiente SET revisado = 1 WHERE id = ?", (int(item_id),))
        return cur.rowcount > 0
    finally:
        conn.close()
