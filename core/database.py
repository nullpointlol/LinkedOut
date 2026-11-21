"""Persistencia ultraliviana en SQLite WAL (data/postulaciones.db)."""
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, Union
from core.paths import DATA_DIR

BASE_DIR = Path(__file__).resolve().parent.parent
DB_DIR = DATA_DIR
DB_PATH = DB_DIR / "postulaciones.db"

# Estados de ciclo de vida permitidos
ALLOWED_ESTADOS = {"POR_POSTULAR", "POSTULADA", "EN_ENTREVISTA", "OFERTA", "DESESTIMADA"}


def format_days_elapsed(fecha_str: Optional[str]) -> str:
    """Calcula los días transcurridos desde la fecha de postulación."""
    if not fecha_str or not str(fecha_str).strip():
        return ""
    try:
        clean_str = str(fecha_str).strip().replace("T", " ")
        if len(clean_str) == 10:
            dt = datetime.strptime(clean_str, "%Y-%m-%d")
        else:
            dt = datetime.strptime(clean_str[:19], "%Y-%m-%d %H:%M:%S")
        delta = datetime.now() - dt
        days = delta.days
        if days <= 0:
            return "hoy"
        if days == 1:
            return "hace 1d"
        return f"hace {days}d"
    except Exception:
        return ""


def get_db_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    target = db_path or DB_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target))
    conn.row_factory = sqlite3.Row
    # Configurar WAL y timeouts para evitar bloqueos
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn


def init_db(db_path: Optional[Path] = None):
    conn = get_db_connection(db_path)
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS postulaciones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                titulo TEXT NOT NULL,
                empresa TEXT DEFAULT 'Confidencial',
                url TEXT DEFAULT '',
                contenido_oferta TEXT NOT NULL,
                dolores_empresa TEXT DEFAULT '[]',
                requisitos TEXT DEFAULT '[]',
                match_score REAL DEFAULT 0.0,
                cv_markdown TEXT DEFAULT '',
                pitch TEXT DEFAULT '',
                pitches_multicanal TEXT DEFAULT '{}',
                reporte_auditoria TEXT DEFAULT '{}',
                telemetry TEXT DEFAULT '{}',
                status TEXT DEFAULT 'PENDING',
                estado_postulacion TEXT DEFAULT 'POR_POSTULAR',
                fecha_postulacion TIMESTAMP,
                link_postulacion TEXT DEFAULT '',
                notas_seguimiento TEXT DEFAULT '',
                contacto_recruiter TEXT DEFAULT '',
                interview_prep TEXT DEFAULT '{}',
                networking_pitches TEXT DEFAULT '{}'
            );
        """)
        # Asegurar compatibilidad si la tabla ya existía sin las nuevas columnas (migraciones ad-hoc)
        for col_name, col_type in [
            ("telemetry", "TEXT DEFAULT '{}'"),
            ("analisis", "TEXT DEFAULT '{}'"),
            ("pitches_multicanal", "TEXT DEFAULT '{}'"),
            ("estado_postulacion", "TEXT DEFAULT 'POR_POSTULAR'"),
            ("fecha_postulacion", "TIMESTAMP"),
            ("link_postulacion", "TEXT DEFAULT ''"),
            ("notas_seguimiento", "TEXT DEFAULT ''"),
            ("contacto_recruiter", "TEXT DEFAULT ''"),
            ("raw_scraped_data", "TEXT DEFAULT '{}'"),
            ("interview_prep", "TEXT DEFAULT '{}'"),
            ("networking_pitches", "TEXT DEFAULT '{}'"),
            ("origen", "TEXT DEFAULT 'manual'"),
            # Post del Radar que originó la postulación (su `urn`): clave de reutilización del CV
            ("radar_post_ref", "TEXT DEFAULT ''"),
            # CV final (`core/cv_final.py`): el PDF auditado (ruta relativa a exports) y sus huellas SHA-256
            ("cv_pdf", "TEXT DEFAULT ''"),
            ("cv_pdf_sha256", "TEXT DEFAULT ''"),
            ("cv_md_sha256", "TEXT DEFAULT ''"),
        ]:
            try:
                conn.execute(f"ALTER TABLE postulaciones ADD COLUMN {col_name} {col_type};")
            except sqlite3.OperationalError:
                pass
        # Formato del CV: «completo» (sin límite de páginas, por defecto) o «una_pagina». Los CV
        # que ya existían al agregar la columna son de 1 página y quedan marcados así.
        try:
            conn.execute("ALTER TABLE postulaciones ADD COLUMN formato_cv TEXT DEFAULT 'completo';")
            conn.execute("UPDATE postulaciones SET formato_cv = 'una_pagina' "
                         "WHERE cv_markdown IS NOT NULL AND TRIM(cv_markdown) != '';")
        except sqlite3.OperationalError:
            pass
        # Tablas del Radar LinkedIn (import diferido: radar_store depende de este módulo)
        from core.radar_store import init_radar_tables, marcar_postuladas_enviadas
        init_radar_tables(conn)
        # Enviadas antes de que el envío marcara la postulación: quedan «Postulada» con su fecha
        marcar_postuladas_enviadas(conn)
    conn.close()


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    for json_col in ("dolores_empresa", "requisitos", "reporte_auditoria", "telemetry", "analisis", "pitches_multicanal", "raw_scraped_data", "interview_prep", "networking_pitches"):
        if json_col in d and isinstance(d[json_col], str):
            try:
                d[json_col] = json.loads(d[json_col])
            except Exception:
                pass
    d.setdefault("pitches_multicanal", {})
    if not isinstance(d.get("pitches_multicanal"), dict):
        d["pitches_multicanal"] = {}
    d.setdefault("interview_prep", {})
    if not isinstance(d.get("interview_prep"), dict):
        d["interview_prep"] = {}
    d.setdefault("networking_pitches", {})
    if not isinstance(d.get("networking_pitches"), dict):
        d["networking_pitches"] = {}
    # Valores por defecto para campos de ciclo de vida
    d.setdefault("estado_postulacion", "POR_POSTULAR")
    if d.get("estado_postulacion") is None:
        d["estado_postulacion"] = "POR_POSTULAR"
    d.setdefault("fecha_postulacion", None)
    d.setdefault("link_postulacion", "")
    d.setdefault("notas_seguimiento", "")
    d.setdefault("contacto_recruiter", "")
    if d.get("radar_post_ref") is None:
        d["radar_post_ref"] = ""
    return d


def create_postulacion(
    titulo: str,
    empresa: str,
    contenido: str,
    url: str = "",
    status: str = "PENDING",
    origen: str = "manual",
    db_path: Optional[Path] = None,
    radar_post_ref: str = "",
) -> int:
    """Crea una postulación. `origen` distingue las manuales de las generadas por el Radar ('linkedin');
    `radar_post_ref` identifica el post del Radar que la originó (su `urn`)."""
    conn = get_db_connection(db_path)
    with conn:
        cursor = conn.execute(
            """
            INSERT INTO postulaciones (titulo, empresa, contenido_oferta, url, status, origen, radar_post_ref)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (titulo, empresa, contenido, url, status, origen, radar_post_ref or "")
        )
        postulacion_id = cursor.lastrowid
    conn.close()
    return postulacion_id


# Mayor entero de SQLite: un id más grande no puede existir (y sqlite3 lanzaría OverflowError)
MAX_ID_SQLITE = 2**63 - 1


def _id_en_rango(postulacion_id: Any) -> bool:
    """False si el id es un entero fuera del rango de SQLite (esa fila no puede existir)."""
    return not isinstance(postulacion_id, int) or -MAX_ID_SQLITE - 1 <= postulacion_id <= MAX_ID_SQLITE


def get_postulacion(postulacion_id: int, db_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    if not _id_en_rango(postulacion_id):
        return None
    conn = get_db_connection(db_path)
    cursor = conn.execute("SELECT * FROM postulaciones WHERE id = ?", (postulacion_id,))
    row = cursor.fetchone()
    conn.close()
    return _row_to_dict(row) if row else None


def list_postulaciones(
    db_path: Optional[Union[Path, str]] = None,
    filtro_lifecycle: Optional[str] = None,
    origen: Optional[str] = "manual",
    sin_post: bool = False,
) -> List[Dict[str, Any]]:
    """Lista postulaciones. Por defecto solo las manuales; `origen=None` devuelve todas.
    `sin_post`: solo las que ningún post del Radar referencia (sin eso no se ven en la vista Radar)."""
    # Permitir que el primer argumento posicional sea el filtro de ciclo de vida si se pasa como str descriptivo
    target_db_path = db_path
    if isinstance(db_path, str) and (db_path.upper() in ALLOWED_ESTADOS or db_path.upper() in ("TODAS", "ALL", "EN_PROCESO", "FINALIZADAS")):
        filtro_lifecycle = db_path
        target_db_path = None

    conditions: List[str] = []
    params: List[Any] = []
    if filtro_lifecycle and filtro_lifecycle.upper() not in ("TODAS", "ALL"):
        filtro_upper = filtro_lifecycle.upper()
        if filtro_upper == "EN_PROCESO":
            conditions.append("estado_postulacion IN ('EN_ENTREVISTA', 'OFERTA')")
        elif filtro_upper == "FINALIZADAS":
            conditions.append("estado_postulacion IN ('DESESTIMADA')")
        else:
            conditions.append("estado_postulacion = ?")
            params.append(filtro_upper)
    if origen is not None:
        # COALESCE: filas anteriores a la migración se consideran manuales
        conditions.append("COALESCE(origen, 'manual') = ?")
        params.append(origen)
    if sin_post:
        conditions.append("NOT EXISTS (SELECT 1 FROM linkedin_posts lp WHERE lp.postulacion_id = postulaciones.id)")

    query = "SELECT * FROM postulaciones"
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY match_score DESC, id DESC"

    conn = get_db_connection(target_db_path)
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]


def update_postulacion_lifecycle(
    postulacion_id: int,
    estado: str,
    fecha: Optional[str] = None,
    link: Optional[str] = None,
    notas: Optional[str] = None,
    contacto: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """Actualiza el estado de ciclo de vida y seguimiento de una postulación."""
    estado_clean = str(estado).strip().upper() if estado else ""
    if estado_clean not in ALLOWED_ESTADOS:
        raise ValueError(f"Estado de postulación inválido: '{estado}'. Permitidos: {sorted(list(ALLOWED_ESTADOS))}")

    postulacion = get_postulacion(postulacion_id, db_path)
    if not postulacion:
        return None

    update_payload: Dict[str, Any] = {"estado_postulacion": estado_clean}

    if fecha is not None:
        update_payload["fecha_postulacion"] = fecha
    elif estado_clean == "POSTULADA" and not postulacion.get("fecha_postulacion"):
        update_payload["fecha_postulacion"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if link is not None:
        update_payload["link_postulacion"] = link
    if notas is not None:
        update_payload["notas_seguimiento"] = notas
    if contacto is not None:
        update_payload["contacto_recruiter"] = contacto

    update_postulacion(postulacion_id, db_path, **update_payload)
    return get_postulacion(postulacion_id, db_path)


def update_postulacion(postulacion_id: int, db_path: Optional[Path] = None, **kwargs):
    fields = []
    values = []
    for k, v in kwargs.items():
        if k in ("dolores_empresa", "requisitos", "reporte_auditoria", "telemetry", "analisis", "pitches_multicanal", "raw_scraped_data", "interview_prep", "networking_pitches") and isinstance(v, (list, dict)):
            v = json.dumps(v, ensure_ascii=False)
        fields.append(f"{k} = ?")
        values.append(v)

    if not fields:
        return

    values.append(postulacion_id)
    query = f"UPDATE postulaciones SET {', '.join(fields)} WHERE id = ?"

    conn = get_db_connection(db_path)
    with conn:
        anterior = conn.execute(
            "SELECT cv_markdown FROM postulaciones WHERE id = ?", (postulacion_id,)
        ).fetchone() if "cv_markdown" in kwargs else None
        cambia_cv = anterior is not None and anterior["cv_markdown"] != kwargs["cv_markdown"]
        conn.execute(query, values)
        if cambia_cv or kwargs.get("status") == "PROCESSING":
            # La aprobación cubre también el adjunto. Se invalida en la misma transacción
            # que modifica el CV, incluso si el envío ya estaba preparando el PDF.
            conn.execute(
                "UPDATE linkedin_posts SET aprobado_at = NULL, "
                "updated_at = strftime('%Y-%m-%d %H:%M:%f', 'now') "
                "WHERE postulacion_id = ? AND estado IN ('LISTO', 'ENVIANDO')",
                (postulacion_id,),
            )
    conn.close()


def toggle_tag(postulacion_id: int, tag_index: int, db_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    postulacion = get_postulacion(postulacion_id, db_path)
    if not postulacion:
        return None

    requisitos = postulacion.get("requisitos", [])
    if not 0 <= tag_index < len(requisitos):
        return None  # tag inexistente: no se toca la fila
    current_approved = requisitos[tag_index].get("approved", False)
    requisitos[tag_index]["approved"] = not current_approved

    from core.strategist import calculate_match_score_with_breakdown
    new_score, desglose = calculate_match_score_with_breakdown(requisitos)

    analisis = postulacion.get("analisis") or {}
    if isinstance(analisis, dict):
        analisis["match_score"] = new_score
        analisis["desglose_afinidad"] = desglose
        analisis["requisitos"] = requisitos

    update_postulacion(
        postulacion_id,
        db_path,
        requisitos=requisitos,
        match_score=new_score,
        analisis=analisis
    )

    return get_postulacion(postulacion_id, db_path)


def delete_postulacion(postulacion_id: int, db_path: Optional[Path] = None) -> bool:
    """Elimina definitivamente una postulación de la base de datos sin dejar rastro."""
    if not _id_en_rango(postulacion_id):
        return False
    conn = get_db_connection(db_path)
    with conn:
        cursor = conn.execute("DELETE FROM postulaciones WHERE id = ?", (postulacion_id,))
        deleted = cursor.rowcount > 0
    conn.close()
    return deleted

