"""Tickets de errores: cada error del portal queda registrado con lo necesario para entenderlo y reproducirlo.

API pública: `reportar_error(componente, tipo, mensaje, ...) -> int` (id del ticket; 0 si no se pudo guardar).
Nunca lanza excepciones ni se llama a sí misma en bucle: registrar un error no puede romper lo que falló.

- Huella = componente + tipo + mensaje normalizado (minúsculas, números → «#», espacios). El mismo error sin resolver
  suma ocurrencias; uno ya resuelto que vuelve reabre el mismo ticket (suma `reaperturas` y toma el commit actual),
  así no se duplica.
- Base propia `<DATA_DIR>/tickets/tickets.db` (SQLite WAL); captura y HTML en `<DATA_DIR>/tickets/<id>/`. Aparte de
  `postulaciones.db`: los tickets no engordan la base ni sus respaldos. Los tests la redirigen (`CARPETA`).
- Los secretos (contraseñas, tokens, cookies, app passwords) se tapan antes de guardar.
- `instalar_logging()` engancha el logging: guarda las últimas líneas en memoria (van en cada ticket) y convierte en
  ticket todo registro ERROR o superior del portal.
- La petición HTTP en curso (la fija el middleware del servidor) va sola en todo ticket creado durante ella.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import traceback
from collections import deque
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from core import database
from core.paths import DATA_DIR

logger = logging.getLogger(__name__)

ESTADOS = ("abierto", "en_curso", "resuelto")
SIN_RESOLVER = ("abierto", "en_curso")
TEXTOS_ESTADO = {"abierto": "Abierto", "en_curso": "En curso", "resuelto": "Resuelto"}

MAX_MENSAJE = 2000
MAX_TRAZA = 30000
MAX_TEXTO_JSON = 20000
MAX_HTML = 5 * 1024 * 1024
MAX_CAPTURA = 8 * 1024 * 1024
LINEAS_DE_LOG = 60
OCURRENCIAS_GUARDADAS = 10
ARCHIVO_CAPTURA = "captura.png"
ARCHIVO_HTML = "pagina.html"

BASE_DIR = Path(__file__).resolve().parent.parent
CARPETA = DATA_DIR / "tickets"
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

# Petición HTTP en curso: {"info": {...}, "tickets": [ids]}. La fija el middleware del servidor.
PETICION_EN_CURSO: ContextVar[Optional[Dict[str, Any]]] = ContextVar("ticket_peticion", default=None)

_dentro = threading.local()
_commit: Optional[str] = None
_lock_commit = threading.Lock()
_LOG: deque = deque(maxlen=300)

# ---------------------------------------------------------------- secretos

_CLAVE_SECRETA = re.compile(
    r"pass(?:word)?|contrase(?:ñ|n)a|token|cookie|secret|app[_ ]?pass|authorization|api[_-]?key|li_at|"
    r"jsessionid|credencial", re.IGNORECASE)
_TAPADO = "***"
_SECRETOS_EN_TEXTO = [
    # "clave": "valor" (JSON)
    (re.compile(r'("(?:[^"]*?(?:' + _CLAVE_SECRETA.pattern + r')[^"]*?)"\s*:\s*)"(?:[^"\\]|\\.)*"', re.IGNORECASE),
     r'\1"' + _TAPADO + '"'),
    # clave=valor (formularios y consultas)
    (re.compile(r'((?:^|[?&;\s,])[\w.-]*(?:' + _CLAVE_SECRETA.pattern + r')[\w.-]*=)[^&;\s,]*', re.IGNORECASE),
     r"\1" + _TAPADO),
    # Encabezados: «Authorization: Bearer x», «Cookie: a=b; c=d»
    (re.compile(r'((?:authorization|cookie|set-cookie)\s*:\s*)[^\r\n]+', re.IGNORECASE), r"\1" + _TAPADO),
]


def tapar_secretos(valor: Any) -> Any:
    """Copia de `valor` con los secretos tapados (dicts por clave, listas, y texto suelto por patrón)."""
    if isinstance(valor, dict):
        return {k: (_TAPADO if isinstance(k, str) and _CLAVE_SECRETA.search(k) else tapar_secretos(v))
                for k, v in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [tapar_secretos(v) for v in valor]
    if isinstance(valor, str):
        for patron, reemplazo in _SECRETOS_EN_TEXTO:
            valor = patron.sub(reemplazo, valor)
        return valor
    return valor


# ---------------------------------------------------------------- contexto

def commit_desplegado() -> str:
    """`git rev-parse HEAD` del código que corre (una vez por proceso)."""
    global _commit
    if _commit is not None:
        return _commit
    with _lock_commit:
        if _commit is None:
            try:
                r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(BASE_DIR), capture_output=True,
                                   text=True, timeout=5, creationflags=CREATE_NO_WINDOW)
                valor = r.stdout.strip() if r.returncode == 0 else ""
            except Exception:
                valor = ""
            _commit = valor or "desconocido"
    return _commit


def ultimas_lineas_de_log(n: int = LINEAS_DE_LOG) -> str:
    return "\n".join(list(_LOG)[-n:])


class _BufferLog(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            linea = f"{datetime.fromtimestamp(record.created).strftime('%H:%M:%S')} {record.levelname} " \
                    f"{record.name}: {record.getMessage()}"
            _LOG.append(tapar_secretos(linea)[:2000])
        except Exception:
            pass


def _componente_de_logger(nombre: str) -> str:
    if nombre.startswith("core."):
        return nombre.split(".", 2)[1]
    return "servidor"


class _TicketsDelLog(logging.Handler):
    """Todo registro ERROR o superior del portal se vuelve ticket (cubre los `logger.exception` existentes)."""

    def emit(self, record: logging.LogRecord) -> None:
        nombre = record.name or ""
        if nombre.startswith("core.tickets") or getattr(record, "sin_ticket", False):
            return
        if not (nombre == "server" or nombre.startswith("core") or nombre in ("root", "__main__", "run")):
            return
        if getattr(_dentro, "activo", False):
            return
        try:
            excepcion = record.exc_info[1] if record.exc_info else None
            reportar_error(_componente_de_logger(nombre), "error_registrado", record.getMessage(),
                           excepcion=excepcion, datos={"logger": nombre, "lugar": f"{record.pathname}:{record.lineno}"})
        except Exception:
            pass


_instalado = False
_BUFFER = _BufferLog(level=logging.DEBUG)


def capturar_logs_de_uvicorn() -> None:
    """uvicorn configura sus loggers sin propagar a la raíz: se les suma el búfer (peticiones recientes en cada
    ticket). Se llama al arrancar el portal, después de que uvicorn configuró el logging. Idempotente."""
    for nombre in ("uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(nombre)
        if _BUFFER not in lg.handlers:
            lg.addHandler(_BUFFER)


def instalar_logging() -> None:
    """Idempotente. Si la raíz no tenía salida, se agrega una a stderr (WARNING) para no perder los avisos."""
    global _instalado
    if _instalado:
        return
    _instalado = True
    raiz = logging.getLogger()
    if not raiz.handlers:
        salida = logging.StreamHandler(sys.stderr)
        salida.setLevel(logging.WARNING)
        raiz.addHandler(salida)
    tickets_log = _TicketsDelLog(level=logging.ERROR)
    raiz.addHandler(_BUFFER)
    raiz.addHandler(tickets_log)
    for nombre in ("core", "server"):
        lg = logging.getLogger(nombre)
        if lg.level == logging.NOTSET:
            lg.setLevel(logging.INFO)


# ---------------------------------------------------------------- base

_TABLA = """
CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    huella TEXT NOT NULL,
    creado_at TEXT NOT NULL,
    ultima_vez TEXT NOT NULL,
    ocurrencias INTEGER NOT NULL DEFAULT 1,
    reaperturas INTEGER NOT NULL DEFAULT 0,
    estado TEXT NOT NULL DEFAULT 'abierto',
    componente TEXT NOT NULL,
    tipo TEXT NOT NULL,
    mensaje TEXT NOT NULL,
    traza TEXT DEFAULT '',
    peticion TEXT DEFAULT 'null',
    datos TEXT DEFAULT '{}',
    log TEXT DEFAULT '',
    pasos TEXT DEFAULT '',
    commit_sha TEXT DEFAULT '',
    captura TEXT DEFAULT '',
    html TEXT DEFAULT '',
    ocurrencias_detalle TEXT DEFAULT '[]',
    resuelto_at TEXT
);
"""


def init_tickets_table(conn) -> None:
    conn.execute(_TABLA)
    columnas = {f[1] for f in conn.execute("PRAGMA table_info(tickets)").fetchall()}
    if "reaperturas" not in columnas:
        conn.execute("ALTER TABLE tickets ADD COLUMN reaperturas INTEGER NOT NULL DEFAULT 0")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tickets_huella ON tickets(huella, estado)")


def carpeta_tickets() -> Path:
    return Path(CARPETA)


def _conexion():
    conn = database.get_db_connection(carpeta_tickets() / "tickets.db")
    init_tickets_table(conn)
    return conn


def _ahora() -> str:
    return datetime.now().isoformat(timespec="seconds")


def normalizar_mensaje(mensaje: str) -> str:
    texto = (mensaje or "").lower()
    texto = re.sub(r"0x[0-9a-f]+", "#", texto)
    texto = re.sub(r"\d+", "#", texto)
    return re.sub(r"\s+", " ", texto).strip()[:500]


def huella(componente: str, tipo: str, mensaje: str) -> str:
    base = f"{componente}|{tipo}|{normalizar_mensaje(mensaje)}"
    return hashlib.sha1(base.encode("utf-8")).hexdigest()


def _json(valor: Any) -> str:
    texto = json.dumps(valor, ensure_ascii=False, default=str)
    if len(texto) > MAX_TEXTO_JSON:
        texto = json.dumps({"recortado": texto[:MAX_TEXTO_JSON]}, ensure_ascii=False)
    return texto


def _cargar_json(texto: Optional[str], defecto: Any) -> Any:
    try:
        return json.loads(texto) if texto else defecto
    except Exception:
        return defecto


# ---------------------------------------------------------------- pasos para reproducir

def armar_pasos(componente: str, tipo: str, mensaje: str, peticion: Optional[dict], datos: Optional[dict]) -> str:
    datos = datos or {}
    pasos: List[str] = []
    if peticion:
        ruta = peticion.get("ruta") or ""
        consulta = peticion.get("consulta") or ""
        linea = f"Con el portal abierto, hacer {peticion.get('metodo', 'GET')} {ruta}{'?' + consulta if consulta else ''}"
        cuerpo = peticion.get("cuerpo")
        if cuerpo not in (None, "", {}):
            linea += f" con este cuerpo: {json.dumps(cuerpo, ensure_ascii=False, default=str)[:1500]}"
        pasos.append(linea + ".")
        if peticion.get("estado_http"):
            pasos.append(f"Se espera una respuesta sin error; se obtuvo {peticion['estado_http']}.")
    if componente == "navegador" or datos.get("vista"):
        if datos.get("url") or datos.get("vista"):
            pasos.append(f"Abrir el portal en {datos.get('url') or '/'} (vista «{datos.get('vista') or 'manual'}»).")
        if datos.get("post_id"):
            pasos.append(f"Abrir el post #{datos['post_id']} del Radar.")
        if datos.get("oferta_id"):
            pasos.append(f"Abrir la oferta #{datos['oferta_id']}.")
        if tipo == "reporte_usuario":
            pasos.append(f"Lo que pasó, según el reporte: {mensaje}")
    if datos.get("consulta"):
        cuenta = datos.get("cuenta") or "principal"
        pasos.append(f"Con la cuenta «{cuenta}», buscar «{datos['consulta']}» en Publicaciones de LinkedIn.")
    elif datos.get("post_id") and componente != "navegador":
        pasos.append(f"Ver el post #{datos['post_id']} del Radar (estado, análisis y CV).")
    if tipo != "reporte_usuario":
        pasos.append(f"Resultado: {mensaje}")
    if not pasos or len(pasos) == 1:
        pasos.append("Revisar la traza y las últimas líneas del log de este ticket.")
    return "\n".join(f"{i}. {p}" for i, p in enumerate(pasos, 1))


# ---------------------------------------------------------------- archivos

def _guardar_captura(destino: Path, captura: Any) -> bool:
    datos: Optional[bytes] = None
    if isinstance(captura, bytes):
        datos = captura
    elif isinstance(captura, Path) or (isinstance(captura, str) and not captura.startswith("data:")):
        origen = Path(captura)
        if origen.is_file() and origen.stat().st_size <= MAX_CAPTURA:
            destino.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(origen, destino)
            return True
        return False
    elif isinstance(captura, str):
        try:
            datos = base64.b64decode(captura.split(",", 1)[1], validate=False)
        except (IndexError, binascii.Error, ValueError):
            return False
    if not datos or len(datos) > MAX_CAPTURA:
        return False
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(datos)
    return True


def _guardar_html(destino: Path, html: Any) -> bool:
    if isinstance(html, Path) or (isinstance(html, str) and len(html) < 1000 and "<" not in html
                                  and Path(html).is_file()):
        origen = Path(html)
        if not origen.is_file():
            return False
        texto = origen.read_text(encoding="utf-8", errors="replace")
    elif isinstance(html, bytes):
        texto = html.decode("utf-8", errors="replace")
    else:
        texto = str(html)
    if not texto:
        return False
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(texto[:MAX_HTML], encoding="utf-8")
    return True


def ruta_captura(tid: int) -> Optional[Path]:
    p = carpeta_tickets() / str(int(tid)) / ARCHIVO_CAPTURA
    return p if p.is_file() else None


def ruta_html(tid: int) -> Optional[Path]:
    p = carpeta_tickets() / str(int(tid)) / ARCHIVO_HTML
    return p if p.is_file() else None


# ---------------------------------------------------------------- registrar

def _traza(excepcion: Optional[BaseException]) -> str:
    if excepcion is None:
        return ""
    texto = "".join(traceback.format_exception(type(excepcion), excepcion, excepcion.__traceback__))
    return tapar_secretos(texto)[-MAX_TRAZA:]


def _guardar(componente: str, tipo: str, mensaje: str, traza: str, peticion: Optional[dict],
             datos: Optional[dict], captura: Any, html: Any, pasos: Optional[str]) -> int:
    ahora = _ahora()
    h = huella(componente, tipo, mensaje)
    detalle = {"fecha": ahora, "datos": datos or {}}
    if peticion:
        detalle["peticion"] = {k: peticion.get(k) for k in ("metodo", "ruta", "consulta", "estado_http")}
    conn = _conexion()
    try:
        with conn:
            fila = conn.execute(
                "SELECT id, estado, ocurrencias, ocurrencias_detalle, captura, html, traza FROM tickets "
                "WHERE huella = ? ORDER BY id DESC LIMIT 1", (h,)).fetchone()
            if fila:
                tid = int(fila["id"])
                if fila["estado"] == "resuelto":
                    # Volvió después de resuelto: se reabre el mismo ticket con la traza, el log y el commit de ahora.
                    detalle["reapertura"] = True
                    conn.execute(
                        "UPDATE tickets SET estado = 'abierto', resuelto_at = NULL, reaperturas = reaperturas + 1, "
                        "commit_sha = ?, log = ?, traza = CASE WHEN ? = '' THEN traza ELSE ? END WHERE id = ?",
                        (commit_desplegado(), ultimas_lineas_de_log(), traza, traza, tid))
                previas = _cargar_json(fila["ocurrencias_detalle"], [])
                previas = (previas + [detalle])[-OCURRENCIAS_GUARDADAS:]
                conn.execute(
                    "UPDATE tickets SET ocurrencias = ocurrencias + 1, ultima_vez = ?, ocurrencias_detalle = ?, "
                    "traza = CASE WHEN traza = '' THEN ? ELSE traza END WHERE id = ?",
                    (ahora, _json(previas), traza, tid))
                faltan_archivos = (captura is not None and not fila["captura"]) or (html is not None and not fila["html"])
            else:
                cur = conn.execute(
                    "INSERT INTO tickets (huella, creado_at, ultima_vez, ocurrencias, estado, componente, tipo, mensaje,"
                    " traza, peticion, datos, log, pasos, commit_sha, ocurrencias_detalle) "
                    "VALUES (?, ?, ?, 1, 'abierto', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (h, ahora, ahora, componente, tipo, mensaje, traza, _json(peticion), _json(datos or {}),
                     ultimas_lineas_de_log(), pasos or armar_pasos(componente, tipo, mensaje, peticion, datos),
                     commit_desplegado(), _json([detalle])))
                tid = int(cur.lastrowid)
                faltan_archivos = captura is not None or html is not None
        if faltan_archivos:
            carpeta = carpeta_tickets() / str(tid)
            cambios = {}
            if captura is not None:
                try:
                    if _guardar_captura(carpeta / ARCHIVO_CAPTURA, captura):
                        cambios["captura"] = ARCHIVO_CAPTURA
                except Exception:
                    pass
            if html is not None:
                try:
                    if _guardar_html(carpeta / ARCHIVO_HTML, html):
                        cambios["html"] = ARCHIVO_HTML
                except Exception:
                    pass
            if cambios:
                with conn:
                    conn.execute(f"UPDATE tickets SET {', '.join(f'{k} = ?' for k in cambios)} WHERE id = ?",
                                 (*cambios.values(), tid))
        return tid
    finally:
        conn.close()


def reportar_error(componente: str, tipo: str, mensaje: str, *, excepcion: Optional[BaseException] = None,
                   peticion: Optional[dict] = None, datos: Optional[dict] = None, captura: Any = None,
                   html: Any = None, pasos: Optional[str] = None, traza: Optional[str] = None) -> int:
    """Registra un error (o suma una ocurrencia al ticket abierto del mismo error). Devuelve el id, o 0 si falló.

    - `excepcion`: su traza completa va al ticket.
    - `peticion`: {metodo, ruta, consulta, cuerpo, estado_http, respuesta}; si falta, va la petición HTTP en curso.
    - `datos`: lo involucrado (post_id, oferta_id, consulta, cuenta, vista…).
    - `captura`: ruta a un PNG, bytes o data URL. `html`: ruta a un archivo o el HTML como texto.
    - `pasos`: pasos para reproducir; si faltan, se arman con la petición y los datos.
    - `traza`: traza en texto, cuando no hay una excepción de Python (p. ej. un error de JavaScript).
    """
    if getattr(_dentro, "activo", False):
        return 0
    _dentro.activo = True
    try:
        en_curso = PETICION_EN_CURSO.get()
        if peticion is None and en_curso is not None:
            peticion = en_curso.get("info")
        componente = (str(componente or "desconocido").strip() or "desconocido")[:60]
        tipo = (str(tipo or "error").strip() or "error")[:60]
        mensaje = tapar_secretos(str(mensaje or "").strip() or "(sin mensaje)")[:MAX_MENSAJE]
        texto_traza = _traza(excepcion) or tapar_secretos(str(traza or ""))[-MAX_TRAZA:]
        tid = _guardar(componente, tipo, mensaje, texto_traza,
                       tapar_secretos(peticion) if peticion else None,
                       tapar_secretos(datos) if datos else None, captura, html, pasos)
        if en_curso is not None and tid:
            en_curso.setdefault("tickets", []).append(tid)
        return tid
    except Exception:
        try:
            logger.warning("No se pudo registrar un ticket", exc_info=True, extra={"sin_ticket": True})
        except Exception:
            pass
        return 0
    finally:
        _dentro.activo = False


# ---------------------------------------------------------------- consultar

_COLUMNAS_LISTA = ("id, creado_at, ultima_vez, ocurrencias, reaperturas, estado, componente, tipo, mensaje, captura, html")


def _fila(fila) -> Dict[str, Any]:
    d = dict(fila)
    for col, defecto in (("peticion", None), ("datos", {}), ("ocurrencias_detalle", [])):
        if col in d:
            d[col] = _cargar_json(d[col], defecto)
    if "commit_sha" in d:
        d["commit"] = d.pop("commit_sha")
    d["tiene_captura"] = bool(d.pop("captura", ""))
    d["tiene_html"] = bool(d.pop("html", ""))
    d.pop("huella", None)
    return d


def listar(estado: Optional[str] = None, componente: Optional[str] = None, tipo: Optional[str] = None,
           limite: int = 500) -> List[Dict[str, Any]]:
    condiciones, valores = [], []
    if estado == "sin_resolver":
        condiciones.append("estado IN ('abierto', 'en_curso')")
    elif estado:
        condiciones.append("estado = ?"); valores.append(estado)
    if componente:
        condiciones.append("componente = ?"); valores.append(componente)
    if tipo:
        condiciones.append("tipo = ?"); valores.append(tipo)
    donde = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
    conn = _conexion()
    try:
        filas = conn.execute(f"SELECT {_COLUMNAS_LISTA} FROM tickets {donde} ORDER BY ultima_vez DESC, id DESC "
                             f"LIMIT ?", (*valores, int(limite))).fetchall()
    finally:
        conn.close()
    return [_fila(f) for f in filas]


def obtener(tid: int) -> Optional[Dict[str, Any]]:
    conn = _conexion()
    try:
        fila = conn.execute("SELECT * FROM tickets WHERE id = ?", (int(tid),)).fetchone()
    finally:
        conn.close()
    return _fila(fila) if fila else None


def cambiar_estado(tid: int, estado: str) -> bool:
    if estado not in ESTADOS:
        raise ValueError(f"Estado inválido: {estado}")
    conn = _conexion()
    try:
        with conn:
            cur = conn.execute("UPDATE tickets SET estado = ?, resuelto_at = ? WHERE id = ?",
                               (estado, _ahora() if estado == "resuelto" else None, int(tid)))
        return cur.rowcount > 0
    finally:
        conn.close()


def resumen() -> Dict[str, Any]:
    conn = _conexion()
    try:
        por_estado = {e: 0 for e in ESTADOS}
        for fila in conn.execute("SELECT estado, COUNT(*) AS n FROM tickets GROUP BY estado"):
            por_estado[fila["estado"]] = int(fila["n"])
        componentes = [f[0] for f in conn.execute("SELECT DISTINCT componente FROM tickets ORDER BY componente")]
        tipos = [f[0] for f in conn.execute("SELECT DISTINCT tipo FROM tickets ORDER BY tipo")]
    finally:
        conn.close()
    return {**por_estado, "sin_resolver": por_estado["abierto"] + por_estado["en_curso"],
            "componentes": componentes, "tipos": tipos}


def texto_ticket(t: Dict[str, Any]) -> str:
    """El ticket entero en texto plano, para copiarlo y pegarlo en un chat."""
    def bloque(titulo: str, contenido: Any) -> str:
        if contenido in (None, "", {}, []):
            return ""
        if not isinstance(contenido, str):
            contenido = json.dumps(contenido, ensure_ascii=False, indent=2, default=str)
        return f"\n--- {titulo} ---\n{contenido}\n"

    pet = t.get("peticion") or {}
    linea_pet = ""
    if pet:
        linea_pet = f"{pet.get('metodo', '')} {pet.get('ruta', '')}{'?' + pet['consulta'] if pet.get('consulta') else ''}"
        if pet.get("estado_http"):
            linea_pet += f" → {pet['estado_http']}"
    partes = [
        f"Ticket #{t['id']} · {TEXTOS_ESTADO.get(t['estado'], t['estado'])}",
        f"Componente: {t['componente']}",
        f"Tipo: {t['tipo']}",
        f"Mensaje: {t['mensaje']}",
        f"Primera vez: {t['creado_at']} · Última vez: {t['ultima_vez']} · Ocurrencias: {t['ocurrencias']}",
        f"Commit: {t.get('commit') or 'desconocido'}",
    ]
    if t.get("reaperturas"):
        n = t["reaperturas"]
        partes.insert(5, f"Reabierto {n} {'vez' if n == 1 else 'veces'}: volvió después de marcarse resuelto")
    if linea_pet:
        partes.append(f"Petición: {linea_pet}")
    archivos = []
    if t.get("tiene_captura"):
        archivos.append(f"captura: tickets/{t['id']}/{ARCHIVO_CAPTURA}")
    if t.get("tiene_html"):
        archivos.append(f"HTML: tickets/{t['id']}/{ARCHIVO_HTML}")
    if archivos:
        partes.append("Archivos (en la carpeta de datos): " + " · ".join(archivos))
    texto = "\n".join(partes) + "\n"
    texto += bloque("Pasos para reproducir", t.get("pasos"))
    texto += bloque("Datos involucrados", t.get("datos"))
    texto += bloque("Petición completa", pet)
    texto += bloque("Traza", t.get("traza"))
    texto += bloque("Ocurrencias recientes", t.get("ocurrencias_detalle"))
    texto += bloque("Últimas líneas del log", t.get("log") or "(sin líneas)")
    return texto
