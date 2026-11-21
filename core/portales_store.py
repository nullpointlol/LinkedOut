"""Persistencia de Portales: ofertas de Jooble e Indeed en `linkedin_posts` y sus consultas.

- Las ofertas van a la misma tabla que las del Radar, con `fuente` = portal y `query` = nombre de la búsqueda, y siguen
  el mismo circuito (clasificación, análisis, CV y mail).
- Duplicados: una oferta ya vista (`portales_vistos`) no vuelve a entrar; una con el mismo link o el mismo título y
  empresa que otra de un portal se une a esa (se le suma el link) en vez de entrar de nuevo.
- `portales_lecturas`: cada consulta, con lo que trajo y su error. Ritmo y consultas por día se miden ahí.
"""
import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core import radar_store
from core.portales import texto_de_oferta
from core.reglas_portales import NOMBRES

NUEVA, DUPLICADA, VISTA = "nueva", "duplicada", "vista"


def ahora_iso(momento: Optional[datetime] = None) -> str:
    """Instante en UTC con segundos: así las fechas de `portales_lecturas` se comparan como texto."""
    momento = momento or datetime.now(timezone.utc)
    if momento.tzinfo is None:
        momento = momento.astimezone()
    return momento.astimezone(timezone.utc).isoformat(timespec="seconds")


def _normal(texto: Any) -> str:
    sin_tildes = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode("ascii")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", sin_tildes.casefold()).split())


def _link_normal(link: str) -> str:
    return (link or "").strip().rstrip("/")


def _duplicado(conn, oferta: Dict[str, Any]) -> Optional[int]:
    links = [l for l in {_link_normal(oferta.get("link")), _link_normal(oferta.get("link_directo"))} if l]
    for link in links:
        fila = conn.execute(
            "SELECT id FROM linkedin_posts WHERE rtrim(post_url, '/') = ? OR instr(links, ?) > 0 "
            "ORDER BY id LIMIT 1", (link, json.dumps(link)[1:-1])).fetchone()
        if fila:
            return fila[0]
    titulo, empresa = _normal(oferta.get("titulo")), _normal(oferta.get("empresa"))
    if not titulo or not empresa:
        return None
    marcas = ", ".join("?" * len(radar_store.FUENTES_PORTALES))
    for fila in conn.execute(
            f"SELECT id, cargo_autor, autor FROM linkedin_posts WHERE fuente IN ({marcas}) ORDER BY id",
            radar_store.FUENTES_PORTALES):
        if _normal(fila[1]) == titulo and _normal(fila[2]) == empresa:
            return fila[0]
    return _duplicado_aproximado(conn, titulo, empresa)


def _compacta(texto: str) -> str:
    from rapidfuzz import utils
    return re.sub(r"[^0-9a-z]", "", utils.default_process(texto))


def _duplicado_aproximado(conn, titulo: str, empresa: str) -> Optional[int]:
    """La misma oferta con otras palabras: misma empresa y título parecido (RapidFuzz,
    token_set_ratio ≥ el umbral del perfil) en los últimos días del perfil, entre portales y contra LinkedIn (su puesto
    y su empresa salen de la clasificación). Sin la regla `duplicados` en el perfil, no se compara."""
    from rapidfuzz import fuzz, utils
    from core.reglas_portales import leer_duplicados
    regla = leer_duplicados()
    if regla is None:
        return None
    filas = conn.execute(
        "SELECT id, fuente, cargo_autor, autor, triage FROM linkedin_posts "
        "WHERE datetime(created_at) >= datetime('now', ?) ORDER BY id", (f"-{regla.dias} days",)).fetchall()
    for pid, fuente, cargo, autor, triage in filas:
        if fuente in radar_store.FUENTES_PORTALES:
            otro_titulo, otra_empresa = cargo, autor
        else:
            try:
                datos = json.loads(triage or "{}")
            except ValueError:
                continue
            otro_titulo, otra_empresa = (datos or {}).get("puesto"), (datos or {}).get("empresa")
        otro_titulo, otra_empresa = _normal(otro_titulo), _normal(otra_empresa)
        if not otro_titulo or not otra_empresa:
            continue
        # La empresa, sin puntuación ni espacios: «Acme S.A.» y «ACME SA» son la misma
        if (fuzz.ratio(empresa, otra_empresa, processor=_compacta) >= regla.umbral_titulo
                and fuzz.token_set_ratio(titulo, otro_titulo, processor=utils.default_process) >= regla.umbral_titulo):
            return pid
    return None


def guardar_oferta(oferta: Dict[str, Any], busqueda: str, db_path: Optional[Path] = None) -> Tuple[str, Optional[int]]:
    """Guarda una oferta normalizada de core/portales. Devuelve (NUEVA | DUPLICADA | VISTA, id del post)."""
    urn = oferta["urn"]
    conn = radar_store._connect(db_path)
    try:
        fila = conn.execute("SELECT post_id FROM portales_vistos WHERE urn = ?", (urn,)).fetchone()
        if fila:
            return VISTA, fila[0]
        existente = _duplicado(conn, oferta)
        if existente is not None:
            actual = conn.execute("SELECT links FROM linkedin_posts WHERE id = ?", (existente,)).fetchone()
            try:
                links = json.loads(actual[0] or "[]") if actual else []
            except ValueError:
                links = []
            conocidos = {_link_normal(l) for l in links if isinstance(l, str)}
            nuevos = []
            for link in (oferta.get("link_directo"), oferta.get("link")):
                if link and _link_normal(link) not in conocidos:
                    conocidos.add(_link_normal(link))
                    nuevos.append(link)
            with conn:
                if nuevos:
                    conn.execute("UPDATE linkedin_posts SET links = ? WHERE id = ?",
                                 (json.dumps([*links, *nuevos], ensure_ascii=False), existente))
                conn.execute("INSERT OR IGNORE INTO portales_vistos (urn, post_id) VALUES (?, ?)", (urn, existente))
            return DUPLICADA, existente
    finally:
        conn.close()
    post_id = radar_store.insert_post_if_new({
        "urn": urn, "url": "", "autor": oferta.get("empresa") or NOMBRES.get(oferta["portal"], oferta["portal"]),
        "cargo_autor": oferta.get("titulo") or "", "texto": texto_de_oferta(oferta), "fecha_rel": "",
        "emails": oferta.get("emails") or [], "post_url": oferta["link"],
        "links": [l for l in (oferta.get("link_directo"), oferta["link"]) if l],
    }, busqueda, fuente=oferta["portal"], db_path=db_path)
    conn = radar_store._connect(db_path)
    try:
        with conn:
            if post_id is None:  # el urn ya estaba en la tabla (vista antes de esta versión)
                fila = conn.execute("SELECT id FROM linkedin_posts WHERE urn = ?", (urn,)).fetchone()
                post_id = fila[0] if fila else None
                conn.execute("INSERT OR IGNORE INTO portales_vistos (urn, post_id) VALUES (?, ?)", (urn, post_id))
                return VISTA, post_id
            conn.execute("INSERT OR IGNORE INTO portales_vistos (urn, post_id) VALUES (?, ?)", (urn, post_id))
    finally:
        conn.close()
    return NUEVA, post_id


def registrar_lectura(portal: str, busqueda: str, palabras: str, inicio: str, fin: str, traidas: int = 0,
                      nuevas: int = 0, duplicadas: int = 0, error: Optional[str] = None,
                      db_path: Optional[Path] = None) -> None:
    conn = radar_store._connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO portales_lecturas (portal, busqueda, palabras, inicio, fin, traidas, nuevas, duplicadas, "
                "error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (portal, busqueda, palabras, inicio, fin, traidas, nuevas, duplicadas, error))
    finally:
        conn.close()


def consultas_desde(portal: str, desde: str, db_path: Optional[Path] = None) -> int:
    """Consultas a `portal` (con o sin error: el portal las cuenta igual) desde `desde` (ahora_iso)."""
    conn = radar_store._connect(db_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM portales_lecturas WHERE portal = ? AND inicio >= ?",
                            (portal, desde)).fetchone()[0]
    finally:
        conn.close()


def ultima_consulta(portal: str, db_path: Optional[Path] = None) -> Optional[str]:
    conn = radar_store._connect(db_path)
    try:
        return conn.execute("SELECT MAX(inicio) FROM portales_lecturas WHERE portal = ?", (portal,)).fetchone()[0]
    finally:
        conn.close()


def ultimas_por_busqueda(portal: str, db_path: Optional[Path] = None) -> Dict[str, str]:
    """{busqueda: inicio de su última consulta en `portal`}: la que hace más que no se lee va primero."""
    conn = radar_store._connect(db_path)
    try:
        return {b: i for b, i in conn.execute(
            "SELECT busqueda, MAX(inicio) FROM portales_lecturas WHERE portal = ? GROUP BY busqueda", (portal,))}
    finally:
        conn.close()


def lecturas(limite: int = 50, db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Las últimas consultas a los portales (para el panel y el informe)."""
    conn = radar_store._connect(db_path)
    try:
        filas = conn.execute(
            "SELECT portal, busqueda, palabras, inicio, fin, traidas, nuevas, duplicadas, error "
            "FROM portales_lecturas ORDER BY id DESC LIMIT ?", (int(limite),)).fetchall()
    finally:
        conn.close()
    claves = ("portal", "busqueda", "palabras", "inicio", "fin", "traidas", "nuevas", "duplicadas", "error")
    return [dict(zip(claves, f)) for f in filas]


# ------------------------------------------------------------------ pausa de cada radar de portal (desde el panel)

MOTIVO_PAUSA_MANUAL = "Pausado desde el panel"


MOTIVO_PAUSA_VERIFICACION = "Pidió una verificación (captcha): pausado hasta mañana"


def manana(momento: Optional[datetime] = None) -> datetime:
    """La próxima medianoche local."""
    local = (momento or datetime.now(timezone.utc)).astimezone()
    return (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


def pausar(portal: str, motivo: str = MOTIVO_PAUSA_MANUAL, hasta: Optional[datetime] = None,
           db_path: Optional[Path] = None) -> None:
    """Pausa el radar del portal; con `hasta`, se reanuda solo a esa hora."""
    conn = radar_store._connect(db_path)
    try:
        with conn:
            conn.execute("INSERT INTO portales_pausa (portal, motivo, desde, hasta) VALUES (?, ?, ?, ?) "
                         "ON CONFLICT(portal) DO UPDATE SET motivo = excluded.motivo, desde = excluded.desde, "
                         "hasta = excluded.hasta",
                         (portal, motivo, ahora_iso(), ahora_iso(hasta) if hasta else None))
    finally:
        conn.close()


def reanudar(portal: str, db_path: Optional[Path] = None) -> None:
    conn = radar_store._connect(db_path)
    try:
        with conn:
            conn.execute("DELETE FROM portales_pausa WHERE portal = ?", (portal,))
    finally:
        conn.close()


def pausados(db_path: Optional[Path] = None) -> Dict[str, Dict[str, str]]:
    """{portal: {motivo, desde, hasta}} de los radares de portal pausados; una pausa vencida se levanta."""
    conn = radar_store._connect(db_path)
    try:
        with conn:
            conn.execute("DELETE FROM portales_pausa WHERE hasta IS NOT NULL AND hasta <= ?", (ahora_iso(),))
        return {p: {"motivo": m, "desde": d, "hasta": h}
                for p, m, d, h in conn.execute("SELECT portal, motivo, desde, hasta FROM portales_pausa")}
    finally:
        conn.close()


def ya_vistos(urns: List[str], db_path: Optional[Path] = None) -> set:
    """Los `urns` que ya entraron alguna vez (no se vuelve a pedir su aviso)."""
    lista = list(dict.fromkeys(u for u in urns if u))
    if not lista:
        return set()
    conn = radar_store._connect(db_path)
    try:
        return {u for (u,) in conn.execute(
            f"SELECT urn FROM portales_vistos WHERE urn IN ({','.join('?' * len(lista))})", lista)}
    finally:
        conn.close()
