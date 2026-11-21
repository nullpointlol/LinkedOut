"""API HTTP del Radar LinkedIn (spec §4): `build_radar_router(get_radar) -> APIRouter`.

Las rutas son finas: validan, hacen las transiciones de estado que le pertenecen a la API
(aprobar, editar, descartar, forzar, regenerar) y delegan en el `RadarLoop` lo que es del
loop (iniciar/detener, encolar envíos, reintentar, despertar la generación).

Invariante de aprobación: solo `approve` / `approve-all` setean `aprobado_at`, y únicamente
sobre posts `LISTO` con destinatario, asunto y cuerpo. Toda ruta que mueve un post a otro
estado limpia `aprobado_at`, así nada se envía sin una aprobación explícita del contenido actual.

El radar se obtiene con `get_radar()` en cada request (dependencia de FastAPI): reemplazar
`server.radar_loop` (p. ej. en tests) tiene efecto aunque el router ya esté construido.

Solo operación local (`_solo_operacion_local`): el portal escucha en 0.0.0.0 y se publica por un
túnel de Cloudflare para el celular, pero las rutas que modifican algo (POST/PATCH/…) pueden
cambiar el destinatario y aprobar envíos desde el Gmail del usuario. Por eso se rechazan con 403
salvo que la request venga de la propia PC (loopback, sin cabeceras de Cloudflare, con `Host`
local) y no sea cross-site (CSRF / DNS rebinding). Las rutas GET quedan abiertas.

Excepción: un celular emparejado (`core.acceso_remoto`, QR que muestra la PC) trae la cookie
`radar_acceso` con la clave secreta y opera igual que la PC, por la red local o el túnel. Para él
solo se exige que la request no sea cross-site ni de otro origen (la cookie además es SameSite=Strict).
"""
import asyncio
import functools
import gzip
import json
import ipaddress
import logging
import copy
from pathlib import Path
import re
from datetime import datetime
import time
from typing import Annotated, Any, Callable, Dict, List, Optional, Set, Tuple
from urllib.parse import urlsplit

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi import Path as ParametroRuta
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel, Field, StrictBool
from pydantic_core import to_json

# Ids y límites dentro del rango de un INTEGER de SQLite: fuera de él sqlite3 lanza OverflowError (500).
# Con este tipo, un número fuera de rango es una entrada inválida (422).
MAX_ENTERO_SQLITE = 2**63 - 1
IdSqlite = Annotated[int, ParametroRuta(ge=-MAX_ENTERO_SQLITE - 1, le=MAX_ENTERO_SQLITE)]
EnteroSqlite = Annotated[int, Field(ge=-MAX_ENTERO_SQLITE - 1, le=MAX_ENTERO_SQLITE)]

from core import acceso_remoto, auditoria_envio, database, descartes, radar_store, revision_store
from core.prioridades import texto_capturado
from core.aprendizajes import _clave as clave_requisito, registrar_aprendizaje
from core.descartes import motivos_descarte
from core.strategist import calculate_match_score_with_breakdown
from core.bridge import CUOTA, sondear_cuota
from core.cv_guardas import FORMATOS_CV
from core.mailer import load_gmail_config
from core.paths import DATA_DIR
from core.radar import MOTIVO_NO_OFERTA, PRIORIDAD_FORZADA, pausa_busquedas
from core.triage import ALCANCES, _senal_argentina

logger = logging.getLogger(__name__)

# Descartar una oferta solo desde el portal (aparecieron 107 ofertas descartadas «a mano» que nadie
# descartó, 9 en el mismo segundo). El portal manda esta cabecera; un pedido sin ella (un script, un agente) se
# rechaza y deja un ticket
CABECERA_ORIGEN = "X-LinkedOut-Origen"
ORIGEN_PORTAL = "portal"
DESCARTE_DESDE = ("detalle", "duplicado")

ESTADOS_POST = (
    "NUEVO", "OFERTA", "ANALIZANDO", "ANALIZADA", "DESCARTADO", "REVISAR",
    "GENERANDO", "LISTO", "ENVIANDO", "ENVIADO", "ERROR", "IGNORADA",
)
DIAS_DUPLICADO = 30
GMAIL_NO_CONFIGURADO = "Gmail no configurado: completá GMAIL_ADDRESS y GMAIL_APP_PASSWORD en .env"
LARGO_EXTRACTO = 280
# Resúmenes de `analisis` en memoria, por su JSON: cubre las tres listas (unas 4000 ofertas) sin
# decodificar de nuevo lo que no cambió
ANALISIS_EN_CACHE = 6000
# Las posiciones de las colas se reusan mientras ninguna candidata cambie, hasta este tope: la edad de
# cada oferta corre sola y el perfil puede cambiar el grupo de rol
VIGENCIA_LUGARES_COLA_S = 60
# La lista pesa varios MB: comprimida viaja en una fracción (pesa en el celular, por el túnel)
MINIMO_GZIP = 1024
NIVEL_GZIP = 5

_FMT_FECHA = "%Y-%m-%d %H:%M:%S"  # hora local, igual que el RadarLoop
_EMAIL_RE = re.compile(r"^[^@\s,;<>()]+@[^@\s,;<>()]+\.[^@\s,;<>()]+$")


SOLO_LOCAL = (
    "El Radar solo se puede operar desde la PC donde corre el portal o desde un celular emparejado: "
    "en la PC tocá «Usar en el celular» y escaneá el QR"
)
_METODOS_SEGUROS = ("GET", "HEAD", "OPTIONS")
_NOMBRES_LOCALES = {"localhost"}
_CABECERAS_CLOUDFLARE = ("cf-connecting-ip", "cf-ray")

# Hook SOLO para tests: `app.state.radar_hosts_de_test` (set de str) agrega nombres que se aceptan
# como locales para el cliente y el `Host` (TestClient usa "testclient" / "testserver"). En
# producción nadie lo setea, así que solo pasan loopback y localhost.
ESTADO_HOSTS_DE_TEST = "radar_hosts_de_test"


# Vista Portales: `fuente=portales` lista las ofertas de todos los portales juntas
FUENTE_PORTALES = "portales"


def _es_loopback(host: Optional[str]) -> bool:
    if not host:
        return False
    host = host.strip().strip("[]").lower()
    if host in _NOMBRES_LOCALES:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    mapeada = getattr(ip, "ipv4_mapped", None)
    return ip.is_loopback or bool(mapeada and mapeada.is_loopback)


def _host_puerto(netloc: str, scheme: str = "http") -> Optional[Tuple[str, int]]:
    """'localhost:8000' → ('localhost', 8000); sin puerto usa el del esquema. None si es inválido."""
    try:
        partes = urlsplit(f"{scheme}://{netloc}")
        host = (partes.hostname or "").lower()
        puerto = partes.port or (443 if scheme == "https" else 80)
    except ValueError:
        return None
    return (host, puerto) if host else None


def _motivo_red_no_local(request: Request) -> Optional[str]:
    """Por qué una request no llega de la propia PC por la red (None si es local): cliente, túnel y Host."""
    extra = {h.lower() for h in (getattr(request.app.state, ESTADO_HOSTS_DE_TEST, None) or ())}
    es_local = lambda h: _es_loopback(h) or (h or "").strip().lower() in extra  # noqa: E731

    cliente = request.client.host if request.client else ""
    if not es_local(cliente):
        return f"cliente no local ({cliente or 'desconocido'})"
    headers = request.headers
    if any(c in headers for c in _CABECERAS_CLOUDFLARE):
        # cloudflared se conecta desde localhost: estas cabeceras delatan al túnel
        return "request recibida por el túnel de Cloudflare"
    host_hdr = headers.get("host") or ""
    host = _host_puerto(host_hdr, request.url.scheme)
    if host is None or not es_local(host[0]):
        # DNS rebinding: un dominio ajeno que resuelve a 127.0.0.1
        return f"Host no local ({host_hdr or 'vacío'})"
    return None


def _motivo_no_local(request: Request) -> Optional[str]:
    """Por qué una request no viene de la propia PC (None si es local)."""
    motivo = _motivo_red_no_local(request)
    if motivo:
        return motivo
    headers = request.headers
    if (headers.get("sec-fetch-site") or "").strip().lower() == "cross-site":
        return "request cross-site"
    host = _host_puerto(headers.get("host") or "", request.url.scheme)
    origin = headers.get("origin")
    if origin is not None:
        try:
            o = urlsplit(origin.strip())
            origen = _host_puerto(o.netloc, o.scheme) if o.scheme in ("http", "https") and o.netloc else None
        except ValueError:
            origen = None
        if origen != host:
            return f"origen distinto ({origin})"
    return None


def _motivo_emparejado(request: Request) -> Optional[str]:
    """Por qué se rechaza una request que no es local (None si viene de un celular emparejado)."""
    if not acceso_remoto.es_valida(request.cookies.get(acceso_remoto.COOKIE)):
        return "dispositivo no emparejado"
    headers = request.headers
    if (headers.get("sec-fetch-site") or "").strip().lower() == "cross-site":
        return "request cross-site"
    origin = headers.get("origin")
    if origin is not None:
        # Solo el nombre: detrás del túnel el Origin es https y el Host llega como http
        try:
            origen = urlsplit(origin.strip()).hostname
            host = urlsplit(f"http://{headers.get('host') or ''}").hostname
        except ValueError:
            origen = host = None
        if not origen or origen != host:
            return f"origen distinto ({origin})"
    return None


def _exigir_pc_o_emparejado(request: Request) -> None:
    """403 salvo que la request venga de la propia PC o de un celular emparejado."""
    motivo = _motivo_no_local(request)
    if motivo and _motivo_emparejado(request) is None:
        return
    if motivo:
        logger.warning(f"[Radar API] Rechazado {request.method} {request.url.path}: {motivo}")
        raise HTTPException(status_code=403, detail=f"{SOLO_LOCAL} ({motivo}).")


async def _solo_operacion_local(request: Request) -> None:
    """Dependencia: las rutas que modifican algo solo se aceptan desde la propia PC
    o desde un celular emparejado; las lecturas pasan (para eso está `_solo_pc_o_emparejado`)."""
    if request.method.upper() in _METODOS_SEGUROS:
        return
    _exigir_pc_o_emparejado(request)


async def _solo_pc_o_emparejado(request: Request) -> None:
    """Dependencia de toda la API (S1, repo público): lecturas y escrituras solo desde la propia PC
    o desde un celular emparejado. Las lecturas también, porque devuelven datos del candidato."""
    _exigir_pc_o_emparejado(request)


# ----------------------------------------------------------------- modelos de entrada

class ConfigIn(BaseModel):
    umbral_match: Optional[int] = Field(default=None, ge=0, le=100)
    modo_serie: Optional[bool] = None


class RequisitoIn(BaseModel):
    nombre: str = Field(min_length=1, max_length=300)
    eliminatorio: Optional[bool] = None


# Un clic en un requisito: brecha → transferible → coincidencia → brecha (igual que en Postulaciones)
SIGUIENTE_ESTADO_REQUISITO = {
    "BRECHA_REAL": "TRANSFERIBLE", "TRANSFERIBLE": "MATCH_DIRECTO", "MATCH_DIRECTO": "BRECHA_REAL",
}


def _ciclar_requisito(requisitos: Any, nombre: str) -> Optional[Dict[str, Any]]:
    """Pasa el requisito `nombre` a su estado siguiente. None si no está."""
    for req in requisitos if isinstance(requisitos, list) else []:
        if isinstance(req, dict) and clave_requisito(req.get("nombre")) == clave_requisito(nombre):
            nuevo = SIGUIENTE_ESTADO_REQUISITO.get(req.get("estado"), "TRANSFERIBLE")
            req.update(estado=nuevo, approved=nuevo != "BRECHA_REAL", aprendido=True)
            if nuevo != "BRECHA_REAL":
                # La marca del usuario anula la brecha dura que puso una regla
                req.pop("dura", None)
            return req
    return None


def _recalcular_descarte(analisis: dict, texto_oferta: str, *, manual: bool = False, contacto_local: str = "") -> None:
    """Reconfirma al modelo y respeta la decisión explícita del usuario."""
    restricciones = descartes.cargar_restricciones()
    motivos = []
    for req in analisis.get("requisitos") or []:
        if not isinstance(req, dict):
            continue
        if isinstance(req.get("eliminatorio_usuario"), bool):
            motivos.extend(m for m in motivos_descarte({**analisis, "requisitos": [req]}, restricciones)
                           if m not in motivos)
            continue
        texto = req.get("texto", req.get("nombre", ""))
        if not isinstance(texto, str) or not texto.strip():
            continue
        posiciones = list(re.finditer(re.escape(texto), texto_oferta))
        if not posiciones:
            continue
        confirmados = motivos_descarte({**analisis, "requisitos": [req]}, restricciones)
        for pos in posiciones:
            inicio = max(texto_oferta.rfind(s, 0, pos.start()) for s in "\n;.!?") + 1
            finales = [p for s in "\n;.!?" if (p := texto_oferta.find(s, pos.end())) >= 0]
            contexto = texto_oferta[inicio:min(finales) if finales else len(texto_oferta)].strip()
            categorias = {m["categoria"] for m in motivos_descarte(
                {**analisis, "requisitos": [{**req, "texto": contexto}]}, restricciones)}
            confirmados = [m for m in confirmados if m["categoria"] in categorias]
        motivos.extend(m for m in confirmados if m not in motivos)
    desde = (analisis.pop("no_apto", None) or {}).get("desde")
    if motivos:
        analisis["no_apto"] = {"motivos": motivos}
    descartes.aplicar_condicion_ubicacion(analisis, texto_oferta, restricciones, manual=manual,
                                          contacto_local=contacto_local)
    if analisis.get("no_apto") and desde in ("ANALIZADA", "LISTO"):
        analisis["no_apto"]["desde"] = desde


def _recalcular_y_persistir_post(post: Dict[str, Any], *, updated_at_esperado: Optional[str] = None) -> bool:
    """Recalcula score, desglose y descarte de un post; persiste cambios y sincroniza su postulación vinculada."""
    analisis = post.get("analisis")
    if not isinstance(analisis, dict):
        return False
    reqs = analisis.get("requisitos")
    if not isinstance(reqs, list):
        return False

    score, desglose = calculate_match_score_with_breakdown(reqs)
    analisis["match_score"] = score
    analisis["desglose_afinidad"] = desglose

    sello_previo = dict((post.get("analisis") or {}).get("no_apto") or {})
    desde_previo = sello_previo.get("desde")

    _recalcular_descarte(analisis, texto_capturado(post), manual=post.get("fuente") == radar_store.FUENTE_MANUAL,
                         contacto_local=_senal_argentina(post))

    estado_actual = post.get("estado")
    tiene_motivos = bool((analisis.get("no_apto") or {}).get("motivos"))
    tiene_aprobado = bool(post.get("aprobado_at"))

    if tiene_motivos:
        if estado_actual in ("ANALIZADA", "LISTO"):
            if not tiene_aprobado:
                analisis["no_apto"]["desde"] = estado_actual
                nuevo_estado = "IGNORADA"
            else:
                nuevo_estado = estado_actual
        elif estado_actual == "IGNORADA":
            if desde_previo in ("ANALIZADA", "LISTO") and "desde" not in analisis["no_apto"]:
                analisis["no_apto"]["desde"] = desde_previo
            nuevo_estado = "IGNORADA"
        else:
            nuevo_estado = estado_actual
    else:
        analisis.pop("no_apto", None)
        if estado_actual == "IGNORADA":
            nuevo_estado = desde_previo if desde_previo in ("ANALIZADA", "LISTO") else "ANALIZADA"
        else:
            nuevo_estado = estado_actual

    ok = radar_store.update_post(
        post["id"],
        updated_at_esperado=updated_at_esperado,
        analisis=analisis,
        estado=nuevo_estado,
    )
    if not ok:
        return False

    postulacion_id = post.get("postulacion_id")
    if postulacion_id:
        postulacion = database.get_postulacion(postulacion_id)
        if postulacion:
            p_reqs = postulacion.get("requisitos") or []
            req_map = {clave_requisito(r.get("nombre")): r for r in reqs if isinstance(r, dict)}
            cambio = False
            for p_r in p_reqs:
                if isinstance(p_r, dict):
                    k = clave_requisito(p_r.get("nombre"))
                    if k in req_map:
                        r_orig = req_map[k]
                        for campo in ("estado", "approved", "aprendido", "eliminatorio", "eliminatorio_usuario", "categoria_descarte"):
                            if campo in r_orig:
                                p_r[campo] = r_orig[campo]
                            else:
                                p_r.pop(campo, None)
                        cambio = True
            if cambio:
                p_score, p_desglose = calculate_match_score_with_breakdown(p_reqs)
                p_analisis = postulacion.get("analisis") if isinstance(postulacion.get("analisis"), dict) else {}
                p_analisis["match_score"] = p_score
                p_analisis["desglose_afinidad"] = p_desglose
                p_analisis["requisitos"] = p_reqs
                database.update_postulacion(
                    postulacion_id,
                    requisitos=p_reqs,
                    match_score=p_score,
                    analisis=p_analisis,
                )
    return True


def _resumen_requisito(r: Dict[str, Any], tiene_no_apto: bool) -> Dict[str, Any]:
    """Lo que la tarjeta necesita de un requisito (desglose de afinidad y sello «no apto»). Sin la
    justificación ni la categoría (pesan la mayor parte de la lista y solo las usa el detalle) y sin las
    marcas en falso: ausente vale lo mismo."""
    item: Dict[str, Any] = {"estado": r.get("estado")}
    if r.get("aprendido"):
        item["aprendido"] = True
    if tiene_no_apto:
        item["nombre"] = r.get("nombre")
    if r.get("eliminatorio"):
        item["eliminatorio"] = True
    for campo in ("logistico", "dura", "tipo"):
        val = r.get(campo)
        if val is not None and val != "" and val is not False:
            item[campo] = val
    return item


class EmailEditIn(BaseModel):
    email_to: Optional[str] = None
    email_subject: Optional[str] = None
    email_body: Optional[str] = None
    updated_at: Optional[str] = None


class ApproveIn(BaseModel):
    confirm_duplicate: bool = False
    # «Enviar igual»: aprobar aunque la auditoría del CV no aprobó (la guarda lo acepta solo para ese motivo)
    forzar_auditoria: bool = False
    # El `updated_at` con que el usuario revisó el post: si cambió desde entonces, no se aprueba
    updated_at: Optional[str] = None


class ApproveAllIn(BaseModel):
    """Lo que el usuario confirmó en "Aprobar todos": ids y el `updated_at` con que los vio."""
    ids: List[EnteroSqlite]
    updated_at: Dict[int, Optional[str]] = Field(default_factory=dict)


class DescarteIn(BaseModel):
    desde: str = ""  # «detalle» (botón del detalle) o «duplicado» (fila de «Aprobar todos»)


class RegenerateIn(BaseModel):
    # CV desde el maestro: «Regenerar» rehace siempre CV y mail; `cv=False` (solo el mail) queda
    # solo para la API
    cv: bool = True
    # Del flujo anterior («completo» o «una_pagina»); el CV desde el maestro es siempre de 1 página
    formato: Optional[str] = None


class BacklogIn(BaseModel):
    activo: bool


class GeneracionIn(BaseModel):
    pausada: bool


class InterruptorIn(BaseModel):
    activo: StrictBool


# ----------------------------------------------------------------- helpers

def _captura_bloqueo_path(radar: Any = None) -> Path:
    d = getattr(radar, "debug_dir", None) or getattr(radar, "_debug_dir", None) or (DATA_DIR / "radar_debug")
    return Path(d) / "bloqueo" / "screenshot.png"


def _busquedas_pausa(radar: Any = None) -> Optional[Dict[str, Any]]:
    p = pausa_busquedas()
    if not p:
        return None
    res = dict(p)
    path = _captura_bloqueo_path(radar)
    res["captura"] = "/api/radar/captura-bloqueo" if path.is_file() else None
    return res


def _pendientes_revision() -> int:
    """Lo de `revision_pendiente` más las listas para enviar cuya auditoría del CV no aprobó."""
    return revision_store.contar_pendientes() + len(radar_store.auditorias_no_aprobadas())


def _items_auditoria() -> List[Dict[str, Any]]:
    """Las listas con la auditoría no aprobada, como ítems de «Por revisar» (se resuelven corrigiendo o enviando)."""
    return [{"tipo": "auditoria", "post_id": post_id, "frase": auditoria_envio.motivos_cortos(reporte)[0]}
            for post_id, reporte in sorted(radar_store.auditorias_no_aprobadas().items(), reverse=True)]


def _status(radar: Any = None) -> Dict[str, Any]:
    gmail = load_gmail_config()
    return {**radar_store.get_radar_state(), "gmail_configurado": gmail is not None,
            # Modo de prueba del mail (LINKEDOUT_MAIL_PRUEBA): solo si está activo, nunca la dirección
            "mail_prueba": bool(getattr(gmail, "mail_prueba", None)),
            "revision_pendientes": _pendientes_revision(),
            "agy_cuota": CUOTA.estado(), "cola": _cola(radar),
            "busquedas_pausa": _busquedas_pausa(radar), "cuentas": _cuentas(radar)}


def _cuentas(radar: Any = None) -> List[Dict[str, Any]]:
    """Cuentas de LinkedIn del Radar para el panel. Sin radar (o uno de prueba), solo la principal."""
    estado = getattr(radar, "estado_cuentas", None)
    try:
        if callable(estado):
            return list(estado())
    except Exception:
        logger.exception("[Radar API] No se pudo leer el estado de las cuentas")
    return [{"cuenta": "principal", "con_sesion": True, "en_uso": True, "pausada_hasta": None},
            {"cuenta": "secundaria", "con_sesion": False, "en_uso": False, "pausada_hasta": None}]


def _cola(radar: Any) -> Dict[str, Any]:
    """Lo que el radar tiene en cola: cuántos CVs faltan, cuál se hace ahora (con su progreso
    persistido) y cuándo terminaría. La etapa en memoria del radar, si la hay, manda sobre la guardada."""
    cola = radar_store.resumen_cola(radar=radar)
    etapas = getattr(radar, "etapas_generacion", None)
    actual = cola["actual"]
    if actual and isinstance(etapas, dict) and etapas.get(actual["post_id"]):
        actual["etapa"] = etapas[actual["post_id"]]
    return cola


def _get_post_or_404(post_id: int) -> Dict[str, Any]:
    post = radar_store.get_post(post_id)
    if post is None:
        raise HTTPException(status_code=404, detail=f"Post {post_id} no encontrado")
    return post


def _triage(post: Dict[str, Any]) -> Dict[str, Any]:
    return post["triage"] if isinstance(post.get("triage"), dict) else {}


def _puesto_empresa_de(post_id: Optional[int], postulacion_id: Optional[int]) -> Tuple[str, str]:
    """Puesto y empresa de la oferta de un ítem de revisión: los del post y, si no hay, los de la postulación."""
    puesto = empresa = ""
    if post_id:
        triage = _triage(radar_store.get_post(post_id) or {})
        puesto, empresa = triage.get("puesto") or "", triage.get("empresa") or ""
    if postulacion_id and not (puesto or empresa):
        postulacion = database.get_postulacion(postulacion_id) or {}
        puesto, empresa = postulacion.get("titulo") or "", postulacion.get("empresa") or ""
    return puesto, empresa


LARGO_MOTIVO_CORTO = 28


def motivo_descarte(post: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """Por qué está descartada (la tarjeta tiene que decirlo): `corto` para la tarjeta y `largo` para
    el detalle. None si no está descartada."""
    if post.get("estado") != "DESCARTADO":
        return None
    t = _triage(post)
    if t.get("motivo_descarte") == "manual":
        cuando = str(t.get("descartado_at") or post.get("updated_at") or "")
        dia = f"{cuando[8:10]}/{cuando[5:7]}" if re.match(r"\d{4}-\d{2}-\d{2}", cuando) else ""
        corto, largo = f"A mano{' · ' + dia if dia else ''}", f"la descartaste a mano{' el ' + dia if dia else ''}"
    elif t.get("motivo_descarte") == "prefiltro":
        que = re.sub(r"^(?:Descartado sin IA:\s*)?(?:ubicaci[oó]n\s+)?", "", str(t.get("alcance_motivo") or "")).strip()
        corto = f"Exterior{': ' + que if que else ''}"
        largo = f"es un puesto en el exterior{' (' + que + ')' if que else ''}"
    elif t.get("motivo") == "republicada":
        corto, largo = "Repetida", f"es la misma oferta que ya llegó (#{t.get('republicacion_de')}), republicada por su autor"
    elif t.get("motivo") == MOTIVO_NO_OFERTA:
        corto, largo = "No parece oferta", "el clasificador no la vio como oferta"
    elif t.get("alcance") == "EXTERIOR":
        pais = str(t.get("pais") or "").strip()
        corto = f"Exterior{': ' + pais if pais else ''}"
        largo = f"es un puesto en el exterior que no acepta candidatos de Argentina{' (' + pais + ')' if pais else ''}"
    else:
        corto, largo = "Sin motivo registrado", "no quedó registrado el motivo"
    if len(corto) > LARGO_MOTIVO_CORTO:
        corto = corto[:LARGO_MOTIVO_CORTO - 1].rstrip() + "…"
    return {"corto": corto, "largo": largo}


def _analisis_lista(analisis: Dict[str, Any]) -> Dict[str, Any]:
    """Lo que la lista toma del análisis de la oferta."""
    return {
        "puesto": analisis.get("puesto") or "",
        "empresa": analisis.get("empresa") or "",
        # Score de afinidad del análisis de la oferta (el mismo de Postulaciones); None sin analizar
        "match_score": analisis.get("match_score"),
        "no_apto": analisis.get("no_apto"),
        "condicion_ubicacion": analisis.get("condicion_ubicacion") or "",
        "analizado": bool(analisis),
        # Solo estado y marcas de cada requisito: alcanza para el desglose de la afinidad en la tarjeta
        "requisitos_estado": [
            _resumen_requisito(r, bool(analisis.get("no_apto")))
            for r in (analisis.get("requisitos") if isinstance(analisis.get("requisitos"), list) else [])
            if isinstance(r, dict)
        ],
        "oferta_cerrada": bool(analisis.get("oferta_cerrada")),
    }


@functools.lru_cache(maxsize=ANALISIS_EN_CACHE)
def _analisis_lista_crudo(crudo: str) -> Dict[str, Any]:
    """`_analisis_lista` desde el JSON guardado. El resultado se comparte entre GETs: solo se serializa."""
    try:
        analisis = json.loads(crudo)
    except ValueError:
        analisis = None
    return _analisis_lista(analisis if isinstance(analisis, dict) else {})


def _analisis_de(post: Dict[str, Any]) -> Dict[str, Any]:
    analisis = post.get("analisis")
    if isinstance(analisis, str):
        return _analisis_lista_crudo(analisis)
    return _analisis_lista(analisis if isinstance(analisis, dict) else {})


def _campos_auditoria(reporte: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """`auditoria_ok` y, si la auditoría del CV no aprobó, sus motivos cortos (la tarjeta dice «Revisar»)."""
    if not auditoria_envio.no_aprobada(reporte):
        return {"auditoria_ok": True}
    return {"auditoria_ok": False, "auditoria_motivos": auditoria_envio.motivos_cortos(reporte)}


def _resumen(post: Dict[str, Any], cola: Optional[Dict[str, Any]] = None,
             auditoria: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Versión liviana para la lista (sin texto completo ni cuerpo del email). `cola`: su lugar en las
    colas dinámicas de CV («Prioritaria, 2.º»), o None si no espera CV. `analisis` puede venir decodificado
    o como el JSON guardado (`list_posts(crudas=("analisis",))`). `auditoria`: el reporte del CV si no aprobó
    (`radar_store.auditorias_no_aprobadas`)."""
    triage = _triage(post)
    analisis = _analisis_de(post)
    texto = post.get("texto") or ""
    return {
        "id": post["id"],
        "urn": post.get("urn"),
        "url": post.get("url"),
        "post_url": post.get("post_url") or "",
        "autor": post.get("autor") or "",
        "cargo_autor": post.get("cargo_autor") or "",
        "fecha_rel": post.get("fecha_rel") or "",
        "query": post.get("query") or "",
        "estado": post["estado"],
        "emails": post.get("emails") or [],
        "email_to": post.get("email_to") or "",
        "email_subject": post.get("email_subject") or "",
        **analisis,
        "puesto": analisis["puesto"] or triage.get("puesto") or "",
        "empresa": analisis["empresa"] or triage.get("empresa") or "",
        "prioridad": int(post.get("prioridad") or 0),
        "motivo": triage.get("motivo") or "",
        "alcance": triage.get("alcance") or "",
        "pais": triage.get("pais") or "",
        "fuente": post.get("fuente") or "busqueda",
        "canal": post.get("canal") or "",
        "destino_canal": post.get("destino_canal") or "",
        "links": post.get("links") or [],
        "whatsapp": post.get("whatsapp") or [],
        "postulacion_id": post.get("postulacion_id"),
        "aprobado_at": post.get("aprobado_at"),
        "sent_at": post.get("sent_at"),
        "message_id": post.get("message_id"),
        "error": post.get("error"),
        "extracto": texto[:LARGO_EXTRACTO],
        "created_at": post.get("created_at"),
        "updated_at": post.get("updated_at"),
        "cola": cola,
        **_campos_auditoria(auditoria),
        # Solo en las descartadas: por qué (la tarjeta lo dice)
        **({"descarte": motivo_descarte(post)} if post.get("estado") == "DESCARTADO" else {}),
    }


def _motivos_no_apto(post: Dict[str, Any]) -> bool:
    no_apto = _analisis_de(post)["no_apto"]
    return bool(isinstance(no_apto, dict) and no_apto.get("motivos"))


# (firma de las candidatas, momento, posiciones) del último cálculo
_LUGARES_COLA: Optional[Tuple[int, float, Dict[int, Dict[str, Any]]]] = None


def _lugares_cola() -> Dict[int, Dict[str, Any]]:
    """Lugar de cada candidata en las colas de CV. Ordenarlas cuesta (lee la captura de cada una): se
    reusa el último cálculo si ninguna fila cambió, hasta VIGENCIA_LUGARES_COLA_S."""
    global _LUGARES_COLA
    from core import colas
    columnas, filas = radar_store.filas_cola()
    firma = hash((columnas, tuple(filas)))
    ahora = time.monotonic()
    previo = _LUGARES_COLA
    if previo and previo[0] == firma and ahora - previo[1] <= VIGENCIA_LUGARES_COLA_S:
        return previo[2]
    lugares = colas.posiciones([radar_store.fila_a_post(columnas, f) for f in filas])
    _LUGARES_COLA = (firma, ahora, lugares)
    return lugares


def _acepta_gzip(request: Request) -> bool:
    """¿El `Accept-Encoding` incluye gzip con calidad mayor que cero?"""
    for parte in (request.headers.get("accept-encoding") or "").split(","):
        nombre, _, params = parte.partition(";")
        if nombre.strip().lower() != "gzip":
            continue
        clave, _, valor = params.replace(" ", "").partition("=")
        try:
            return clave.lower() != "q" or float(valor) > 0
        except ValueError:
            return False
    return False


def _json_lista(request: Request, datos: List[Dict[str, Any]]) -> Response:
    """JSON de la lista, comprimido si el navegador lo acepta."""
    cuerpo = to_json(datos)
    cabeceras = {"Vary": "Accept-Encoding"}
    if len(cuerpo) >= MINIMO_GZIP and _acepta_gzip(request):
        cuerpo = gzip.compress(cuerpo, compresslevel=NIVEL_GZIP)
        cabeceras["Content-Encoding"] = "gzip"
    return Response(content=cuerpo, media_type="application/json", headers=cabeceras)


def _detalle(post_id: int) -> Dict[str, Any]:
    post = _get_post_or_404(post_id)
    pid = post.get("postulacion_id")
    post["postulacion"] = database.get_postulacion(pid) if pid else None
    # Solo si no aprobó (el detalle conserva su forma; radar.js lee la falta del campo como aprobado)
    reporte = (post["postulacion"] or {}).get("reporte_auditoria")
    if post.get("estado") in ("LISTO", "ERROR") and auditoria_envio.no_aprobada(reporte):
        post.update(_campos_auditoria(reporte))
    if post.get("estado") == "DESCARTADO":
        post["descarte"] = motivo_descarte(post)
    return post


def _es_canal_email(post: Dict[str, Any]) -> bool:
    """Un post sin canal (anterior a los canales) se postula por email."""
    return radar_store.es_canal_email(post)


def _aviso_otro_canal(post: Dict[str, Any]) -> str:
    return f"Este post se postula por {post.get('canal')}: usá «Ya me postulé»"


def _motivo_no_aprobable(post: Dict[str, Any]) -> Optional[str]:
    """Por qué un post `LISTO` no se puede aprobar todavía (None si se puede)."""
    if post.get("postulacion_id"):
        postulacion = database.get_postulacion(post["postulacion_id"])
        if not postulacion or postulacion.get("status") != "READY":
            return "el CV todavía no está listo; esperá a que termine y revisalo"
    to = (post.get("email_to") or "").strip()
    if not to:
        return "falta el destinatario"
    if not _EMAIL_RE.match(to):
        return f"destinatario inválido: {to}"
    if not (post.get("email_subject") or "").strip():
        return "falta el asunto"
    if not (post.get("email_body") or "").strip():
        return "falta el cuerpo del email"
    return None


def _destinatarios_pendientes(excluir_id: Optional[int] = None) -> Set[str]:
    """Destinatarios con un envío en curso o aprobado en cola (todavía no figuran como ENVIADO)."""
    pendientes = radar_store.list_posts(estado="ENVIANDO") + [
        p for p in radar_store.list_posts(estado="LISTO") if p.get("aprobado_at")
    ]
    return {
        (p.get("email_to") or "").strip().lower()
        for p in pendientes
        if p["id"] != excluir_id and (p.get("email_to") or "").strip()
    }


def _es_duplicado(post: Dict[str, Any], pendientes: Set[str]) -> bool:
    to = (post.get("email_to") or "").strip()
    return to.lower() in pendientes or radar_store.recipient_sent_within(
        to, DIAS_DUPLICADO, exclude_post_id=post["id"]
    )


def _require_gmail() -> None:
    if load_gmail_config() is None:
        raise HTTPException(status_code=400, detail=GMAIL_NO_CONFIGURADO)


def _ahora() -> str:
    return datetime.now().strftime(_FMT_FECHA)


async def _notificar(radar: Any, post_id: Optional[int] = None) -> None:
    """SSE de cambios hechos desde la API; un fallo del broadcast nunca rompe la request."""
    try:
        if post_id is None:
            await radar.notify_status()
        else:
            await radar.notify_post(post_id)
    except Exception:
        logger.exception("[Radar API] Falló la notificación SSE")


async def _regenerar_post(post_id: int, payload: RegenerateIn, radar: Any, *,
                          solo_no_aprobados: bool = False) -> dict | None:
    """Regenera un post; el lote omite candidatos que cambiaron o perdieron el reclamo."""
    post = radar_store.get_post(post_id) if solo_no_aprobados else _get_post_or_404(post_id)
    if solo_no_aprobados and (
        post is None or post["estado"] != "LISTO"
        or post.get("aprobado_at") is not None or not _triage(post)
    ):
        return None
    if post["estado"] not in ("LISTO", "ERROR"):
        raise HTTPException(status_code=409, detail=f"Solo se regenera un post LISTO o en ERROR (estado={post['estado']})")
    if not _triage(post):
        raise HTTPException(status_code=409, detail="El post no pasó el triage: usá Reintentar")
    cambios: Dict[str, Any] = dict(
        estado="GENERANDO", gen_prioridad=PRIORIDAD_FORZADA, gen_manual=0, aprobado_at=None,
        email_subject=None, email_body=None, intentos=0, error=None,
    )
    pid = post.get("postulacion_id")
    if payload.cv:
        cambios["postulacion_id"] = None
        if payload.formato is not None and payload.formato not in FORMATOS_CV:
            raise HTTPException(status_code=422, detail=f"Formato inválido: {payload.formato}")
    if solo_no_aprobados:
        # Reclamar antes de tocar la postulación: una versión perdida no deja efectos.
        if not radar_store.update_post(post_id, updated_at_esperado=post["updated_at"], **cambios):
            return None
    if payload.cv:
        if pid and database.get_postulacion(pid) and payload.formato:
            database.update_postulacion(pid, formato_cv=payload.formato)
        if pid and database.get_postulacion(pid):
            # El pipeline reutiliza la postulación de la misma URL al salir de READY.
            database.update_postulacion(pid, status="PENDING")
    if not solo_no_aprobados:
        radar_store.update_post(post_id, **cambios)
    await _notificar(radar, post_id)
    await radar.wake_generation()
    return _detalle(post_id)


async def _detener_si_corresponde(radar: Any) -> None:
    """Background de `POST /stop`: no detiene si el usuario volvió a iniciar mientras tanto."""
    try:
        if radar_store.get_radar_state()["desired_running"]:
            logger.info("[Radar API] Stop omitido: el radar se volvió a iniciar")
            return
        await radar.stop()
    except Exception:
        logger.exception("[Radar API] Falló la detención del radar")


async def _aprobar(radar: Any, post_id: int, updated_at: str, forzado_auditoria: bool = False) -> None:
    """Setea `aprobado_at` y encola. `ValueError` del radar → 409 (no quedó encolado). Con `forzado_auditoria`
    (Enviar igual con la auditoría no aprobada) la aprobación lo registra con la misma hora."""
    ahora = _ahora()
    campos = {"aprobado_at": ahora, "forzado_auditoria_at": ahora if forzado_auditoria else None}
    if not radar_store.update_post(post_id, updated_at_esperado=updated_at, **campos):
        raise HTTPException(status_code=409, detail="Cambió desde que lo revisaste: abrilo y aprobalo de nuevo")
    try:
        await radar.enqueue_send(post_id)
    except ValueError as e:
        # El post dejó de estar LISTO entre la validación y el encolado: no queda aprobado
        radar_store.update_post(post_id, aprobado_at=None, forzado_auditoria_at=None)
        raise HTTPException(status_code=409, detail=str(e))
    if forzado_auditoria:
        logger.warning(f"[Radar API] Envío aprobado sin la auditoría del CV (post {post_id}): Enviar igual")
    await _notificar(radar, post_id)


# ----------------------------------------------------------------- router

def _tiene_requisito(post: Dict[str, Any], clave: str) -> bool:
    reqs = (post.get("analisis") or {}).get("requisitos") if isinstance(post.get("analisis"), dict) else None
    return isinstance(reqs, list) and any(isinstance(r, dict) and clave_requisito(r.get("nombre")) == clave for r in reqs)


def _propagar_requisito(post_id: int, nombre: str, eliminatorio: Optional[bool], req: Dict[str, Any]) -> List[int]:
    """Lleva la decisión sobre un requisito a los otros posts editables que lo tienen; devuelve los actualizados."""
    clave_objetivo = clave_requisito(nombre)
    actualizados: List[int] = []
    for dest_summary in radar_store.list_posts(limit=None):
        if not _tiene_requisito(dest_summary, clave_objetivo):
            continue
        dest_id = dest_summary.get("id")
        if not isinstance(dest_id, int) or dest_id == post_id:
            continue
        if dest_summary.get("aprobado_at"):
            continue
        dest_estado = dest_summary.get("estado")
        dest_sello = (dest_summary.get("analisis") or {}).get("no_apto") or {}
        es_elegible = (dest_estado in ("ANALIZADA", "LISTO")) or (
            dest_estado == "IGNORADA" and bool(dest_sello.get("motivos"))
        )
        if not es_elegible:
            continue

        dest = radar_store.get_post(dest_id)
        if not dest or dest.get("aprobado_at"):
            continue
        estado_fresco = dest.get("estado")
        sello_fresco = (dest.get("analisis") or {}).get("no_apto") or {}
        es_elegible_fresco = (estado_fresco in ("ANALIZADA", "LISTO")) or (
            estado_fresco == "IGNORADA" and bool(sello_fresco.get("motivos"))
        )
        if not es_elegible_fresco:
            continue

        dest_analisis = dest.get("analisis")
        if not isinstance(dest_analisis, dict):
            continue
        dest_reqs = dest_analisis.get("requisitos")
        if not isinstance(dest_reqs, list):
            continue

        hubo_match = False
        for r_dest in dest_reqs:
            if isinstance(r_dest, dict) and clave_requisito(r_dest.get("nombre")) == clave_objetivo:
                hubo_match = True
                if eliminatorio is None:
                    r_dest["estado"] = req["estado"]
                    r_dest["approved"] = req["approved"]
                    r_dest["aprendido"] = True
                    if req["estado"] != "BRECHA_REAL":
                        r_dest.pop("dura", None)
                else:
                    r_dest["eliminatorio"] = eliminatorio
                    r_dest["eliminatorio_usuario"] = eliminatorio
                    if not eliminatorio:
                        r_dest.pop("categoria_descarte", None)

        if not hubo_match:
            continue

        version_esperada = dest.get("updated_at")
        if _recalcular_y_persistir_post(dest, updated_at_esperado=version_esperada):
            actualizados.append(dest_id)
    return actualizados


def build_radar_router(get_radar: Callable[[], Any]) -> APIRouter:
    router = APIRouter(prefix="/api/radar", tags=["radar"], dependencies=[Depends(_solo_pc_o_emparejado)])
    # Sin guarda: el celular todavía no emparejado llega acá con la clave del QR y la ruta la valida
    abierto = APIRouter(prefix="/api/radar", tags=["radar"])

    def radar_dep() -> Any:
        return get_radar()

    # ---------------------------------------------------------- ciclo de vida

    @router.post("/start")
    async def radar_start(radar: Any = Depends(radar_dep)):
        radar_store.update_radar_state(desired_running=True)
        await radar.start()
        return _status(get_radar())

    @router.post("/stop")
    async def radar_stop(background_tasks: BackgroundTasks, radar: Any = Depends(radar_dep)):
        # Responde ya; el estado DETENIDO llega por SSE cuando termina el paso en curso
        radar_store.update_radar_state(desired_running=False)
        background_tasks.add_task(_detener_si_corresponde, radar)
        return _status(get_radar())

    @router.post("/reintentar")
    async def radar_reintentar(radar: Any = Depends(radar_dep)):
        await radar.stop()
        radar_store.update_radar_state(desired_running=True)
        await radar.start()
        return _status(get_radar())

    @router.get("/status")
    def radar_status():
        return _status(get_radar())

    @router.post("/levantar-bloqueo")
    def radar_levantar_bloqueo():
        n = radar_store.levantar_bloqueo()
        return {**_status(get_radar()), "levantadas": n}

    @router.get("/captura-bloqueo")
    def radar_captura_bloqueo(radar: Any = Depends(radar_dep)):
        path = _captura_bloqueo_path(radar)
        if not path.is_file():
            raise HTTPException(status_code=404, detail="No hay captura de bloqueo")
        return FileResponse(path, media_type="image/png")

    @router.get("/lecturas")
    def radar_lecturas(limite: int = Query(50, ge=0, le=MAX_ENTERO_SQLITE), query: Optional[str] = None):
        return radar_store.listar_lecturas(limite=limite, query=query)

    @router.get("/procesos")
    def radar_procesos(radar: Any = Depends(radar_dep)):
        from core.procesos import armar_procesos
        from core.prioridades import memo_captura
        with memo_captura():
            return armar_procesos(radar)

    @router.get("/limpieza")
    async def radar_limpieza_candidatas():
        """Cuántas ofertas viejas borraría la limpieza con la afinidad mínima vigente."""
        umbral = radar_store.get_radar_state()["umbral_match"]
        return {**await asyncio.to_thread(radar_store.candidatas_limpieza, umbral),
                "umbral": umbral, "dias": radar_store.DIAS_LIMPIEZA}

    @router.post("/limpieza")
    async def radar_limpieza():
        """Borra las ofertas viejas con afinidad baja (backup antes) y compacta la base."""
        umbral = radar_store.get_radar_state()["umbral_match"]
        resultado = await asyncio.to_thread(radar_store.limpiar_viejas, umbral)
        logger.info("[Radar] Limpieza de ofertas viejas: %s borradas, backup %s",
                    resultado["borradas"], resultado["backup"])
        return resultado

    @router.post("/backlog")
    async def radar_backlog(payload: BacklogIn, radar: Any = Depends(radar_dep)):
        radar_store.update_radar_state(backlog_activo=payload.activo)
        if payload.activo:
            await radar.wake_generation()
        await _notificar(radar)
        return _status(get_radar())

    @router.post("/generacion")
    async def radar_generacion(payload: GeneracionIn, radar: Any = Depends(radar_dep)):
        radar_store.update_radar_state(generacion_pausada=payload.pausada)
        if not payload.pausada:
            await radar.wake_generation()
        await _notificar(radar)
        return _status(get_radar())

    @router.post("/analisis")
    async def radar_analisis(payload: GeneracionIn, radar: Any = Depends(radar_dep)):
        """Pausa o reanuda la clasificación y el análisis; la cosecha del Radar sigue."""
        radar_store.update_radar_state(analisis_pausado=payload.pausada)
        if not payload.pausada:
            await radar.wake_processing()
        await _notificar(radar)
        return _status(get_radar())

    @router.post("/feed")
    async def radar_feed(payload: InterruptorIn, radar: Any = Depends(radar_dep)):
        """Enciende o apaga la lectura del feed; vale desde la próxima lectura del ciclo."""
        radar_store.update_radar_state(feed_activo=payload.activo)
        await _notificar(radar)
        return _status(get_radar())

    @router.post("/busquedas")
    async def radar_busquedas(payload: InterruptorIn, radar: Any = Depends(radar_dep)):
        """Enciende o apaga las búsquedas en LinkedIn; apagadas, el ciclo en curso no hace ninguna más."""
        radar_store.update_radar_state(busquedas_activas=payload.activo)
        await _notificar(radar)
        return _status(get_radar())

    @router.patch("/config")
    async def radar_config(payload: ConfigIn, radar: Any = Depends(radar_dep)):
        from .bridge import configurar_modo_serie

        if payload.umbral_match is not None:
            radar_store.update_radar_state(umbral_match=payload.umbral_match)
        if payload.modo_serie is not None:
            configurar_modo_serie(payload.modo_serie)
        await _notificar(radar)
        # Conservar el flujo legacy sólo cuando se modifica su configuración.
        if payload.umbral_match is not None:
            await radar.generar_automaticos()
        return _status(get_radar())

    # ---------------------------------------------------------- revisión pendiente

    @router.get("/revision")
    def revision_listar(todos: bool = False):
        """Lo que se completó o corrigió sin datos suficientes, para revisarlo."""
        items = _items_auditoria() + revision_store.listar(pendientes=not todos)
        for it in items:
            it["puesto"], it["empresa"] = _puesto_empresa_de(it.get("post_id"), it.get("postulacion_id"))
        return {"items": items, "pendientes": _pendientes_revision()}

    @router.post("/revision/{item_id}/revisado")
    def revision_marcar(item_id: IdSqlite):
        if not revision_store.marcar_revisado(item_id):
            raise HTTPException(status_code=404, detail="Ítem de revisión inexistente.")
        return {"pendientes": _pendientes_revision()}

    # ---------------------------------------------------------- acceso desde el celular

    def _solo_pc(request: Request) -> None:
        motivo = _motivo_no_local(request)
        if motivo:
            raise HTTPException(status_code=403, detail=f"Solo desde la PC ({motivo}).")

    def _acceso_celular(request: Request) -> Dict[str, Any]:
        ip = acceso_remoto.ip_lan()
        if not ip:
            raise HTTPException(status_code=503, detail="La PC no está conectada a una red local.")
        url = f"http://{ip}:{request.url.port or 80}/api/radar/emparejar?clave={acceso_remoto.clave()}"
        return {"url": url, "qr_svg": acceso_remoto.qr_svg(url)}

    @router.get("/acceso-celular")
    def radar_acceso_celular(request: Request):
        _solo_pc(request)
        return _acceso_celular(request)

    @router.post("/acceso-celular/regenerar")
    def radar_acceso_celular_regenerar(request: Request):
        _solo_pc(request)
        acceso_remoto.regenerar()
        return _acceso_celular(request)

    @abierto.get("/emparejar")
    def radar_emparejar(request: Request, clave: str = ""):
        if not acceso_remoto.es_valida(clave):
            return HTMLResponse(
                "<meta name='viewport' content='width=device-width'><p style='font-family:sans-serif'>"
                "Link de emparejamiento inválido o vencido. Escaneá de nuevo el QR de la PC.</p>",
                status_code=403)
        https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie(acceso_remoto.COOKIE, clave, max_age=acceso_remoto.DURACION_COOKIE,
                        httponly=True, samesite="strict", secure=https)
        return resp

    @router.post("/show-browser")
    async def radar_show_browser(radar: Any = Depends(radar_dep)):
        try:
            await radar.show_browser()
        except Exception as e:
            logger.exception("[Radar API] No se pudo mostrar el navegador")
            raise HTTPException(status_code=503, detail=f"No se pudo abrir el navegador: {e}")
        return _status(get_radar())

    @router.post("/resume-sends")
    async def radar_resume_sends(radar: Any = Depends(radar_dep)):
        await radar.resume_sends()
        return _status(get_radar())

    # ---------------------------------------------------------- posts

    @router.get("/posts")
    def radar_list_posts(
        request: Request,
        estado: Optional[str] = None,
        alcance: Optional[str] = None,
        fuente: Optional[str] = None,
        limit: Optional[int] = Query(default=None, ge=1, le=1000),
        limite_descartados: Optional[int] = Query(default=None, ge=1, le=1000),
    ) -> Response:
        """`limite_descartados=N`: todos los posts que no están DESCARTADO ni IGNORADA, sin tope, más los
        N más recientes de cada uno de esos dos (así ningún enviado, listo, analizado, pendiente o error
        queda afuera)."""
        if estado is not None:
            estado = estado.strip().upper()
            if estado not in ESTADOS_POST:
                raise HTTPException(status_code=422, detail=f"Estado inválido. Válidos: {list(ESTADOS_POST)}")
        if limite_descartados is not None and (estado is not None or limit is not None):
            raise HTTPException(status_code=422, detail="limite_descartados no se combina con estado ni limit")
        if fuente is not None:
            fuente = fuente.strip().lower()
            validas = [*radar_store.FUENTES_VALIDAS, FUENTE_PORTALES]
            if fuente not in validas:
                raise HTTPException(status_code=422, detail=f"Fuente inválida. Válidas: {validas}")
            if fuente == FUENTE_PORTALES:
                fuente = radar_store.FUENTES_PORTALES
        # Sin fuente, la vista LinkedIn: ni las manuales ni las de portales
        excluir_fuente = radar_store.FUENTES_NO_LINKEDIN if fuente is None else None
        lugares = _lugares_cola()
        auditorias = radar_store.auditorias_no_aprobadas()
        # `analisis` llega como JSON: `_resumen` lo resume con caché (decodificarlo era la mitad del GET)
        crudas = ("analisis",)
        if limite_descartados is not None:
            archivados = ("DESCARTADO", "IGNORADA")
            posts = radar_store.list_posts(fuente=fuente, excluir_fuente=excluir_fuente, excluir_estados=archivados,
                                           crudas=crudas)
            posts += radar_store.list_posts(
                estado="DESCARTADO", limit=limite_descartados, fuente=fuente, excluir_fuente=excluir_fuente,
                crudas=crudas)
            # Un descartado con CV generado es una postulación: siempre visible, fuera del tope
            vistos = {p["id"] for p in posts}
            posts += [p for p in radar_store.list_posts(
                estado="DESCARTADO", fuente=fuente, excluir_fuente=excluir_fuente, con_postulacion=True,
                crudas=crudas,
            ) if p["id"] not in vistos]
            antiguas = 0
            for post in radar_store.list_posts(estado="IGNORADA", fuente=fuente, excluir_fuente=excluir_fuente,
                                               crudas=crudas):
                if _motivos_no_apto(post):
                    posts.append(post)
                elif antiguas < limite_descartados:
                    posts.append(post)
                    antiguas += 1
            posts.sort(key=lambda p: p["id"], reverse=True)
            if alcance is not None:
                alcance = alcance.strip().upper()
                if alcance not in ALCANCES:
                    raise HTTPException(status_code=422, detail=f"Alcance inválido. Válidos: {list(ALCANCES)}")
                posts = [p for p in posts if _triage(p).get("alcance") == alcance]
            return _json_lista(request, [_resumen(p, lugares.get(p["id"]), auditorias.get(p["id"])) for p in posts])
        if alcance is None:
            return _json_lista(request, [_resumen(p, lugares.get(p["id"]), auditorias.get(p["id"])) for p in radar_store.list_posts(
                estado=estado, limit=limit, fuente=fuente, excluir_fuente=excluir_fuente, crudas=crudas,
            )])
        alcance = alcance.strip().upper()
        if alcance not in ALCANCES:
            raise HTTPException(status_code=422, detail=f"Alcance inválido. Válidos: {list(ALCANCES)}")
        # El alcance vive dentro del JSON de triage: se filtra en Python (posts sin triage no entran)
        posts = [
            p for p in radar_store.list_posts(
                estado=estado, fuente=fuente, excluir_fuente=excluir_fuente, crudas=crudas,
            ) if _triage(p).get("alcance") == alcance
        ]
        return _json_lista(request, [_resumen(p, lugares.get(p["id"]), auditorias.get(p["id"])) for p in posts[:limit]])

    @router.get("/posts/{post_id}")
    def radar_get_post(post_id: IdSqlite):
        return _detalle(post_id)

    @router.patch("/posts/{post_id}")
    async def radar_edit_post(post_id: IdSqlite, payload: EmailEditIn, radar: Any = Depends(radar_dep)):
        post = _get_post_or_404(post_id)
        if payload.updated_at is not None and payload.updated_at != post.get("updated_at"):
            raise HTTPException(status_code=409, detail="Cambió desde que lo revisaste: abrilo y revisá los cambios")
        if post["estado"] != "LISTO" or post.get("aprobado_at"):
            raise HTTPException(status_code=409, detail="Solo se edita un post LISTO que todavía no fue aprobado")
        cambios = {k: v.strip() for k, v in payload.model_dump(exclude_unset=True, exclude={"updated_at"}).items() if v is not None}
        if not _es_canal_email(post):
            # Canal sin email: el destino lo fija el triage (el asunto puede quedar vacío)
            if "email_to" in cambios and cambios["email_to"] != (post.get("email_to") or "").strip():
                raise HTTPException(
                    status_code=409,
                    detail=f"El destino de un post por {post.get('canal')} no se edita",
                )
            cambios.pop("email_to", None)
        elif cambios.get("email_to") and not _EMAIL_RE.match(cambios["email_to"]):
            raise HTTPException(status_code=422, detail=f"Destinatario inválido: {cambios['email_to']}")
        if cambios:
            if not radar_store.update_post(post_id, updated_at_esperado=payload.updated_at, **cambios):
                raise HTTPException(status_code=409, detail="Cambió desde que lo revisaste: abrilo y revisá los cambios")
        # La notificación cede el control. La respuesta corresponde al guardado propio,
        # sin incorporar silenciosamente ediciones que lleguen durante ese await.
        detalle = _detalle(post_id)
        if cambios:
            await _notificar(radar, post_id)
        return detalle

    @router.post("/posts/{post_id}/approve")
    async def radar_approve(post_id: IdSqlite, payload: Optional[ApproveIn] = None, radar: Any = Depends(radar_dep)):
        payload = payload or ApproveIn()
        post = _get_post_or_404(post_id)
        if not payload.updated_at:
            raise HTTPException(status_code=400, detail="Falta la versión revisada: abrilo y aprobalo de nuevo")
        if post["estado"] != "LISTO":
            raise HTTPException(status_code=409, detail=f"Solo se aprueba un post LISTO (estado={post['estado']})")
        if str(post.get("updated_at") or "") != payload.updated_at:
            raise HTTPException(status_code=409, detail="Cambió desde que lo revisaste: abrilo y aprobalo de nuevo")
        if not _es_canal_email(post):
            raise HTTPException(status_code=409, detail=_aviso_otro_canal(post))
        _require_gmail()
        motivo = _motivo_no_aprobable(post)
        if motivo:
            raise HTTPException(status_code=409, detail=f"No se puede aprobar: {motivo}")
        if post.get("aprobado_at"):
            # Ya aprobado: solo se asegura que esté en cola (enqueue_send es idempotente)
            try:
                await radar.enqueue_send(post_id)
            except ValueError as e:
                raise HTTPException(status_code=409, detail=str(e))
            return _detalle(post_id)
        if not payload.confirm_duplicate and _es_duplicado(post, _destinatarios_pendientes(post_id)):
            raise HTTPException(status_code=409, detail={"duplicate": True, "email": post["email_to"].strip()})
        postulacion = database.get_postulacion(post["postulacion_id"]) if post.get("postulacion_id") else None
        forzado = payload.forzar_auditoria and auditoria_envio.no_aprobada((postulacion or {}).get("reporte_auditoria"))
        await _aprobar(radar, post_id, payload.updated_at, forzado_auditoria=forzado)
        return _detalle(post_id)

    @router.post("/approve-all")
    async def radar_approve_all(payload: ApproveAllIn, radar: Any = Depends(radar_dep)):
        """Aprueba SOLO los posts que el usuario contó al confirmar, y solo si no cambiaron desde
        entonces (mismo `updated_at`); el resto va a `omitidos` con el motivo."""
        _require_gmail()
        encolados: List[int] = []
        omitidos: List[Dict[str, Any]] = []
        pendientes = _destinatarios_pendientes()
        for post_id in sorted(set(payload.ids)):
            post = radar_store.get_post(post_id)
            if post is None:
                omitidos.append({"id": post_id, "motivo": "ya no existe"})
                continue
            if post["estado"] != "LISTO" or post.get("aprobado_at"):
                estado = "ya aprobado" if post["estado"] == "LISTO" else post["estado"]
                omitidos.append({"id": post_id, "motivo": f"cambió: ya no está listo para aprobar ({estado})"})
                continue
            if not _es_canal_email(post):
                omitidos.append({"id": post_id, "motivo": _aviso_otro_canal(post)})
                continue
            visto = payload.updated_at.get(post_id)
            if not visto or str(post.get("updated_at") or "") != visto:
                omitidos.append({"id": post_id, "motivo": "cambió desde que lo revisaste: abrilo y aprobalo de nuevo"})
                continue
            no_apto = (post.get("analisis") or {}).get("no_apto") or {}
            if no_apto.get("motivos"):
                omitidos.append({"id": post_id, "motivo": f"No apto: {no_apto['motivos'][0]['texto']}",
                                 "tipo": "no_apto"})
                continue
            motivo = _motivo_no_aprobable(post)
            if motivo is None and _es_duplicado(post, pendientes):
                email = post["email_to"].strip()
                # `previo`: la oferta que ya recibió (o va a recibir) ese mail, para que la UI deje elegir
                omitidos.append({
                    "id": post_id,
                    "motivo": f"duplicado: ya se envió o está por enviarse a {email}",
                    "tipo": "duplicado",
                    "email": email,
                    "previo": radar_store.envio_previo_a(email, DIAS_DUPLICADO, exclude_post_id=post_id),
                })
                continue
            if motivo:
                omitidos.append({"id": post_id, "motivo": motivo})
                continue
            try:
                await _aprobar(radar, post_id, visto)
            except HTTPException as e:
                omitidos.append({"id": post_id, "motivo": str(e.detail)})
                continue
            pendientes.add(post["email_to"].strip().lower())
            encolados.append(post_id)
        return {"encolados": encolados, "omitidos": omitidos}

    @router.post("/posts/{post_id}/marcar-postulado")
    async def radar_marcar_postulado(post_id: IdSqlite, radar: Any = Depends(radar_dep)):
        """Registra una postulación manual (pasa a ENVIADO sin enviar nada) y corta la generación en curso."""
        post = _get_post_or_404(post_id)
        if post["estado"] not in radar_store.ESTADOS_MARCABLES:
            raise HTTPException(status_code=409, detail=f"Esta oferta no se puede marcar como postulada (estado={post['estado']})")
        canal = str(post.get("canal") or "email").strip().lower()
        # Condicional en la DB: solo cuenta si esta request hizo la transición. `postulados` es aparte
        # de `enviados` para no gastar el tope diario de Gmail
        if not radar_store.mark_applied(post_id, f"manual:{canal}", _ahora()):
            raise HTTPException(status_code=409, detail="El post cambió: ya no se puede marcar como postulado")
        if post["estado"] == "GENERANDO":
            # La generación ya no pisa el estado: solo se libera el worker
            await radar.cancelar_generacion(post_id)
        radar_store.bump_counter("postulados")
        await _notificar(radar, post_id)
        await _notificar(radar)
        return _detalle(post_id)

    @router.post("/posts/{post_id}/requisito")
    async def radar_ciclar_requisito(post_id: IdSqlite, payload: RequisitoIn, radar: Any = Depends(radar_dep)):
        """Un clic en un requisito del análisis lo pasa a su estado siguiente, recalcula la afinidad y
        lo guarda como aprendizaje: en las próximas ofertas ese requisito ya llega con ese estado. Si
        el post ya tiene postulación, también se actualiza ahí (el próximo CV lo tiene en cuenta)."""
        post = _get_post_or_404(post_id)
        sello = (post.get("analisis") or {}).get("no_apto") or {}
        if post["estado"] in ("ANALIZANDO", "GENERANDO", "ENVIANDO", "ENVIADO", "DESCARTADO") or (
            post["estado"] == "IGNORADA" and not sello.get("motivos")
        ):
            raise HTTPException(status_code=409, detail="Esperá a que termine el análisis o el CV")
        analisis = post.get("analisis") if isinstance(post.get("analisis"), dict) else {}
        if payload.eliminatorio is None:
            req = _ciclar_requisito(analisis.get("requisitos"), payload.nombre)
        else:
            req = next((r for r in analisis.get("requisitos", []) if isinstance(r, dict)
                        and clave_requisito(r.get("nombre")) == clave_requisito(payload.nombre)), None)
            if req is not None:
                req["eliminatorio"] = payload.eliminatorio
                req["eliminatorio_usuario"] = payload.eliminatorio
                if not payload.eliminatorio:
                    req.pop("categoria_descarte", None)
        if req is None:
            raise HTTPException(status_code=404, detail="Ese requisito no está en el análisis del post")
        if str(req.get("estado") or "").upper() not in ("MATCH_DIRECTO", "TRANSFERIBLE", "BRECHA_REAL"):
            # Sin estado no hay qué aprender: el análisis quedó incompleto
            raise HTTPException(status_code=409, detail="Ese requisito no tiene estado: volvé a analizar el post")
        registrar_aprendizaje(req["nombre"], req["estado"], origen="radar", eliminatorio=payload.eliminatorio)
        _recalcular_y_persistir_post(post)

        # Propagación protegida a otros posts editables con la misma _clave. Corre en un hilo (no frena el portal) y
        # solo abre los posts cuyo resumen ya trae ese requisito: antes abría todos los editables (1011 en el dev,
        # de 6 a 10 s por clic con el Radar andando: «hice clic en Me descarta y no pasó nada»)
        for dest_id in await asyncio.to_thread(_propagar_requisito, post_id, payload.nombre, payload.eliminatorio, req):
            await _notificar(radar, dest_id)

        await _notificar(radar, post_id)
        post_actualizado = radar_store.get_post(post_id)
        if post_actualizado and not (post_actualizado.get("analisis") or {}).get("no_apto"):
            await radar.generar_automaticos([post_id])
        return _detalle(post_id)

    @router.post("/posts/{post_id}/discard")
    async def radar_discard(post_id: IdSqlite, request: Request, body: Optional[DescarteIn] = None,
                            radar: Any = Depends(radar_dep)):
        post = _get_post_or_404(post_id)
        if (request.headers.get(CABECERA_ORIGEN) or "").strip().lower() != ORIGEN_PORTAL:
            from core import tickets
            tickets.reportar_error(
                "api", "descarte_sin_origen",
                f"Se rechazó descartar la oferta {post_id} ({post['estado']}): el pedido no vino del portal",
                peticion={"metodo": "POST", "ruta": f"/api/radar/posts/{post_id}/discard",
                          "agente": request.headers.get("user-agent") or ""},
                datos={"post_id": post_id, "estado": post["estado"]})
            raise HTTPException(status_code=403, detail="Las ofertas se descartan solo desde el portal")
        if post["estado"] in ("ENVIANDO", "ENVIADO"):
            raise HTTPException(status_code=409, detail=f"No se puede descartar un post {post['estado']}")
        desde = body.desde if body and body.desde in DESCARTE_DESDE else "detalle"
        triage = {**_triage(post), "motivo_descarte": "manual", "descartado_desde": desde,
                  "descartado_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        if post["estado"] != "DESCARTADO":
            # Para poder deshacer (`/restaurar`)
            triage["estado_previo"] = post["estado"]
        radar_store.update_post(post_id, estado="DESCARTADO", aprobado_at=None, triage=triage)
        if post["estado"] == "GENERANDO":
            # Si su CV se está generando ahora, se corta (la cola sigue con el próximo); en cola no hace nada
            await radar.cancelar_generacion(post_id)
        await _notificar(radar, post_id)
        return _detalle(post_id)

    @router.post("/posts/{post_id}/restaurar")
    async def radar_restaurar(post_id: IdSqlite, radar: Any = Depends(radar_dep)):
        """Deshace un descarte manual: devuelve el post al estado en que estaba."""
        post = _get_post_or_404(post_id)
        triage = _triage(post)
        previo = triage.get("estado_previo")
        if post["estado"] != "DESCARTADO" or triage.get("motivo_descarte") != "manual" or not previo:
            raise HTTPException(status_code=409, detail="Este post no se puede restaurar: no fue descartado a mano")
        # ANALIZANDO era un paso en curso: vuelve a la cola de ofertas para analizarse de nuevo
        destino = "OFERTA" if previo == "ANALIZANDO" else previo
        triage = {k: v for k, v in triage.items()
                  if k not in ("motivo_descarte", "estado_previo", "descartado_desde", "descartado_at")}
        radar_store.update_post(post_id, estado=destino, aprobado_at=None, triage=triage)
        await _notificar(radar, post_id)
        if destino == "GENERANDO":
            await radar.wake_generation()
        elif destino in ("NUEVO", "OFERTA"):
            # El worker de procesamiento duerme sin timeout: hay que despertarlo
            await radar.wake_processing()
        return _detalle(post_id)

    @router.post("/posts/{post_id}/analizar")
    async def radar_analizar(post_id: IdSqlite, radar: Any = Depends(radar_dep)):
        """Analiza el post ya (primero en la cola), aunque haya fallado o el radar esté detenido."""
        _get_post_or_404(post_id)
        try:
            await radar.prioritize(post_id)
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e))
        return _detalle(post_id)

    @router.post("/posts/{post_id}/generar-cv")
    async def radar_generar_cv(post_id: IdSqlite, radar: Any = Depends(radar_dep)):
        """Genera el CV cuando el usuario lo decide (circuito de Postulaciones) y el mensaje del canal."""
        _get_post_or_404(post_id)
        try:
            await radar.generate_cv(post_id)
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e))
        return _detalle(post_id)

    @router.post("/posts/{post_id}/force")
    async def radar_force(post_id: IdSqlite, radar: Any = Depends(radar_dep)):
        """Postular igual quita el sello local, sin aprobar contenido ni enviar."""
        post = _get_post_or_404(post_id)
        analisis = post.get("analisis") or {}
        if not (analisis.get("no_apto") or {}).get("motivos"):
            if post["estado"] == "IGNORADA":
                raise HTTPException(status_code=409, detail="Esta oferta no se puede forzar en su estado actual")
            return await radar_generar_cv(post_id, radar)
        if post["estado"] not in ("ANALIZADA", "LISTO", "IGNORADA") or post.get("aprobado_at"):
            raise HTTPException(status_code=409, detail="Esta oferta no se puede forzar en su estado actual")
        desde = (analisis.get("no_apto") or {}).get("desde")
        estado_destino = post["estado"]
        if estado_destino == "IGNORADA":
            estado_destino = desde if desde in ("ANALIZADA", "LISTO") else "ANALIZADA"
        if estado_destino != "LISTO":
            try:
                await radar.generate_cv(post_id)
            except ValueError as e:
                raise HTTPException(status_code=409, detail=str(e))
        # generate_cv valida primero: un rechazo no deja una limpieza parcial.
        actual = _get_post_or_404(post_id)
        analisis = dict(actual.get("analisis") or {})
        analisis.pop("no_apto", None)
        if analisis.get("condicion_ubicacion"):
            # El usuario decidió postular igual: un recálculo no vuelve a archivarla por idioma
            analisis["ubicacion_aceptada"] = True
        radar_store.update_post(post_id, analisis=analisis, gen_prioridad=PRIORIDAD_FORZADA,
                                estado="LISTO" if estado_destino == "LISTO" else actual["estado"])
        await _notificar(radar, post_id)
        return _detalle(post_id)

    @router.post("/agy/reintentar")
    async def radar_agy_reintentar():
        """Reintentar ahora (tras cambiar de cuenta de AGY): devuelve el estado de la cuota."""
        return await sondear_cuota()

    @router.post("/reprocesar")
    async def radar_reprocesar(radar: Any = Depends(radar_dep)):
        """Reprocesa todo lo obtenido con el flujo actual (ver `RadarService.reprocesar`)."""
        hechos = await radar.reprocesar()
        return {k: len(v) for k, v in hechos.items()}

    @router.post("/rescate")
    async def radar_rescate(radar: Any = Depends(radar_dep)):
        """Barrido inmediato del rescate de «Captura incompleta» (tope de 3 posts)."""
        return await radar.rescatar_capturas()

    @router.post("/posts/{post_id}/retry")
    async def radar_retry(post_id: IdSqlite, radar: Any = Depends(radar_dep)):
        post = _get_post_or_404(post_id)
        if post["estado"] != "ERROR":
            raise HTTPException(status_code=409, detail=f"Solo se reintenta un post en ERROR (estado={post['estado']})")
        try:
            await radar.retry(post_id)
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e))
        return _detalle(post_id)

    @router.post("/posts/{post_id}/regenerate")
    async def radar_regenerate(post_id: IdSqlite, payload: Optional[RegenerateIn] = None, radar: Any = Depends(radar_dep)):
        return await _regenerar_post(post_id, payload or RegenerateIn(), radar)

    @router.post("/regenerar-listos")
    async def radar_regenerar_listos(radar: Any = Depends(radar_dep)):
        ids = []
        for post in radar_store.list_posts(estado="LISTO"):
            if post.get("aprobado_at") is not None or not _triage(post):
                continue
            resultado = await _regenerar_post(
                post["id"], RegenerateIn(cv=True), radar, solo_no_aprobados=True,
            )
            if resultado is not None:
                ids.append(post["id"])
        return {"regenerados": len(ids), "ids": ids}

    contenedor = APIRouter()
    contenedor.include_router(router)
    contenedor.include_router(abierto)
    return contenedor
