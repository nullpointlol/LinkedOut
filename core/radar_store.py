"""Persistencia del Radar LinkedIn: tablas `linkedin_posts` y `radar_state` (SQLite WAL)."""
import json
import re
import math
import sqlite3
import statistics
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple
from uuid import uuid4

from core import database
from core.linkedin_parser import extract_emails, extract_links, extract_whatsapp

# Contadores diarios válidos y su valor inicial
# `enviados` = emails salidos por Gmail (cuenta para el tope diario); `postulados` = marcados a mano
# (WhatsApp, link, DM u otro): no consumen el tope de Gmail
COUNTER_NAMES = ("leidos_busquedas", "leidos_feed", "con_email", "match", "listos", "enviados", "postulados")

ERROR_ENVIO_INTERRUMPIDO = "envío interrumpido, verificar en Gmail antes de reintentar"
# El SMTP se cortó mientras se transmitía el mensaje: puede haber salido (nunca se reintenta solo)
ERROR_ENVIO_INCIERTO = "Envío incierto: verificá en Gmail (Enviados) antes de reintentar"

_POST_COLUMNS = {
    "urn", "url", "autor", "cargo_autor", "texto", "fecha_rel", "query", "emails", "triage",
    "estado", "postulacion_id", "email_to", "email_subject", "email_body", "aprobado_at",
    "sent_at", "message_id", "error", "intentos", "created_at", "updated_at",
    "fuente", "links", "whatsapp", "canal", "destino_canal", "analisis", "prioridad", "progreso", "gen_prioridad",
    "imagenes", "texto_imagen", "texto_pagina", "rescate_intentos", "rescate_at", "reintentos_auto", "reintentos_gen_auto", "gen_manual",
}
_POST_JSON_COLUMNS = ("emails", "triage", "links", "whatsapp", "analisis", "progreso", "imagenes")
# Columnas agregadas después de la primera versión de la tabla: se migran con ALTER TABLE tolerante
_POST_MIGRATION_COLUMNS = (
    ("fuente", "TEXT DEFAULT 'busqueda'"),
    ("links", "TEXT DEFAULT '[]'"),
    ("whatsapp", "TEXT DEFAULT '[]'"),
    ("canal", "TEXT DEFAULT ''"),
    ("destino_canal", "TEXT DEFAULT ''"),
    # Análisis de la oferta (mismo que el Estratega de Postulaciones) y pedido de prioridad del usuario
    ("analisis", "TEXT"),
    ("prioridad", "INTEGER DEFAULT 0"),
    # Link a la publicación (`url` es el perfil del autor, destino del canal "dm")
    ("post_url", "TEXT DEFAULT ''"),
    # Progreso de la generación del CV (JSON de `core.progreso`); escribirlo no toca `updated_at`
    ("progreso", "TEXT"),
    ("gen_prioridad", "INTEGER DEFAULT 0"),
    ("imagenes", "TEXT DEFAULT '[]'"),
    ("texto_imagen", "TEXT DEFAULT ''"),
    ("texto_pagina", "TEXT DEFAULT ''"),
    # Rescate de fondo de las «Captura incompleta»: intentos hechos y hora del último
    ("rescate_intentos", "INTEGER DEFAULT 0"),
    ("rescate_at", "TIMESTAMP"),
    # Reintentos automáticos de un análisis que falló por la IA: cuántas veces volvió solo a OFERTA
    ("reintentos_auto", "INTEGER DEFAULT 0"),
    # Reintentos automáticos de una generación que falló por AGY: cuántas veces volvió sola a GENERANDO
    ("reintentos_gen_auto", "INTEGER DEFAULT 0"),
    # Generación pedida a mano por oferta (Generar CV / Postular igual): corre aunque la generación
    # automática esté en pausa. Queda en 1 como registro; toda toma automática la pone en 0.
    ("gen_manual", "INTEGER DEFAULT 0"),
)
# Prefijo del error de un análisis que falló por la IA (timeout, 503 o respuesta inválida) tras sus intentos
PREFIJO_ANALISIS_FALLIDO = "Falló el análisis ("
# Prefijo del error de las ofertas que el análisis frenó por falta de contenido
PREFIJO_CAPTURA_INCOMPLETA = "Captura incompleta"
# De dónde salió el post: búsqueda global de LinkedIn o el feed de inicio del usuario
FUENTE_MANUAL = "manual"
# Cuentas de LinkedIn del Radar: cada lectura registra cuál la hizo
CUENTA_PRINCIPAL = "principal"
CUENTA_SECUNDARIA = "secundaria"
# Portales de empleo (Computrabajo y Bumeran): los de core/reglas_portales.py, una sola lista. Sus
# ofertas van a la misma tabla y al mismo circuito, con su fuente; no se ven en la vista LinkedIn
from core.reglas_portales import PORTALES as FUENTES_PORTALES  # noqa: E402
FUENTES_VALIDAS = ("busqueda", "feed", FUENTE_MANUAL, *FUENTES_PORTALES)
# Lo que no es de LinkedIn: la vista LinkedIn lo excluye
FUENTES_NO_LINKEDIN = (FUENTE_MANUAL, *FUENTES_PORTALES)
# `updated_at` con milisegundos (UTC, como CURRENT_TIMESTAMP): "Aprobar todos" lo usa para detectar
# que un post cambió desde que el usuario lo vio, así que la resolución de 1 s no alcanza
_AHORA_SQL = "strftime('%Y-%m-%d %H:%M:%f', 'now')"

_STATE_COLUMNS = {
    "desired_running", "estado", "estado_detalle", "next_cycle_at", "umbral_match",
    "tope_posts_dia", "tope_envios_dia", "contadores_dia", "fecha_contadores",
    "ciclos_vacios_consecutivos", "envios_pausados", "backlog_activo", "generacion_pausada", "analisis_pausado",
    "modo_serie", "agy_serie_hasta", "agy_historial", "feed_activo", "busquedas_activas", "ronda_leidas",
}
_STATE_BOOL_COLUMNS = (
    "desired_running", "envios_pausados", "backlog_activo", "generacion_pausada", "analisis_pausado", "modo_serie",
    "feed_activo", "busquedas_activas",
)
_STATE_JSON_COLUMNS = ("contadores_dia", "agy_historial", "ronda_leidas")
_STATE_MIGRATION_COLUMNS = (
    ("backlog_activo", "INTEGER DEFAULT 1"),
    ("generacion_pausada", "INTEGER DEFAULT 0"),
    ("analisis_pausado", "INTEGER DEFAULT 0"),
    ("modo_serie", "INTEGER DEFAULT 0"),
    ("agy_serie_hasta", "REAL"),
    ("agy_historial", "TEXT DEFAULT '[]'"),
    # Interruptores del Radar: las búsquedas arrancan apagadas hasta que el usuario las encienda
    ("feed_activo", "INTEGER DEFAULT 1"),
    ("busquedas_activas", "INTEGER DEFAULT 0"),
    # Búsquedas ya leídas en la ronda en curso: la ronda sigue donde cortó aunque el portal se reinicie
    ("ronda_leidas", "TEXT DEFAULT '[]'"),
)

BACKLOG_AFINIDAD_MIN = 70
BACKLOG_DIAS = 21

# Compartido por la lectura y la toma: una oferta que cambió mientras esperaba ya no se toma.
_BACKLOG_ELEGIBLE = """COALESCE(fuente, 'busqueda') <> 'manual' AND (
    (estado = 'ANALIZADA' AND TRIM(COALESCE(email_body, '')) = '')
    OR (estado = 'ERROR' AND error LIKE 'Falló la generación%' AND COALESCE(intentos, 0) < 2
        AND INSTR(COALESCE(error, ''), '503') = 0
        AND lower(COALESCE(error, '')) NOT LIKE '%service unavailable%'
        AND lower(COALESCE(error, '')) NOT LIKE '%no capacity available%'
        AND lower(COALESCE(error, '')) NOT LIKE '%overloaded%')
)"""


def _empty_counters() -> Dict[str, int]:
    return {name: 0 for name in COUNTER_NAMES}


def _today() -> str:
    return date.today().isoformat()


def init_radar_tables(conn: sqlite3.Connection) -> None:
    """Crea (si faltan) las tablas del Radar y la fila única de `radar_state`."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS radar_lecturas (
            id INTEGER PRIMARY KEY,
            query TEXT, fuente TEXT, inicio TEXT, fin TEXT,
            scrolls INT, motivo_fin TEXT, pantallas TEXT,
            parseados INT, guardados INT, destinos TEXT
        );
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS linkedin_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            urn TEXT UNIQUE NOT NULL,
            url TEXT,
            autor TEXT,
            cargo_autor TEXT,
            texto TEXT NOT NULL,
            fecha_rel TEXT,
            query TEXT,
            emails TEXT DEFAULT '[]',
            triage TEXT,
            estado TEXT DEFAULT 'NUEVO',
            postulacion_id INTEGER,
            email_to TEXT,
            email_subject TEXT,
            email_body TEXT,
            aprobado_at TIMESTAMP,
            sent_at TIMESTAMP,
            message_id TEXT,
            error TEXT,
            intentos INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            fuente TEXT DEFAULT 'busqueda',
            links TEXT DEFAULT '[]',
            whatsapp TEXT DEFAULT '[]',
            canal TEXT DEFAULT '',
            destino_canal TEXT DEFAULT '',
            imagenes TEXT DEFAULT '[]',
            texto_imagen TEXT DEFAULT '',
            texto_pagina TEXT DEFAULT ''
        );
    """)
    # Cuenta de LinkedIn que hizo cada lectura (Radar con dos cuentas)
    columnas_lecturas = {row[1] for row in conn.execute("PRAGMA table_info(radar_lecturas)").fetchall()}
    if "cuenta" not in columnas_lecturas:
        conn.execute("ALTER TABLE radar_lecturas ADD COLUMN cuenta TEXT DEFAULT 'principal'")
    # Posts nuevos con email en la captura: el clasificador borra los que no son oferta y con ellos sus
    # mails. NULL en las lecturas de antes: no se sabe.
    if "con_mail" not in columnas_lecturas:
        conn.execute("ALTER TABLE radar_lecturas ADD COLUMN con_mail INTEGER")
    # Migración tolerante: tablas creadas antes de la fuente/canal no tienen estas columnas
    existentes = {row[1] for row in conn.execute("PRAGMA table_info(linkedin_posts)").fetchall()}
    for nombre, definicion in _POST_MIGRATION_COLUMNS:
        if nombre not in existentes:
            conn.execute(f"ALTER TABLE linkedin_posts ADD COLUMN {nombre} {definicion}")
            if nombre == "gen_manual":
                # Una sola vez, al crear la columna: lo que ya esperaba en GENERANDO forzado se pidió a mano
                # (p. ej. el post 6941, pedido con la pausa puesta antes de este arreglo) y se retoma.
                conn.execute("UPDATE linkedin_posts SET gen_manual = 1 "
                             "WHERE estado = 'GENERANDO' AND gen_prioridad = 2")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_linkedin_posts_estado ON linkedin_posts(estado);")
    # Cubriente: los descartados con postulación se buscan sin leer las filas (texto y análisis pesan)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_linkedin_posts_estado_postulacion "
                 "ON linkedin_posts(estado, postulacion_id);")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS radar_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            desired_running INTEGER DEFAULT 0,
            estado TEXT DEFAULT 'DETENIDO',
            estado_detalle TEXT DEFAULT '',
            next_cycle_at TIMESTAMP,
            umbral_match INTEGER DEFAULT 70,
            tope_posts_dia INTEGER DEFAULT 600,
            tope_envios_dia INTEGER DEFAULT 40,
            contadores_dia TEXT DEFAULT '{}',
            fecha_contadores TEXT,
            ciclos_vacios_consecutivos INTEGER DEFAULT 0,
            envios_pausados INTEGER DEFAULT 0,
            modo_serie INTEGER DEFAULT 0,
            agy_serie_hasta REAL,
            agy_historial TEXT DEFAULT '[]'
        );
    """)
    existentes = {row[1] for row in conn.execute("PRAGMA table_info(radar_state)").fetchall()}
    for nombre, definicion in _STATE_MIGRATION_COLUMNS:
        if nombre not in existentes:
            conn.execute(f"ALTER TABLE radar_state ADD COLUMN {nombre} {definicion}")
    # Solo escribe si falta algo: un INSERT OR IGNORE o un UPDATE que no cambian nada igual toman el lock de
    # escritura, y esto corre en cada conexión. Con ellos cada lectura competía como un escritor y daba
    # «database is locked» detrás de cualquier escritura larga (producción).
    fila = conn.execute("SELECT tope_posts_dia FROM radar_state WHERE id = 1").fetchone()
    if fila is None:
        conn.execute(
            "INSERT OR IGNORE INTO radar_state (id, contadores_dia, fecha_contadores) VALUES (1, ?, ?)",
            (json.dumps(_empty_counters()), _today()),
        )
    elif fila[0] == 300:
        # El tope pasó a contar solo posts nunca vistos y a repartirse entre el feed y cada consulta:
        # el default viejo (300) se lleva al nuevo
        conn.execute("UPDATE radar_state SET tope_posts_dia = 600 WHERE id = 1 AND tope_posts_dia = 300")
    # Urns ya leídos (guardados o no): releerlos no consume el tope diario
    conn.execute("""
        CREATE TABLE IF NOT EXISTS linkedin_vistos (
            urn TEXT PRIMARY KEY,
            query TEXT NOT NULL,
            fecha TEXT NOT NULL
        );
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_linkedin_vistos_fecha ON linkedin_vistos(fecha, query);")
    # Registro de errores: cada error que se guarda en un post, para agrupar por tipo y
    # aprender de los que se repiten. El post conserva solo el último; acá queda el historial.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS radar_errores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            etapa TEXT NOT NULL,
            motivo TEXT NOT NULL,
            tipo TEXT NOT NULL,
            fecha TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_radar_errores_fecha ON radar_errores(fecha);")
    # Portales: cada consulta a un portal (con su error, si lo hubo) y las ofertas ya vistas de cada uno
    # (una que se borró por no ser oferta no vuelve a entrar). Aparte de radar_lecturas: no gastan el presupuesto
    # de búsquedas de LinkedIn.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS portales_lecturas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            portal TEXT NOT NULL,
            busqueda TEXT NOT NULL,
            palabras TEXT NOT NULL,
            inicio TEXT NOT NULL,
            fin TEXT,
            traidas INTEGER DEFAULT 0,
            nuevas INTEGER DEFAULT 0,
            duplicadas INTEGER DEFAULT 0,
            error TEXT
        );
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_portales_lecturas ON portales_lecturas(portal, inicio);")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS portales_vistos (
            urn TEXT PRIMARY KEY,
            post_id INTEGER,
            fecha TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
    """)
    # Pausa de cada radar de portal: desde el panel de procesos o sola, si el portal pidió una verificación
    conn.execute("""
        CREATE TABLE IF NOT EXISTS portales_pausa (
            portal TEXT PRIMARY KEY,
            motivo TEXT NOT NULL DEFAULT '',
            desde TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
    """)


# Un post ENVIADO es una postulación hecha: la suya pasa de «Por postular» a «Postulada» con la fecha de envío.
# Nunca pisa un avance que el usuario ya cargó (en entrevista, oferta, desestimada).
_POSTULADA_POR_ENVIO_SQL = (
    "UPDATE postulaciones SET estado_postulacion = 'POSTULADA', fecha_postulacion = ("
    "SELECT lp.sent_at FROM linkedin_posts lp WHERE lp.postulacion_id = postulaciones.id "
    "AND lp.estado = 'ENVIADO' ORDER BY lp.sent_at DESC LIMIT 1) "
    "WHERE COALESCE(estado_postulacion, 'POR_POSTULAR') = 'POR_POSTULAR' AND id IN ("
    "SELECT postulacion_id FROM linkedin_posts WHERE estado = 'ENVIADO' AND postulacion_id IS NOT NULL{filtro})"
)


def marcar_postuladas_enviadas(conn: sqlite3.Connection, post_id: Optional[int] = None) -> int:
    """Deja «Postulada» la postulación de cada post ENVIADO (o solo la de `post_id`). Devuelve cuántas cambió."""
    if post_id is None:
        return conn.execute(_POSTULADA_POR_ENVIO_SQL.format(filtro="")).rowcount
    return conn.execute(_POSTULADA_POR_ENVIO_SQL.format(filtro=" AND id = ?"), (post_id,)).rowcount


def _connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Conexión con las tablas del Radar garantizadas (idempotente y barato)."""
    conn = database.get_db_connection(db_path)
    try:
        with conn:
            init_radar_tables(conn)
    except Exception:
        conn.close()
        raise
    return conn


# ---------------------------------------------------------------- linkedin_posts

# Días que se conservan las lecturas (diagnóstico y resultados por búsqueda en Mi perfil)
DIAS_LECTURAS = 14
# Destinos de una lectura que son posts nuevos guardados (la columna `guardados` los cuenta)
DESTINOS_NUEVOS = ("guardado", "exterior", "ignorada_vieja")
# Lecturas que no midieron nada: cortadas por la sesión (o un error, que queda como `login`) o por un bloqueo
MOTIVOS_SIN_MEDIDA = ("login", "bloqueo", "bloqueo_levantado")


def lecturas_productivas(consultas: Iterable[str], db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Las lecturas de búsquedas (no del feed, de cualquier cuenta) de `consultas` en los últimos DIAS_LECTURAS
    días que terminaron de leerse: [{query, parseados, con_mail, destinos}]."""
    lista = list(dict.fromkeys(q for q in consultas if isinstance(q, str)))
    if not lista:
        return []
    conn = _connect(db_path)
    try:
        filas = []
        for i in range(0, len(lista), 500):
            lote = lista[i:i + 500]
            filas.extend(conn.execute(
                f"SELECT query, parseados, con_mail, destinos FROM radar_lecturas WHERE fuente = 'busqueda' "
                f"AND COALESCE(motivo_fin, '') NOT IN ({','.join('?' * len(MOTIVOS_SIN_MEDIDA))}) "
                f"AND datetime(inicio) >= datetime('now', ?) AND query IN ({','.join('?' * len(lote))})",
                (*MOTIVOS_SIN_MEDIDA, f"-{DIAS_LECTURAS} days", *lote)).fetchall())
    finally:
        conn.close()
    resultado = []
    for query, parseados, con_mail, destinos in filas:
        try:
            destinos = json.loads(destinos or "{}")
        except (TypeError, ValueError):
            destinos = {}
        resultado.append({"query": query, "parseados": int(parseados or 0), "con_mail": con_mail,
                          "destinos": destinos if isinstance(destinos, dict) else {}})
    return resultado


def estados_por_urn(urns: Iterable[str], db_path: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    """{urn: {estado, clasificado, match_score}} de los posts que siguen en la base."""
    lista = list(dict.fromkeys(u for u in urns if isinstance(u, str) and u))
    conn = _connect(db_path)
    try:
        resultado: Dict[str, Dict[str, Any]] = {}
        for i in range(0, len(lista), 500):
            lote = lista[i:i + 500]
            for urn, estado, triage, analisis in conn.execute(
                f"SELECT urn, estado, triage, analisis FROM linkedin_posts WHERE urn IN ({','.join('?' * len(lote))})",
                lote,
            ):
                try:
                    analisis = json.loads(analisis) if analisis else {}
                except (TypeError, ValueError):
                    analisis = {}
                resultado[urn] = {
                    "estado": estado, "clasificado": bool(triage),
                    "match_score": analisis.get("match_score") if isinstance(analisis, dict) else None,
                }
        return resultado
    finally:
        conn.close()


def registrar_lectura(
    query: str, fuente: str, inicio: str, fin: str, scrolls: int,
    motivo_fin: str, pantallas: list, parseados: int, guardados: int,
    destinos: dict, db_path: Optional[Path] = None, cuenta: str = CUENTA_PRINCIPAL,
    con_mail: Optional[int] = None,
) -> int:
    """Guarda una lectura completa y conserva DIAS_LECTURAS días de diagnóstico."""
    conn = _connect(db_path)
    try:
        with conn:
            # Respecto de la lectura que se guarda (el reloj del Radar), no del reloj del sistema: con relojes
            # distintos se borraban las lecturas recién hechas y el presupuesto por hora contaba 0
            conn.execute("DELETE FROM radar_lecturas WHERE datetime(inicio) < datetime(?, ?)",
                         (inicio, f"-{DIAS_LECTURAS} days"))
            cursor = conn.execute("""INSERT INTO radar_lecturas
                (query, fuente, inicio, fin, scrolls, motivo_fin, pantallas, parseados, guardados, destinos, cuenta,
                 con_mail)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (query, fuente, inicio, fin, scrolls, motivo_fin,
                 json.dumps(pantallas, ensure_ascii=False), parseados, guardados,
                 json.dumps(destinos, ensure_ascii=False), cuenta, con_mail))
            return cursor.lastrowid
    finally:
        conn.close()


def listar_lecturas(limite: int = 50, query: Optional[str] = None,
                    db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    conn = _connect(db_path)
    try:
        filtro = " WHERE query = ?" if query is not None else ""
        parametros = ([query] if query is not None else []) + [max(0, limite)]
        rows = conn.execute("SELECT * FROM radar_lecturas" + filtro + " ORDER BY id DESC LIMIT ?", parametros)
        resultado = []
        for row in rows:
            item = dict(row)
            for campo in ("pantallas", "destinos"):
                item[campo] = json.loads(item[campo])
            resultado.append(item)
        return resultado
    finally:
        conn.close()

def _filtro_cuenta(cuenta: Optional[str]) -> Tuple[str, tuple]:
    """Fragmento SQL y parámetros para limitar a una cuenta (None = todas)."""
    return (" AND COALESCE(cuenta, 'principal') = ?", (cuenta,)) if cuenta else ("", ())


def resumen_lecturas(db_path: Optional[Path] = None, cuenta: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """{query: {"ultimo_inicio": datetime local sin zona, "motivos": [motivo_fin de las 2 últimas, la más
    reciente primero]}} de las búsquedas registradas en `radar_lecturas` (el feed no cuenta)."""
    conn = _connect(db_path)
    try:
        filas = conn.execute(
            "SELECT query, inicio, motivo_fin FROM radar_lecturas WHERE fuente != 'feed'"
            + _filtro_cuenta(cuenta)[0] + " ORDER BY id DESC", _filtro_cuenta(cuenta)[1]
        ).fetchall()
    finally:
        conn.close()
    resumen: Dict[str, Dict[str, Any]] = {}
    for query, inicio, motivo in filas:
        info = resumen.setdefault(str(query), {"ultimo_inicio": None, "motivos": []})
        if info["ultimo_inicio"] is None:
            try:
                fecha = datetime.fromisoformat(str(inicio))
                if fecha.tzinfo is not None:
                    fecha = fecha.astimezone().replace(tzinfo=None)
                info["ultimo_inicio"] = fecha
            except (TypeError, ValueError):
                pass
        if len(info["motivos"]) < 2:
            info["motivos"].append(str(motivo or ""))
    return resumen


def contar_busquedas(desde: datetime, db_path: Optional[Path] = None, cuenta: Optional[str] = None) -> int:
    """Cantidad de lecturas con fuente != 'feed' y datetime(inicio) >= datetime(desde_utc_iso)."""
    desde_utc = desde.astimezone(timezone.utc) if desde.tzinfo else desde.replace(tzinfo=timezone.utc)
    desde_utc_iso = desde_utc.isoformat()
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM radar_lecturas WHERE fuente != 'feed' AND datetime(inicio) >= datetime(?)"
            + _filtro_cuenta(cuenta)[0],
            (desde_utc_iso,) + _filtro_cuenta(cuenta)[1],
        ).fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


def inicios_bloqueo(desde: datetime, db_path: Optional[Path] = None, cuenta: Optional[str] = None) -> List[datetime]:
    """Lista de inicios (aware, UTC) con fuente != 'feed' y motivo_fin = 'bloqueo' desde `desde`,
    de la más vieja a la más nueva."""
    desde_utc = desde.astimezone(timezone.utc) if desde.tzinfo else desde.replace(tzinfo=timezone.utc)
    desde_utc_iso = desde_utc.isoformat()
    conn = _connect(db_path)
    try:
        filas = conn.execute(
            "SELECT inicio FROM radar_lecturas WHERE fuente != 'feed' AND motivo_fin = 'bloqueo' "
            "AND datetime(inicio) >= datetime(?)" + _filtro_cuenta(cuenta)[0]
            + " ORDER BY datetime(inicio) ASC, id ASC",
            (desde_utc_iso,) + _filtro_cuenta(cuenta)[1],
        ).fetchall()
        inicios: List[datetime] = []
        for (inicio_raw,) in filas:
            try:
                dt = datetime.fromisoformat(str(inicio_raw))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                else:
                    dt = dt.astimezone(timezone.utc)
                inicios.append(dt)
            except (ValueError, TypeError):
                continue
        inicios.sort()
        return inicios
    finally:
        conn.close()


def levantar_bloqueo(db_path: Optional[Path] = None, cuenta: Optional[str] = None) -> int:
    """Pasa las lecturas con motivo_fin = 'bloqueo' a 'bloqueo_levantado' (fuente != 'feed').
    Devuelve la cantidad de filas actualizadas."""
    conn = _connect(db_path)
    try:
        with conn:
            cursor = conn.execute(
                "UPDATE radar_lecturas SET motivo_fin = 'bloqueo_levantado' "
                "WHERE fuente != 'feed' AND motivo_fin = 'bloqueo'" + _filtro_cuenta(cuenta)[0],
                _filtro_cuenta(cuenta)[1],
            )
            return cursor.rowcount
    finally:
        conn.close()


def _post_row_to_dict(row: Any, crudas: Sequence[str] = ()) -> Dict[str, Any]:
    """Fila → post, con las columnas JSON decodificadas salvo las de `crudas` (quedan como texto)."""
    d = dict(row)
    for col in _POST_JSON_COLUMNS:
        if col in crudas:
            continue
        raw = d.get(col)
        if isinstance(raw, str):
            try:
                d[col] = json.loads(raw)
            except Exception:
                pass
    for col in ("emails", "links", "whatsapp", "imagenes"):
        if d.get(col) is None or not isinstance(d.get(col), list):
            d[col] = []
    if d.get("texto_imagen") is None:
        d["texto_imagen"] = ""
    if d.get("texto_pagina") is None:
        d["texto_pagina"] = ""
    if "progreso" in d and not isinstance(d["progreso"], dict):
        d["progreso"] = None  # NULL o JSON ilegible: como si no hubiera progreso
    return d


def insert_post_if_new(
    post: Dict[str, Any],
    query: str,
    fuente: str = "busqueda",
    db_path: Optional[Path] = None,
) -> Optional[int]:
    """Inserta el post en estado NUEVO (o IGNORADA: publicado hace un mes o más, ver
    `colas.es_antigua`). Devuelve su id, o None si el `urn` ya existía.

    `fuente` es "busqueda", "feed" o "manual"; otro valor lanza ValueError.
    """
    if fuente not in FUENTES_VALIDAS:
        raise ValueError(f"Fuente desconocida: '{fuente}'. Válidas: {list(FUENTES_VALIDAS)}")
    from core.colas import es_antigua

    estado = "IGNORADA" if fuente != FUENTE_MANUAL and es_antigua(post.get("fecha_rel")) else "NUEVO"
    conn = _connect(db_path)
    try:
        with conn:
            imagenes = post.get("imagenes")
            if isinstance(imagenes, list):
                imagenes_val = json.dumps(imagenes, ensure_ascii=False)
            elif isinstance(imagenes, str):
                imagenes_val = imagenes
            else:
                imagenes_val = "[]"
            texto_img_val = str(post.get("texto_imagen") or "")
            texto_pag_val = str(post.get("texto_pagina") or "")
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO linkedin_posts
                    (urn, url, autor, cargo_autor, texto, fecha_rel, query, emails, estado,
                     fuente, links, whatsapp, post_url, imagenes, texto_imagen, texto_pagina)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    post["urn"],
                    post.get("url", ""),
                    post.get("autor", ""),
                    post.get("cargo_autor", ""),
                    post.get("texto", ""),
                    post.get("fecha_rel", ""),
                    query,
                    json.dumps(post.get("emails") or [], ensure_ascii=False),
                    estado,
                    fuente,
                    json.dumps(post.get("links") or [], ensure_ascii=False),
                    json.dumps(post.get("whatsapp") or [], ensure_ascii=False),
                    post.get("post_url") or "",
                    imagenes_val,
                    texto_img_val,
                    texto_pag_val,
                ),
            )
            return cursor.lastrowid if cursor.rowcount > 0 else None
    finally:
        conn.close()


def insert_post_manual(
    texto: str, post_url: str = "", titulo: str = "", empresa: str = "",
    db_path: Optional[Path] = None,
    imagenes: Optional[List[str]] = None,
) -> int:
    """Guarda una oferta del usuario para procesarla aun con la captura detenida."""
    post_id = insert_post_if_new({
        "urn": f"manual:{uuid4().hex}", "texto": texto, "post_url": post_url,
        "autor": empresa or "Oferta manual", "cargo_autor": titulo,
        "emails": extract_emails(texto), "links": extract_links(texto, [post_url]),
        "whatsapp": extract_whatsapp(texto, [post_url]),
        "imagenes": imagenes or [],
    }, query="manual", fuente=FUENTE_MANUAL, db_path=db_path)
    update_post(post_id, prioridad=1, db_path=db_path)
    return post_id


def post_manual_existente(
    texto: str, post_url: str = "", db_path: Optional[Path] = None,
) -> Optional[int]:
    """Deduplica solo entre manuales, por link o texto sin diferencias de espacios."""
    normal = " ".join((texto or "").split())
    for post in list_posts(fuente=FUENTE_MANUAL, db_path=db_path):
        if ((post_url and post["post_url"] == post_url)
                or (normal and " ".join((post["texto"] or "").split()) == normal)):
            return post["id"]
    return None


def existe_texto(texto: str, db_path: Optional[Path] = None) -> bool:
    """¿Ya hay un post guardado con el mismo texto (sin contar espacios)? Las consultoras republican la
    misma oferta con otro urn: analizarla dos veces gasta créditos."""
    normal = " ".join((texto or "").split())
    if not normal:
        return False
    conn = _connect(db_path)
    try:
        filas = conn.execute(
            "SELECT texto FROM linkedin_posts WHERE COALESCE(estado, '') <> 'IGNORADA' "
            "AND (fuente = 'manual' OR substr(texto, 1, 40) = substr(?, 1, 40))",
            ((texto or "").strip(),),
        ).fetchall()
    finally:
        conn.close()
    return any(" ".join((f[0] or "").split()) == normal for f in filas)


# Republicación (la misma oferta de Ruth Leytes entró 4 veces, con otro saludo): mismo autor, mismo
# puesto y texto casi igual en los últimos días. El texto solo no alcanza: plantillas de un mismo autor con otro
# puesto llegan a 0,98 de parecido (medido sobre el dev 1112)
UMBRAL_REPUBLICACION = 0.95
DIAS_REPUBLICACION = 30


def _puesto_normal(puesto: Any) -> str:
    texto = unicodedata.normalize("NFKD", str(puesto or "")).encode("ascii", "ignore").decode().lower()
    return " ".join(re.findall(r"[a-z0-9]+", texto))


def _puesto_comparable(puesto: Any) -> str:
    """Puesto sin marcas de género («Técnica/o» = «Técnico/a»)."""
    return " ".join(p[:-1] if len(p) > 4 and p[-1] in "ao" else p
                    for p in _puesto_normal(puesto).split() if len(p) > 1)


UMBRAL_REPOSTEO = 0.6


def _mismo_contacto(post: Dict[str, Any], puesto: Any, db_path: Optional[Path]) -> Optional[int]:
    """Otro autor que repostea la misma oferta: mismo mail, mismo puesto y texto parecido."""
    import difflib
    emails = sorted({str(e).strip().lower() for e in post.get("emails") or [] if str(e).strip()})
    objetivo = _puesto_comparable(puesto)
    texto = " ".join((post.get("texto") or "").lower().split())
    if not emails or not objetivo or not texto:
        return None
    conn = _connect(db_path)
    try:
        filas = conn.execute(
            "SELECT id, emails, triage, texto FROM linkedin_posts WHERE id <> ? AND triage IS NOT NULL "
            "AND created_at >= datetime('now', ?) AND (" + " OR ".join("lower(emails) LIKE ?" for _ in emails)
            + ") ORDER BY id",
            (post.get("id") or -1, f"-{DIAS_REPUBLICACION} days", *(f'%"{e}"%' for e in emails)),
        ).fetchall()
    finally:
        conn.close()
    for fila in filas:
        try:
            otros, triage = json.loads(fila[1] or "[]"), json.loads(fila[2] or "{}")
        except ValueError:
            continue
        if not (isinstance(triage, dict) and _puesto_comparable(triage.get("puesto")) == objetivo
                and set(emails) & {str(e).strip().lower() for e in otros or []}):
            continue
        comparar = difflib.SequenceMatcher(None, texto, " ".join((fila[3] or "").lower().split()), autojunk=False)
        if comparar.quick_ratio() >= UMBRAL_REPOSTEO and comparar.ratio() >= UMBRAL_REPOSTEO:
            return int(fila[0])
    return None


def republicacion_de(post: Dict[str, Any], puesto: Any, db_path: Optional[Path] = None) -> Optional[int]:
    """Id del post anterior del que este es una republicación, o None."""
    import difflib
    autor, objetivo = (post.get("autor") or "").strip(), _puesto_normal(puesto)
    texto = " ".join((post.get("texto") or "").lower().split())
    otro_autor = _mismo_contacto(post, puesto, db_path)
    if otro_autor is not None:
        return otro_autor
    if not autor or not objetivo or not texto:
        return None
    conn = _connect(db_path)
    try:
        filas = conn.execute(
            "SELECT id, texto, triage FROM linkedin_posts WHERE autor = ? AND id <> ? AND triage IS NOT NULL "
            "AND created_at >= datetime('now', ?) ORDER BY id",
            (autor, post.get("id") or -1, f"-{DIAS_REPUBLICACION} days"),
        ).fetchall()
    finally:
        conn.close()
    for fila in filas:
        try:
            triage = json.loads(fila[2] or "{}")
        except ValueError:
            continue
        if not isinstance(triage, dict) or _puesto_normal(triage.get("puesto")) != objetivo:
            continue
        comparar = difflib.SequenceMatcher(None, texto, " ".join((fila[1] or "").lower().split()), autojunk=False)
        if (comparar.real_quick_ratio() >= UMBRAL_REPUBLICACION and comparar.quick_ratio() >= UMBRAL_REPUBLICACION
                and comparar.ratio() >= UMBRAL_REPUBLICACION):
            return int(fila[0])
    return None


def delete_post(post_id: int, db_path: Optional[Path] = None) -> None:
    """Borra el post (p. ej. no es una oferta laboral: se ignora, no ocupa lugar)."""
    conn = _connect(db_path)
    try:
        with conn:
            conn.execute("DELETE FROM linkedin_posts WHERE id = ?", (post_id,))
    finally:
        conn.close()


def get_post(post_id: int, db_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    if isinstance(post_id, int) and not -2**63 <= post_id < 2**63:
        return None  # fuera del rango de SQLite: no puede existir (sqlite3 lanzaría OverflowError)
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT * FROM linkedin_posts WHERE id = ?", (post_id,)).fetchone()
    finally:
        conn.close()
    return _post_row_to_dict(row) if row else None


def auditorias_no_aprobadas(db_path: Optional[Path] = None) -> Dict[int, Dict[str, Any]]:
    """Posts LISTO cuya postulación tiene el dictamen de la auditoría no aprobado: id → reporte. Una consulta
    (la lista del Radar la usa por pedido, no por fila)."""
    conn = _connect(db_path)
    try:
        filas = conn.execute(
            "SELECT p.id, q.reporte_auditoria FROM linkedin_posts p JOIN postulaciones q ON q.id = p.postulacion_id "
            "WHERE p.estado = 'LISTO' AND json_valid(q.reporte_auditoria) "
            "AND json_type(q.reporte_auditoria, '$.approved') = 'false'").fetchall()
    except sqlite3.OperationalError:
        return {}  # base sin la tabla de postulaciones
    finally:
        conn.close()
    return {int(f[0]): json.loads(f[1]) for f in filas}


def _en(valor: Any) -> Tuple[str, ...]:
    return (valor,) if isinstance(valor, str) else tuple(valor)


def list_posts(
    estado: Optional[str] = None,
    limit: Optional[int] = None,
    fuente: Optional[Any] = None,
    db_path: Optional[Path] = None,
    excluir_fuente: Optional[Any] = None,
    excluir_estados: Optional[Sequence[str]] = None,
    con_postulacion: bool = False,
    crudas: Sequence[str] = (),
) -> List[Dict[str, Any]]:
    """Lista posts por `id DESC`, con filtros opcionales de estado, fuente, fuente a excluir y estados a excluir.
    `fuente` y `excluir_fuente`: una o varias. `con_postulacion`: solo los que tienen postulación (CV generado).
    `crudas`: columnas JSON que vuelven sin decodificar (la lista del Radar resume `analisis` con caché)."""
    query = "SELECT * FROM linkedin_posts"
    params: List[Any] = []
    filtros: List[str] = []
    if estado is not None and con_postulacion:
        # Por el índice cubriente: 10× más rápido que filtrar `postulacion_id` leyendo cada fila
        filtros.append("id IN (SELECT id FROM linkedin_posts INDEXED BY idx_linkedin_posts_estado_postulacion "
                       "WHERE estado = ? AND postulacion_id IS NOT NULL)")
        params.append(estado)
    elif estado is not None:
        filtros.append("estado = ?")
        params.append(estado)
    if fuente is not None:
        fuentes = _en(fuente)
        filtros.append("fuente IN (%s)" % ", ".join("?" * len(fuentes)))
        params.extend(fuentes)
    if excluir_fuente is not None:
        excluidas = _en(excluir_fuente)
        filtros.append("COALESCE(fuente, 'busqueda') NOT IN (%s)" % ", ".join("?" * len(excluidas)))
        params.extend(excluidas)
    if excluir_estados:
        filtros.append("estado NOT IN (%s)" % ", ".join("?" * len(excluir_estados)))
        params.extend(excluir_estados)
    if con_postulacion:
        filtros.append("postulacion_id IS NOT NULL")
    if filtros:
        query += " WHERE " + " AND ".join(filtros)
    query += " ORDER BY id DESC"
    if limit is not None:
        query += " LIMIT ?"
        params.append(int(limit))
    conn = _connect(db_path)
    try:
        rows = conn.execute(query, params).fetchall()
    finally:
        conn.close()
    return [_post_row_to_dict(r, crudas) for r in rows]


def backlog_pendiente(db_path: Optional[Path] = None) -> List[dict]:
    """Mensajes elegibles con afinidad > 70, captura suficiente y creados en los últimos 21 días."""
    from core.prioridades import elegible_automatico
    from core.radar import orden_backlog

    conn = _connect(db_path)
    try:
        rows = conn.execute(f"SELECT * FROM linkedin_posts WHERE {_BACKLOG_ELEGIBLE}").fetchall()
    finally:
        conn.close()
    ahora = datetime.now(timezone.utc)
    desde = ahora - timedelta(days=BACKLOG_DIAS)
    pendientes = []
    for row in rows:
        post = _post_row_to_dict(row)
        if not elegible_automatico(post):
            continue
        try:
            creada = datetime.fromisoformat(post.get("created_at"))
        except (TypeError, ValueError, OverflowError):
            continue
        # CURRENT_TIMESTAMP de SQLite guarda UTC sin indicación de zona.
        if creada.tzinfo is None:
            creada = creada.replace(tzinfo=timezone.utc)
        if desde <= creada <= ahora:
            pendientes.append(post)
    return orden_backlog(pendientes)


def tomar_automatico(post_id: int, db_path: Optional[Path] = None, *, gen_prioridad: int = 1) -> bool:
    """Encola solo si sigue ANALIZADA y es elegible al momento de la transición."""
    from core.prioridades import elegible_automatico

    conn = _connect(db_path)
    try:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            fila = conn.execute("SELECT * FROM linkedin_posts WHERE id = ?", (post_id,)).fetchone()
            if not fila:
                return False
            post = _post_row_to_dict(fila)
            if post.get("estado") != "ANALIZADA":
                return False
            if gen_prioridad != 2 and not elegible_automatico(post):
                return False
            cursor = conn.execute(
                "UPDATE linkedin_posts SET estado = 'GENERANDO', prioridad = 0, "
                "gen_prioridad = ?, gen_manual = CASE WHEN fuente = 'manual' THEN 1 ELSE 0 END, "
                "intentos = 0, error = NULL, "
                f"updated_at = {_AHORA_SQL} WHERE id = ? AND estado = 'ANALIZADA' "
                "AND TRIM(COALESCE(email_body, '')) = ''",
                (gen_prioridad, post_id),
            )
            return cursor.rowcount > 0
    finally:
        conn.close()


def tomar_backlog(post_id: int, db_path: Optional[Path] = None) -> bool:
    """Reserva solo si sigue elegible; conserva los intentos para acotar los reintentos."""
    from core.prioridades import elegible_automatico

    conn = _connect(db_path)
    try:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            estado = conn.execute(
                "SELECT generacion_pausada, backlog_activo FROM radar_state WHERE id = 1"
            ).fetchone()
            if estado["generacion_pausada"] or not estado["backlog_activo"]:
                return False
            fila = conn.execute("SELECT * FROM linkedin_posts WHERE id = ?", (post_id,)).fetchone()
            if not fila:
                return False
            post = _post_row_to_dict(fila)
            if not elegible_automatico(post):
                return False
            try:
                creada = datetime.fromisoformat(post.get("created_at"))
            except (TypeError, ValueError, OverflowError):
                return False
            if creada.tzinfo is None:
                creada = creada.replace(tzinfo=timezone.utc)
            ahora = datetime.now(timezone.utc)
            desde = ahora - timedelta(days=BACKLOG_DIAS)
            if not (desde <= creada <= ahora):
                return False
            cursor = conn.execute(
                f"UPDATE linkedin_posts SET estado = 'GENERANDO', gen_prioridad = 0, gen_manual = 0, error = NULL, "
                f"updated_at = {_AHORA_SQL} WHERE id = ? AND {_BACKLOG_ELEGIBLE}",
                (post_id,),
            )
            return cursor.rowcount > 0
    finally:
        conn.close()


def candidatas_cola(db_path: Optional[Path] = None) -> List[dict]:
    """Ofertas que esperan CV en las colas dinámicas (`core.colas`): toda ANALIZADA sin mensaje y los
    errores de generación reintentables, sin umbral (a la de fondo entra todo lo que no
    es prioritario). Sin orden: lo calcula `colas.armar_colas` en cada turno."""
    columnas, filas = filas_cola(db_path)
    return [fila_a_post(columnas, f) for f in filas]


def filas_cola(db_path: Optional[Path] = None) -> Tuple[Tuple[str, ...], List[tuple]]:
    """Las candidatas de `candidatas_cola` sin decodificar: (columnas, filas en orden de id). Comparar dos
    lecturas dice si cambió algo sin pagar el JSON de cada una."""
    conn = _connect(db_path)
    try:
        cursor = conn.execute(f"SELECT * FROM linkedin_posts WHERE {_BACKLOG_ELEGIBLE} ORDER BY id")
        return tuple(c[0] for c in cursor.description), [tuple(r) for r in cursor.fetchall()]
    finally:
        conn.close()


def fila_a_post(columnas: Sequence[str], fila: Sequence[Any]) -> Dict[str, Any]:
    """Una fila de `filas_cola` como post (igual que las demás lecturas)."""
    return _post_row_to_dict(dict(zip(columnas, fila)))


def tomar_candidata(post_id: int, nivel: str, db_path: Optional[Path] = None) -> bool:
    """Pasa a GENERANDO la candidata elegida si sigue en el mismo nivel (atómico).

    Prioritaria → gen_prioridad 1; de fondo → 0 y exige la cola de fondo activa. Nada con la
    generación en pausa. Un error reintentado conserva sus intentos."""
    from core import colas

    conn = _connect(db_path)
    try:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            estado = conn.execute(
                "SELECT generacion_pausada, backlog_activo FROM radar_state WHERE id = 1"
            ).fetchone()
            if estado is None or estado["generacion_pausada"]:
                return False
            if nivel == colas.FONDO and not estado["backlog_activo"]:
                return False
            fila = conn.execute("SELECT * FROM linkedin_posts WHERE id = ?", (post_id,)).fetchone()
            if not fila or colas.nivel(_post_row_to_dict(fila)) != nivel:
                return False
            prioridad = 1 if nivel == colas.PRIORITARIA else 0
            cursor = conn.execute(
                "UPDATE linkedin_posts SET estado = 'GENERANDO', prioridad = 0, gen_prioridad = ?, gen_manual = 0, "
                "error = NULL, "
                "intentos = CASE WHEN estado = 'ANALIZADA' THEN 0 ELSE COALESCE(intentos, 0) END, "
                f"updated_at = {_AHORA_SQL} WHERE id = ? AND {_BACKLOG_ELEGIBLE}",
                (prioridad, post_id),
            )
            return cursor.rowcount > 0
    finally:
        conn.close()


def resumen_cola(db_path: Optional[Path] = None, radar: Any = None) -> Dict[str, Any]:
    """Cola de trabajo del radar: CVs por generar, ofertas por analizar, el post que se procesa ahora
    (el que tiene progreso en curso o, si no hay, el primero por prioridad e id) con su `progreso`
    (solo si está en curso: sin `fin`) y
    la estimación (`eta_s` = GENERANDO × la mediana de lo que tardaron las últimas 20 postulaciones
    con tiempo total medido, `telemetry.total_s`; la mediana no se dispara por un CV que esperó la
    cuota horas); None si no hay ninguna."""
    conn = _connect(db_path)
    try:
        conteo = dict(conn.execute(
            "SELECT estado, COUNT(*) FROM linkedin_posts "
            "WHERE estado IN ('GENERANDO', 'OFERTA', 'ANALIZANDO', 'NUEVO') GROUP BY estado"
        ).fetchall())
        filas = conn.execute(
            "SELECT * FROM linkedin_posts WHERE estado = 'GENERANDO' "
            "ORDER BY COALESCE(gen_prioridad, 0) DESC, id"
        ).fetchall()
        quedan = conn.execute(f"SELECT COUNT(*) FROM linkedin_posts WHERE {_BACKLOG_ELEGIBLE}").fetchone()[0]
        telemetrias = [r[0] for r in conn.execute(
            "SELECT telemetry FROM postulaciones WHERE telemetry LIKE '%total_s%' ORDER BY id DESC LIMIT 20"
        ).fetchall()]
    finally:
        conn.close()
    generando = int(conteo.get("GENERANDO", 0))
    actual = None
    pendientes = [_post_row_to_dict(fila) for fila in filas]
    if radar is not None:
        generando_id = getattr(radar, "generando_ahora", None)
        if isinstance(generando_id, int):
            post = next((p for p in pendientes if p["id"] == generando_id), None)
            if post:
                triage = post["triage"] if isinstance(post.get("triage"), dict) else {}
                analisis = post["analisis"] if isinstance(post.get("analisis"), dict) else {}
                prog = post.get("progreso")
                if not isinstance(prog, dict) or prog.get("fin") is not None:
                    prog = None  # ilegible, o de un trabajo anterior ya terminado: está en cola
                etapa = prog.get("etiqueta") if prog else None
                actual = {"post_id": post["id"], "puesto": analisis.get("puesto") or triage.get("puesto") or "",
                          "etapa": etapa if isinstance(etapa, str) and etapa else None, "progreso": prog}
        elif generando_id is None:
            actual = None
        else:
            if pendientes:
                post = next((p for p in pendientes if isinstance(p.get("progreso"), dict)
                             and p["progreso"].get("fin") is None), pendientes[0])
                triage = post["triage"] if isinstance(post.get("triage"), dict) else {}
                analisis = post["analisis"] if isinstance(post.get("analisis"), dict) else {}
                prog = post.get("progreso")
                if not isinstance(prog, dict) or prog.get("fin") is not None:
                    prog = None
                etapa = prog.get("etiqueta") if prog else None
                actual = {"post_id": post["id"], "puesto": analisis.get("puesto") or triage.get("puesto") or "",
                          "etapa": etapa if isinstance(etapa, str) and etapa else None, "progreso": prog}
    elif pendientes:
        # Un pedido prioritario no interrumpe al CV en curso: su progreso sigue siendo el actual.
        post = next((p for p in pendientes if isinstance(p.get("progreso"), dict)
                     and p["progreso"].get("fin") is None), pendientes[0])
        triage = post["triage"] if isinstance(post.get("triage"), dict) else {}
        analisis = post["analisis"] if isinstance(post.get("analisis"), dict) else {}
        prog = post.get("progreso")
        if not isinstance(prog, dict) or prog.get("fin") is not None:
            prog = None  # ilegible, o de un trabajo anterior ya terminado: está en cola
        etapa = prog.get("etiqueta") if prog else None
        actual = {"post_id": post["id"], "puesto": analisis.get("puesto") or triage.get("puesto") or "",
                  "etapa": etapa if isinstance(etapa, str) and etapa else None, "progreso": prog}
    tiempos = []
    for crudo in telemetrias:
        try:
            total = float(json.loads(crudo).get("total_s"))
        except (TypeError, ValueError, AttributeError):
            continue
        if math.isfinite(total) and total > 0:
            tiempos.append(total)
    eta_s = round(generando * statistics.median(tiempos)) if (tiempos and generando and actual is not None) else None
    return {
        "generando": generando,
        "analizando": sum(int(conteo.get(e, 0)) for e in ("OFERTA", "ANALIZANDO", "NUEVO")),
        "actual": actual,
        "eta_s": eta_s,
        "backlog": {"activo": get_radar_state(db_path).get("backlog_activo", False), "quedan": quedan},
    }


def _sin_estado_previo(conn: sqlite3.Connection, post_id: int, fields: Dict[str, Any]) -> None:
    """Saca `estado_previo` del triage que se va a guardar (el de `fields` o, si no viene, el guardado)."""
    triage = fields.get("triage")
    if triage is None and "triage" not in fields:
        fila = conn.execute("SELECT triage FROM linkedin_posts WHERE id = ?", (post_id,)).fetchone()
        try:
            triage = json.loads(fila[0]) if fila and fila[0] else None
        except (TypeError, ValueError):
            triage = None
    if isinstance(triage, dict) and "estado_previo" in triage:
        fields["triage"] = {k: v for k, v in triage.items() if k != "estado_previo"}


_ETAPA_POR_ESTADO = {"NUEVO": "clasificacion", "OFERTA": "analisis", "ANALIZANDO": "analisis",
                     "ANALIZADA": "generacion", "GENERANDO": "generacion", "LISTO": "envio", "ENVIANDO": "envio"}


def tipo_error(motivo: str) -> str:
    """Clave para agrupar errores parecidos: sin números, emails, urls ni detalle entre paréntesis."""
    t = re.sub(r"https?://\S+|[\w.+-]+@[\w-]+\.[\w.]+", "…", motivo or "")
    t = re.sub(r"\([^)]*\)", "", t)
    t = re.sub(r"\d+(?:[.,]\d+)?", "N", t)
    return " ".join(t.split())[:140] or "Sin detalle"


def _registrar_error(conn: sqlite3.Connection, post_id: int, estado_previo: Optional[str], motivo: str) -> None:
    etapa = _ETAPA_POR_ESTADO.get(estado_previo or "", "otra")
    conn.execute("INSERT INTO radar_errores (post_id, etapa, motivo, tipo, fecha) VALUES (?, ?, ?, ?, " + _AHORA_SQL + ")",
                 (post_id, etapa, motivo[:2000], tipo_error(motivo)))


def errores_por_tipo(dias: int = 7, db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Errores de los últimos `dias` agrupados por etapa y tipo, los más frecuentes primero."""
    conn = _connect(db_path)
    try:
        filas = conn.execute(
            "SELECT etapa, tipo, COUNT(*) AS veces, COUNT(DISTINCT post_id) AS posts, MAX(fecha) AS ultima, "
            "MAX(motivo) AS ejemplo, GROUP_CONCAT(DISTINCT post_id) AS ids FROM radar_errores "
            "WHERE fecha >= datetime('now', ?) GROUP BY etapa, tipo ORDER BY veces DESC, ultima DESC",
            (f"-{int(dias)} days",),
        ).fetchall()
    finally:
        conn.close()
    return [{"etapa": f["etapa"], "tipo": f["tipo"], "veces": f["veces"], "posts": f["posts"],
             "ultima": f["ultima"], "ejemplo": f["ejemplo"],
             "post_ids": sorted(int(i) for i in str(f["ids"] or "").split(",") if i)[:20]} for f in filas]


def errores_pendientes_por_tipo(db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Errores sin resolver: los posts que hoy están en ERROR, agrupados por etapa y tipo (lo que ya se solucionó no se muestra). El historial completo queda en `radar_errores`."""
    conn = _connect(db_path)
    try:
        posts = conn.execute(
            "SELECT p.id, p.error, p.updated_at, (SELECT e.etapa FROM radar_errores e WHERE e.post_id = p.id "
            "ORDER BY e.id DESC LIMIT 1) AS etapa FROM linkedin_posts p WHERE p.estado = 'ERROR'"
        ).fetchall()
    finally:
        conn.close()
    grupos: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for f in posts:
        motivo = f["error"] or ""
        etapa = f["etapa"] or "otra"
        clave = (etapa, tipo_error(motivo))
        g = grupos.setdefault(clave, {"etapa": etapa, "tipo": clave[1], "veces": 0, "posts": 0, "ultima": "",
                                      "ejemplo": motivo, "post_ids": []})
        g["veces"] += 1
        g["posts"] += 1
        g["ultima"] = max(g["ultima"], f["updated_at"] or "")
        g["post_ids"].append(int(f["id"]))
    resultado = sorted(grupos.values(), key=lambda g: (-g["veces"], g["ultima"]))
    for g in resultado:
        g["post_ids"] = sorted(g["post_ids"])[:20]
    return resultado


# Una generación que falló por AGY: timeout del Redactor o del plazo, «Fallo al ejecutar agy CLI» o saturación
# (503). Un rechazo de la revisión de calidad no entra: ese ya tuvo su corrección automática.
_GENERACION_FALLIDA_POR_AGY = """estado = 'ERROR'
    AND (error LIKE 'Falló la generación%' OR error LIKE 'Generación detenida por saturación%')
    AND error NOT LIKE '%Revisá el CV%'
    AND (error LIKE '%tiempo límite%' OR error LIKE '%Fallo al ejecutar agy%' OR error LIKE '%503%'
         OR lower(error) LIKE '%saturaci%' OR error LIKE '%min de trabajo%')"""


def candidatos_reintento_generacion(max_reintentos: int, hasta: str,
                                    db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Posts en ERROR por una generación que falló por AGY, con menos de `max_reintentos` reintentos automáticos y sin
    cambios desde `hasta` (`updated_at`, UTC como `_AHORA_SQL`)."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            f"SELECT * FROM linkedin_posts WHERE {_GENERACION_FALLIDA_POR_AGY} "
            "AND COALESCE(reintentos_gen_auto, 0) < ? AND updated_at <= ? ORDER BY id",
            (int(max_reintentos), hasta),
        ).fetchall()
    finally:
        conn.close()
    return [_post_row_to_dict(r) for r in rows]


def candidatos_reintento_analisis(max_reintentos: int, hasta: str,
                                  db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Posts en ERROR por un análisis fallido (`PREFIJO_ANALISIS_FALLIDO`) con menos de `max_reintentos`
    reintentos automáticos y sin cambios desde `hasta` (`updated_at`, UTC como `_AHORA_SQL`)."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM linkedin_posts WHERE estado = 'ERROR' AND error LIKE ? "
            "AND COALESCE(reintentos_auto, 0) < ? AND updated_at <= ? ORDER BY id",
            (PREFIJO_ANALISIS_FALLIDO + "%", int(max_reintentos), hasta),
        ).fetchall()
    finally:
        conn.close()
    return [_post_row_to_dict(r) for r in rows]


def candidatos_rescate(max_intentos: int, limite: int, db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Posts en ERROR por «Captura incompleta», no manuales y con menos de `max_intentos` rescates:
    primero los menos intentados (un barrido no repite los mismos antes de pasar por todos) y, a igual
    cantidad, los más recientes."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM linkedin_posts WHERE estado = 'ERROR' AND error LIKE ? "
            "AND COALESCE(fuente, 'busqueda') <> ? AND COALESCE(rescate_intentos, 0) < ? "
            "ORDER BY COALESCE(rescate_intentos, 0), id DESC LIMIT ?",
            (PREFIJO_CAPTURA_INCOMPLETA + "%", FUENTE_MANUAL, int(max_intentos), int(limite)),
        ).fetchall()
    finally:
        conn.close()
    return [_post_row_to_dict(r) for r in rows]


def update_post(post_id: int, *, db_path: Optional[Path] = None,
                updated_at_esperado: Optional[str] = None, **fields: Any) -> bool:
    """Actualiza el post y su versión; con `updated_at_esperado`, solo si coincide (atómico)."""
    unknown = set(fields) - _POST_COLUMNS
    if unknown:
        raise ValueError(f"Columnas desconocidas en linkedin_posts: {sorted(unknown)}")
    if not fields:
        return False
    conn = _connect(db_path)
    try:
        with conn:
            if fields.get("estado", "DESCARTADO") != "DESCARTADO":
                # Sale de DESCARTADO por otra vía que «Deshacer»: ese estado previo ya no vale
                _sin_estado_previo(conn, post_id, fields)
            sets = []
            values: List[Any] = []
            for key, value in fields.items():
                if key in _POST_JSON_COLUMNS and isinstance(value, (list, dict)):
                    value = json.dumps(value, ensure_ascii=False)
                sets.append(f"{key} = ?")
                values.append(value)
            if "updated_at" not in fields:
                sets.append(f"updated_at = {_AHORA_SQL}")
            values.append(post_id)
            condicion = "id = ?"
            if updated_at_esperado is not None:
                condicion += " AND updated_at = ?"
                values.append(updated_at_esperado)
            previo = None
            if isinstance(fields.get("error"), str) and fields["error"].strip():
                previo = conn.execute("SELECT estado, error FROM linkedin_posts WHERE id = ?", (post_id,)).fetchone()
            actualizado = conn.execute(f"UPDATE linkedin_posts SET {', '.join(sets)} WHERE {condicion}", values)
            if actualizado.rowcount == 1 and previo is not None:
                _registrar_error(conn, post_id, previo["estado"], fields["error"])
            if actualizado.rowcount == 1 and fields.get("estado") == "ENVIADO":
                marcar_postuladas_enviadas(conn, post_id)
            return actualizado.rowcount == 1
    finally:
        conn.close()


def guardar_progreso(post_id: int, progreso: Optional[Dict[str, Any]], db_path: Optional[Path] = None) -> None:
    """Guarda el progreso de la generación (None lo borra). No toca `updated_at`: el progreso no es
    contenido del post y "Aprobar todos" compara `updated_at` para detectar cambios."""
    valor = json.dumps(progreso, ensure_ascii=False) if progreso is not None else None
    conn = _connect(db_path)
    try:
        with conn:
            conn.execute("UPDATE linkedin_posts SET progreso = ? WHERE id = ?", (valor, post_id))
    finally:
        conn.close()


def recipient_sent_within(
    email: str,
    days: int = 30,
    exclude_post_id: Optional[int] = None,
    db_path: Optional[Path] = None,
) -> bool:
    """True si ya se envió (estado ENVIADO) un mail a ese destinatario dentro de la ventana."""
    cutoff = (datetime.now() - timedelta(days=days)).isoformat(sep=" ", timespec="seconds")
    query = (
        "SELECT 1 FROM linkedin_posts WHERE estado = 'ENVIADO' AND lower(email_to) = ? "
        "AND sent_at IS NOT NULL AND julianday(sent_at) >= julianday(?)"
    )
    params: List[Any] = [(email or "").strip().lower(), cutoff]
    if exclude_post_id is not None:
        query += " AND id != ?"
        params.append(exclude_post_id)
    conn = _connect(db_path)
    try:
        return conn.execute(query + " LIMIT 1", params).fetchone() is not None
    finally:
        conn.close()


def envio_previo_a(
    email: str,
    days: int = 30,
    exclude_post_id: Optional[int] = None,
    db_path: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """La oferta que ya recibió (o va a recibir) un mail a ese destinatario: el ENVIADO más reciente
    dentro de la ventana o, si no hay, uno ENVIANDO / LISTO ya aprobado. None si no hay ninguno.
    `{post_id, puesto, empresa, sent_at (None si todavía no salió), estado}`."""
    to = (email or "").strip().lower()
    if not to:
        return None
    cutoff = (datetime.now() - timedelta(days=days)).isoformat(sep=" ", timespec="seconds")
    excluir = "" if exclude_post_id is None else " AND id != ?"
    extra: List[Any] = [] if exclude_post_id is None else [exclude_post_id]
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM linkedin_posts WHERE estado = 'ENVIADO' AND lower(email_to) = ? "
            "AND sent_at IS NOT NULL AND julianday(sent_at) >= julianday(?)" + excluir
            + " ORDER BY julianday(sent_at) DESC, id DESC LIMIT 1",
            [to, cutoff, *extra],
        ).fetchone()
        if row is None:
            row = conn.execute(
                "SELECT * FROM linkedin_posts WHERE lower(email_to) = ? AND (estado = 'ENVIANDO' "
                "OR (estado = 'LISTO' AND aprobado_at IS NOT NULL))" + excluir
                + " ORDER BY id DESC LIMIT 1",
                [to, *extra],
            ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    post = _post_row_to_dict(row)
    triage = post.get("triage") if isinstance(post.get("triage"), dict) else {}
    return {
        "post_id": post["id"],
        "puesto": str(triage.get("puesto") or "").strip(),
        "empresa": str(triage.get("empresa") or "").strip(),
        "sent_at": post.get("sent_at") if post["estado"] == "ENVIADO" else None,
        "estado": post["estado"],
    }


CANALES_NO_EMAIL_SQL = "COALESCE(NULLIF(TRIM(LOWER(canal)), ''), 'email') <> 'email'"


def es_canal_email(post_o_canal: Any) -> bool:
    """True si el canal de postulación es email. Acepta un post (dict) o el canal; vacío/None = email
    (posts anteriores a los canales)."""
    canal = post_o_canal.get("canal") if isinstance(post_o_canal, dict) else post_o_canal
    return (str(canal or "").strip().lower() or "email") == "email"


# Estados desde los que el usuario puede marcar a mano que ya se postuló (botón Listo)
ESTADOS_MARCABLES = ("ANALIZADA", "LISTO", "ERROR")
# El post no se puede enviar por Gmail: otro canal, o canal email sin destinatario
SIN_ENVIO_POR_EMAIL_SQL = f"({CANALES_NO_EMAIL_SQL} OR TRIM(COALESCE(email_to, '')) = '')"


def mark_applied(post_id: int, message_id: str, sent_at: str, db_path: Optional[Path] = None) -> bool:
    """Transición atómica a `ENVIADO` (postulación manual) desde `ESTADOS_MARCABLES`, solo si el post no
    se puede enviar por Gmail. False si cambió en el medio: no se pisa nada y el llamador no cuenta
    la postulación."""
    conn = _connect(db_path)
    try:
        with conn:
            cursor = conn.execute(
                "UPDATE linkedin_posts SET estado = 'ENVIADO', sent_at = ?, message_id = ?, aprobado_at = NULL, "
                f"intentos = 0, error = NULL, updated_at = {_AHORA_SQL} "
                f"WHERE id = ? AND estado IN {ESTADOS_MARCABLES} AND {SIN_ENVIO_POR_EMAIL_SQL}",
                (sent_at, message_id, post_id),
            )
            if cursor.rowcount == 1:
                marcar_postuladas_enviadas(conn, post_id)
            return cursor.rowcount == 1
    finally:
        conn.close()


def claim_for_sending(post_id: int, db_path: Optional[Path] = None) -> bool:
    """Transición atómica `LISTO` aprobado → `ENVIANDO`. False si el post ya no está LISTO y aprobado
    (p. ej. lo descartaron o regeneraron entre la relectura y el envío) o si no es de canal email
    (esos nunca salen por Gmail): entonces no se envía."""
    conn = _connect(db_path)
    try:
        with conn:
            cursor = conn.execute(
                f"UPDATE linkedin_posts SET estado = 'ENVIANDO', error = NULL, updated_at = {_AHORA_SQL} "
                "WHERE id = ? AND estado = 'LISTO' AND aprobado_at IS NOT NULL "
                f"AND NOT ({CANALES_NO_EMAIL_SQL})",
                (post_id,),
            )
            return cursor.rowcount == 1
    finally:
        conn.close()


def devolver_analizando_a_oferta(db_path: Optional[Path] = None) -> None:
    """Un análisis cortado (stop o cierre) vuelve a la cola de ofertas."""
    conn = _connect(db_path)
    try:
        with conn:
            conn.execute(
                f"UPDATE linkedin_posts SET estado = 'OFERTA', updated_at = {_AHORA_SQL} "
                "WHERE estado = 'ANALIZANDO'"
            )
    finally:
        conn.close()


def postulacion_con_cv_listo(postulacion_id: Optional[int], db_path: Optional[Path] = None) -> bool:
    """¿La postulación existe, está READY y tiene CV? Es la misma condición que exige el compose del mensaje."""
    if not postulacion_id:
        return False
    conn = _connect(db_path)
    try:
        fila = conn.execute(
            "SELECT 1 FROM postulaciones WHERE id = ? AND status = 'READY' "
            "AND TRIM(COALESCE(cv_markdown, '')) <> ''", (postulacion_id,)
        ).fetchone()
    except sqlite3.OperationalError:
        return False  # base sin tabla de postulaciones (solo el Radar)
    finally:
        conn.close()
    return fila is not None


def reconcile_on_startup(db_path: Optional[Path] = None) -> None:
    """Posts que quedaron ENVIANDO (proceso caído a mitad de envío) pasan a ERROR sin reenviar
    (y sin aprobación: reintentarlos exige que el usuario vuelva a aprobar). Los que quedaron
    ANALIZANDO vuelven a OFERTA para analizarse de nuevo.

    Un CV cortado a mitad (cierre del proceso): al arrancar nada se está generando, así que los
    GENERANDO quedan en cola sin progreso; los que apuntan a una postulación que no quedó READY se
    desvinculan (el reintento rehace el CV) y las postulaciones del Radar que quedaron PROCESSING
    pasan a ERROR (las manuales no se tocan)."""
    devolver_analizando_a_oferta(db_path)
    conn = _connect(db_path)
    try:
        with conn:
            for fila in conn.execute("SELECT id FROM linkedin_posts WHERE estado = 'ENVIANDO'").fetchall():
                _registrar_error(conn, fila["id"], "ENVIANDO", ERROR_ENVIO_INTERRUMPIDO)
            conn.execute(
                "UPDATE linkedin_posts SET estado = 'ERROR', error = ?, aprobado_at = NULL, "
                f"updated_at = {_AHORA_SQL} WHERE estado = 'ENVIANDO'",
                (ERROR_ENVIO_INTERRUMPIDO,),
            )
            conn.execute("UPDATE linkedin_posts SET progreso = NULL WHERE estado = 'GENERANDO'")
            try:
                conn.execute(
                    "UPDATE linkedin_posts SET postulacion_id = NULL WHERE estado = 'GENERANDO' "
                    "AND postulacion_id IS NOT NULL AND postulacion_id NOT IN "
                    "(SELECT id FROM postulaciones WHERE status = 'READY')"
                )
                conn.execute(
                    "UPDATE postulaciones SET status = 'ERROR' "
                    "WHERE status = 'PROCESSING' AND COALESCE(origen, 'manual') = 'linkedin'"
                )
            except sqlite3.OperationalError:
                pass  # base sin tabla de postulaciones (solo el Radar): no hay nada que desvincular
    finally:
        conn.close()


# ---------------------------------------------------------------- limpieza de ofertas viejas

# Se borran las ofertas de estos estados con más de DIAS_LIMPIEZA días y afinidad menor a la mínima (o sin afinidad)
DIAS_LIMPIEZA = 15
ESTADOS_LIMPIABLES = ("ANALIZADA", "DESCARTADO", "IGNORADA")
_LOTE_LIMPIEZA = 500


def _afinidad(analisis: Any) -> Optional[float]:
    try:
        valor = json.loads(analisis).get("match_score") if analisis else None
        return float(valor) if valor is not None else None
    except (TypeError, ValueError, AttributeError):
        return None


def _candidatas(conn: sqlite3.Connection, umbral: float) -> List[Tuple[int, str, Optional[int]]]:
    marcas = ",".join("?" * len(ESTADOS_LIMPIABLES))
    filas = conn.execute(
        f"SELECT id, estado, analisis, postulacion_id FROM linkedin_posts WHERE estado IN ({marcas}) "
        "AND created_at < datetime('now', ?)", (*ESTADOS_LIMPIABLES, f"-{DIAS_LIMPIEZA} days"))
    resultado = []
    for post_id, estado, analisis, postulacion_id in filas:
        afinidad = _afinidad(analisis)
        if afinidad is None or afinidad < umbral:
            resultado.append((post_id, estado, postulacion_id))
    return resultado


def _por_estado(candidatas: Sequence[Tuple[int, str, Optional[int]]]) -> Dict[str, int]:
    conteo: Dict[str, int] = {}
    for _id, estado, _postulacion in candidatas:
        conteo[estado] = conteo.get(estado, 0) + 1
    return conteo


def candidatas_limpieza(umbral: float, db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Cuántas ofertas borraría la limpieza con la afinidad mínima `umbral`: {total, por_estado}."""
    conn = _connect(db_path)
    try:
        candidatas = _candidatas(conn, umbral)
    finally:
        conn.close()
    return {"total": len(candidatas), "por_estado": _por_estado(candidatas)}


def _archivo_base(db_path: Optional[Path]) -> Path:
    return Path(db_path or database.DB_PATH)


def _megas(archivo: Path) -> float:
    total = sum(f.stat().st_size for f in (archivo, Path(f"{archivo}-wal")) if f.exists())
    return round(total / 1048576, 1)


def _backup_base(archivo: Path) -> str:
    """Copia consistente de la base (API de backup de SQLite) en `backups/`, al lado de la base."""
    destino = archivo.parent / "backups" / f"{archivo.stem}-{datetime.now():%Y%m%d-%H%M%S}-antes-de-limpieza.db"
    destino.parent.mkdir(parents=True, exist_ok=True)
    fuente = sqlite3.connect(str(archivo))
    try:
        copia = sqlite3.connect(str(destino))
        try:
            fuente.backup(copia)
        finally:
            copia.close()
    except Exception:
        destino.unlink(missing_ok=True)
        raise
    finally:
        fuente.close()
    return str(destino)


def limpiar_viejas(umbral: float, db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Borra las candidatas de la limpieza y sus postulaciones, previo backup; después compacta la base.
    Si el backup falla, no borra nada. Sin candidatas, no hace nada."""
    archivo = _archivo_base(db_path)
    conn = _connect(db_path)
    try:
        candidatas = _candidatas(conn, umbral)
        if not candidatas:
            return {"borradas": 0, "postulaciones": 0, "por_estado": {}, "backup": None,
                    "mb_antes": _megas(archivo), "mb_despues": _megas(archivo)}
        mb_antes = _megas(archivo)
        backup = _backup_base(archivo)
        ids = [c[0] for c in candidatas]
        postulaciones = [c[2] for c in candidatas if c[2]]
        tablas = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        with conn:
            for i in range(0, len(ids), _LOTE_LIMPIEZA):
                lote = ids[i:i + _LOTE_LIMPIEZA]
                marcas = ",".join("?" * len(lote))
                conn.execute(f"DELETE FROM linkedin_posts WHERE id IN ({marcas})", lote)
                for tabla in ("radar_errores", "revision_pendiente"):
                    if tabla in tablas:
                        conn.execute(f"DELETE FROM {tabla} WHERE post_id IN ({marcas})", lote)
            for i in range(0, len(postulaciones), _LOTE_LIMPIEZA):
                lote = postulaciones[i:i + _LOTE_LIMPIEZA]
                conn.execute(f"DELETE FROM postulaciones WHERE id IN ({','.join('?' * len(lote))})", lote)
    finally:
        conn.close()
    _compactar(archivo)
    return {"borradas": len(ids), "postulaciones": len(postulaciones), "por_estado": _por_estado(candidatas),
            "backup": backup, "mb_antes": mb_antes, "mb_despues": _megas(archivo)}


def _compactar(archivo: Path) -> None:
    """VACUUM y checkpoint del WAL: el espacio de lo borrado vuelve al disco."""
    conn = database.get_db_connection(archivo)
    try:
        conn.isolation_level = None
        conn.execute("VACUUM")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()


# ----------------------------------------------------------------- radar_state

def _state_row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    d.pop("id", None)
    for col in _STATE_BOOL_COLUMNS:
        d[col] = bool(d.get(col))
    if "agy_serie_hasta" in d and d["agy_serie_hasta"] is not None:
        try:
            d["agy_serie_hasta"] = float(d["agy_serie_hasta"])
        except (TypeError, ValueError):
            d["agy_serie_hasta"] = None
    else:
        d["agy_serie_hasta"] = None
    try:
        historial = json.loads(d.get("agy_historial") or "[]")
        d["agy_historial"] = historial if isinstance(historial, list) else []
    except Exception:
        d["agy_historial"] = []
    try:
        ronda = json.loads(d.get("ronda_leidas") or "[]")
        d["ronda_leidas"] = [q for q in ronda if isinstance(q, str)] if isinstance(ronda, list) else []
    except Exception:
        d["ronda_leidas"] = []
    counters = _empty_counters()
    try:
        loaded = json.loads(d.get("contadores_dia") or "{}")
        if isinstance(loaded, dict):
            counters.update({k: int(v) for k, v in loaded.items() if k in counters})
    except Exception:
        pass
    d["contadores_dia"] = counters
    return d


def _reset_counters_if_new_day(conn: sqlite3.Connection) -> None:
    """Reinicia los contadores si `fecha_contadores` no es hoy (fecha local). Debe correr dentro de una transacción."""
    row = conn.execute("SELECT fecha_contadores FROM radar_state WHERE id = 1").fetchone()
    today = _today()
    if row is None or row["fecha_contadores"] != today:
        conn.execute(
            "UPDATE radar_state SET contadores_dia = ?, fecha_contadores = ? WHERE id = 1",
            (json.dumps(_empty_counters()), today),
        )


def get_radar_state(db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Estado del radar; aplica antes el reinicio diario de contadores."""
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT * FROM radar_state WHERE id = 1").fetchone()
        # El reinicio diario es la única escritura: solo toma el lock cuando cambió el día (y lo vuelve a
        # comprobar adentro de la transacción, por si otro ya lo hizo).
        if row is None or row["fecha_contadores"] != _today():
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                _reset_counters_if_new_day(conn)
            row = conn.execute("SELECT * FROM radar_state WHERE id = 1").fetchone()
    finally:
        conn.close()
    return _state_row_to_dict(row)


def update_radar_state(*, db_path: Optional[Path] = None, **fields: Any) -> None:
    unknown = set(fields) - _STATE_COLUMNS
    if unknown:
        raise ValueError(f"Columnas desconocidas en radar_state: {sorted(unknown)}")
    if not fields:
        return
    sets = []
    values: List[Any] = []
    for key, value in fields.items():
        if key in _STATE_BOOL_COLUMNS:
            value = 1 if value else 0
        elif key in _STATE_JSON_COLUMNS and isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        elif key == "agy_serie_hasta" and value is not None:
            value = float(value)
        sets.append(f"{key} = ?")
        values.append(value)
    conn = _connect(db_path)
    try:
        with conn:
            conn.execute(f"UPDATE radar_state SET {', '.join(sets)} WHERE id = 1", values)
    finally:
        conn.close()


def bump_counter(name: str, n: int = 1, db_path: Optional[Path] = None) -> Dict[str, int]:
    """Suma `n` al contador diario `name` (atómico) y devuelve los contadores actuales."""
    if name not in COUNTER_NAMES:
        raise ValueError(f"Contador desconocido: '{name}'. Válidos: {list(COUNTER_NAMES)}")
    conn = _connect(db_path)
    try:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            _reset_counters_if_new_day(conn)
            row = conn.execute("SELECT * FROM radar_state WHERE id = 1").fetchone()
            counters = _state_row_to_dict(row)["contadores_dia"]
            counters[name] += n
            conn.execute(
                "UPDATE radar_state SET contadores_dia = ? WHERE id = 1",
                (json.dumps(counters),),
            )
    finally:
        conn.close()
    return counters


# ---------------------------------------------------------------- linkedin_vistos

# Días que se recuerda un urn leído (la búsqueda solo muestra la última semana)
DIAS_VISTOS = 60


def filtrar_no_vistos(urns: Iterable[str], db_path: Optional[Path] = None) -> List[str]:
    """Los urns (en orden, sin repetir) que nunca se leyeron."""
    unicos = list(dict.fromkeys(u for u in urns if u))
    if not unicos:
        return []
    conn = _connect(db_path)
    try:
        vistos = set()
        for i in range(0, len(unicos), 500):
            lote = unicos[i:i + 500]
            marcas = ",".join("?" * len(lote))
            vistos.update(r[0] for r in conn.execute(
                f"SELECT urn FROM linkedin_vistos WHERE urn IN ({marcas})", lote
            ))
    finally:
        conn.close()
    return [u for u in unicos if u not in vistos]


def registrar_vistos(urns: Iterable[str], query: str, db_path: Optional[Path] = None) -> None:
    """Marca los urns como leídos hoy por `query` (los ya vistos conservan su registro)."""
    hoy = _today()
    limite = (date.today() - timedelta(days=DIAS_VISTOS)).isoformat()
    conn = _connect(db_path)
    try:
        with conn:
            conn.executemany(
                "INSERT OR IGNORE INTO linkedin_vistos (urn, query, fecha) VALUES (?, ?, ?)",
                [(u, query, hoy) for u in dict.fromkeys(urns) if u],
            )
            conn.execute("DELETE FROM linkedin_vistos WHERE fecha < ?", (limite,))
    finally:
        conn.close()


def urns_vistos(db_path: Optional[Path] = None) -> Set[str]:
    conn = _connect(db_path)
    try:
        return {r[0] for r in conn.execute("SELECT urn FROM linkedin_vistos")}
    finally:
        conn.close()
