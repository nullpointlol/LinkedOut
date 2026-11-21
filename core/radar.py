"""RadarLoop: orquestador del Radar LinkedIn (captura, clasificación, análisis, CV y envío).

Captura (tarea `_harvest_loop`): primero el feed de inicio ("Mi feed", `query="feed"`) y luego las
búsquedas ("Feed general"). Los posts se guardan en `NUEVO` sin clasificar. Las búsquedas son 100 % del usuario
(`<DATA_DIR>/candidato/busquedas.json`, ver `core/radar_consultas.py`) y se releen en cada vuelta. Las reglas de
lectura también son del perfil (`<DATA_DIR>/candidato/reglas_radar.json`, ver `core/reglas_radar.py`): el código no
tiene ningún valor de presupuesto, pausas, largo de lectura, pausa por bloqueo ni topes, y sin reglas válidas no lee
nada. Cada vuelta:
  * Feed (si está prendido): siempre primero, una lectura con el largo del feed del perfil, sin tope diario
    (contador `leidos_feed`).
  * Búsquedas (si están prendidas): por rondas, en el orden del archivo y sin distinguir idioma. Cada vuelta sigue
    donde cortó la anterior (`radar_state.ronda_leidas`, sobrevive a un reinicio) hasta agotar el presupuesto por
    hora y por día de la cuenta y, completa la ronda, vuelve a la primera. Todas con el mismo largo de lectura y
    guardando todo post con texto (el triage borra lo que no es oferta). Sin tope de posts: lo
    limitan las búsquedas por día y el largo de cada lectura. Contador informativo: `leidos_busquedas`.
  * Una búsqueda con cero resultados es un error: captura + HTML en `radar_debug/sin_resultados/` y
    un ticket (`core/tickets.py`).
Lo claramente del exterior (teléfono, moneda o ciudad de otro país, sin ninguna señal argentina ni de
aceptar LATAM) se descarta sin IA. Las pausas entre lecturas y entre vueltas son las del perfil, las 24 h.

Procesamiento (tarea `_processing_worker`, en paralelo con la captura), un trabajo por vez y en
este orden: lo que el usuario priorizó (botón Analizar), Mi feed, lo cargado a mano y las búsquedas en el
orden del perfil, sin distinguir idioma.
  * Clasificación por lotes de hasta 15 `NUEVO` en una sola llamada al LLM: lo que no es una
    oferta laboral se BORRA (se ignora, no ocupa lugar); una oferta del exterior que no acepta
    candidatos de Argentina → `DESCARTADO`; el resto → `OFERTA` (ubicación desconocida incluida).
    Si un lote falla se parte en mitades; un post que falla solo suma `intentos` (al 3º, `ERROR`).
  * Análisis de cada `OFERTA` (`ANALIZANDO` → `ANALIZADA`): el mismo del Estratega de
    Postulaciones (requisitos, brechas, match), con el modelo y el razonamiento elegidos.
Con el radar detenido solo se procesa lo priorizado.

CV: `generate_cv()` (botón Generar CV) → `GENERANDO`. También solo, cuando una `ANALIZADA` tiene
afinidad > 70, captura suficiente y la oferta sigue abierta (`generar_automaticos()`: al terminar cada
análisis, al iniciar el radar y al reclasificar un requisito). Lo que sale
nunca sale solo: el envío sigue pidiendo aprobación.

Todas las esperas pasan por el `sleep` inyectable y `stop()` las interrumpe al instante.
El loop nunca muere por una excepción inesperada: registra `ERROR` y sigue en el próximo ciclo.

Workers:
  * Generación (`_generation_worker`): reserva hasta `generation_workers` posts distintos:
    pedidos del usuario primero, luego automáticos y backlog por contacto, rol, afinidad e id.
    El backlog toma analizadas abiertas con captura suficiente y reintenta fallos de generación
    con menos de dos intentos:
    `pipeline_fn` (CV, se saltea si el post ya tiene `postulacion_id`) + `compose_fn` → `LISTO`.
    Corre mientras el radar está iniciado (espera `wake_generation()` cuando no hay trabajo) o,
    con el radar detenido, hasta vaciar los `GENERANDO` y el backlog activo. `stop()` lo cancela.
  * Envío (`_send_worker`): cola de ids aprobados. Antes de cada envío relee el post (debe seguir
    `LISTO` con `aprobado_at`), revisa `envios_pausados` y el tope diario; marca `ENVIANDO`, llama
    `send_fn` y marca `ENVIADO`. Separa los envíos 30–90 s. Corre mientras el radar está iniciado
    o haya envíos en cola: aprobar con el radar detenido igual envía. `stop()` NO lo cancela (deja
    salir lo aprobado); `stop_for_shutdown()` lo corta solo fuera de `send_fn` (si está enviando,
    espera a que ese envío termine y no toma el siguiente).
"""
import asyncio
import logging
import math
import random
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple

from core import colas, descartes, progreso, radar_store
from core.bridge import CUOTA, es_saturacion_agy
from core.image_reader import leer_imagenes, necesita_lectura
from core.linkedin_parser import (
    _es_imagen_feedshare_valida, extract_emails, extract_links, extract_whatsapp,
)
from core.mailer import MailerAmbiguousError, MailerAuthError, MailerTemporaryError
from core.radar_consultas import consultas_activas, nombre_de_consulta
from core.reglas_radar import Reglas, ReglasInvalidas, leer_reglas
from core.scraper import OfertaInexistente
from core import portales, portales_store
from core.radar_consultas import busquedas_de_portal
from core.reglas_portales import NOMBRES as NOMBRES_PORTALES, ReglaPortal, leer_reglas_portales
from core.prioridades import (
    aviso_util, captura_suficiente, carril_agy, clave_analisis, elegible_automatico, grupo_rol, prioridad_rol,
    puesto_util, tiene_email, texto_capturado,
)
from core.triage import ALCANCE_DESCONOCIDO, ALCANCE_EXTERIOR, MAX_LOTE, _senal_argentina, prefiltro_exterior
from core.paths import DATA_DIR, MARCA_SESION, sesion_marcada

logger = logging.getLogger(__name__)
# Una página sin letras (p. ej. «. . .» de un sitio armado con JavaScript) no trae el aviso
_RE_LETRA = re.compile(r"[^\W\d_]")

PRIORIDAD_FORZADA = 2
PRIORIDAD_AUTO = 1
PRIORIDAD_BACKLOG = 0


def es_solo_url(texto: str) -> bool:
    """Una única URL http(s), sin espacios ni saltos de línea."""
    return bool(re.fullmatch(r"https?://[^\s]+", texto or ""))


# Palabras de una búsqueda laboral: un post que las tiene y el clasificador ve como «no oferta» se descarta
# (se puede recuperar) en lugar de borrarse
_RE_SENAL_BUSQUEDA = re.compile(
    r"\b(?:buscamos|busco|estamos buscando|b[uú]squeda|vacante\w*|sumate|sumamos|incorporamos|postul\w*|"
    r"cv|curr[ií]cul\w*|hiring|we'?re hiring|join (?:our|the) team|apply|job opening|position|"
    r"oportunidad laboral|oferta laboral|se busca)\b",
    re.IGNORECASE,
)


def orden_generacion(posts: List[dict]) -> List[dict]:
    """Orden: prioridad 2/1/0, contacto, rol, score descendente e id FIFO.

    No muta la lista de entrada. Conserva la prioridad de cada pedido encolado.
    """
    def clave(post: dict) -> tuple:
        prioridad = post.get("gen_prioridad")
        prioridad = prioridad if prioridad in (PRIORIDAD_FORZADA, PRIORIDAD_AUTO) else PRIORIDAD_BACKLOG
        links = post.get("links")
        links = links if isinstance(links, (list, tuple)) else []
        tiene_link = bool(
            post.get("canal") == "link"
            or any(isinstance(u, str) and u.strip() for u in links)
        )
        if tiene_email(post):
            canal_ord = 0
        elif tiene_link:
            canal_ord = 1
        else:
            canal_ord = 2

        rol_ord = grupo_rol(post)

        analisis = post.get("analisis")
        score_val = 0.0
        has_valid_score = 1
        if isinstance(analisis, dict):
            try:
                s = float(analisis.get("match_score"))
                if math.isfinite(s):
                    score_val = -s
                    has_valid_score = 0
            except (TypeError, ValueError, OverflowError):
                pass

        pid = post.get("id") or 0
        return (-prioridad, canal_ord, rol_ord, has_valid_score, score_val, pid)

    return sorted(list(posts), key=clave)


def orden_backlog(posts: List[dict]) -> List[dict]:
    """La cola y la toma usan el mismo orden de contacto, rol, score e id."""
    return orden_generacion(posts)


# Estados en los que el post ya tiene (o está generando) su CV: no se re-analiza ni se re-genera
ESTADOS_CON_CV = ("GENERANDO", "LISTO", "ENVIANDO", "ENVIADO")

BASE_DIR = Path(__file__).resolve().parent.parent

# Búsquedas: 100 % del usuario (`<DATA_DIR>/candidato/busquedas.json`, ver core/radar_consultas.py), en el orden
# del archivo. Son las del arranque; con `queries` por defecto, el Radar relee el archivo en cada vuelta. Las reglas
# de lectura (presupuesto, pausas, largo de cada lectura, pausa por bloqueo, topes del día) están en el perfil:
# `<DATA_DIR>/candidato/reglas_radar.json` (core/reglas_radar.py). Acá no hay ningún valor de esas reglas.
DEFAULT_QUERIES: List[str] = consultas_activas()
# Espera técnica (no es una regla de lectura) para releer las reglas del perfil cuando faltan o son inválidas
INTERVALO_REGLAS_S = 60


def interruptores() -> Tuple[bool, bool]:
    """(feed, búsquedas): los interruptores del Radar en el panel de procesos, guardados en `radar_state`.
    Las búsquedas arrancan apagadas; si la base no responde, quedan apagadas (la cuenta primero)."""
    try:
        st = radar_store.get_radar_state()
        return bool(st.get("feed_activo", True)), bool(st.get("busquedas_activas", False))
    except Exception:
        logger.exception("[Radar] No se pudieron leer los interruptores del Radar")
        return True, False


def busquedas_activas() -> bool:
    return interruptores()[1]

INTERVALO_LOGIN = 30
# Marca con la que se guardan (columna `query`) y se cuentan los posts leídos del feed de inicio
FEED_QUERY = "feed"

# Clasificación por lotes y análisis de ofertas
LOTE_TRIAGE = MAX_LOTE
MAX_INTENTOS_TRIAGE = 3
MAX_INTENTOS_ANALISIS = 3
# Trabajos de procesamiento a la vez (1 a la vez según spec orquestacion-prioridades)
CONCURRENCIA_PROCESO = 1

async def escribir_post_con_espera(post_id: int, **cambios: Any) -> bool:
    """`update_post` que espera (sin frenar el loop) si la base está bloqueada por otra escritura, hasta
    `ESPERA_MAX_ESCRITURA_S`. Cualquier otro error sale enseguida."""
    limite = time.monotonic() + ESPERA_MAX_ESCRITURA_S
    pausa = 0.25
    while True:
        try:
            return radar_store.update_post(post_id, **cambios)
        except sqlite3.OperationalError as e:
            if "locked" not in str(e) or time.monotonic() >= limite:
                raise
            logger.warning(f"[Radar] Base bloqueada al guardar el post {post_id}: reintento en {pausa:.2f} s")
            await asyncio.sleep(min(pausa, max(0.0, limite - time.monotonic())))
            pausa = min(pausa * 2, 5.0)


def _ticket(tipo: str, mensaje: str, componente: str = "radar", **kwargs: Any) -> int:
    """Ticket de error del Radar (core/tickets.py). Nunca corta el ciclo."""
    try:
        from core.tickets import reportar_error
        return reportar_error(componente, tipo, mensaje, **kwargs)
    except Exception:
        return 0


# Portales: componente de sus tickets
COMPONENTE_PORTALES = "portales"


# Detección de selectores rotos (spec §6)
CICLOS_VACIOS_PARA_ERROR = 2
# Ciclos seguidos en que el feed de inicio devuelve 0 posts antes de avisar en el log (sin cambiar
# el estado: el feed no decide si los selectores de la búsqueda están rotos)
CICLOS_FEED_VACIO_PARA_AVISO = 2

# Generación del CV: plazo de trabajo efectivo por intento (sin contar la espera de cuota de AGY),
# cada cuánto se mide, intentos antes de ERROR y espera antes de relanzar el worker si muere
PLAZO_TRABAJO_S = 2700
# Motivo del triage de un post que el clasificador no vio como oferta (se descarta, recuperable)
MOTIVO_NO_OFERTA = "El clasificador no lo vio como oferta"
MOTIVO_REPUBLICADA = "republicada"

# Rescate de fondo de las «Captura incompleta»: al final de cada ciclo (25–35 min)
# con al menos 20 min entre barridos, hasta 3 por barrido y 2 intentos por post. Relee la publicación,
# la imagen y los links, y decide con `aviso_util`.
RESCATE_CADA_S = 20 * 60
RESCATE_POR_BARRIDO = 3
RESCATE_MAX_INTENTOS = 2
# Reintento automático de un análisis que falló por la IA (timeout, 503 o respuesta inválida).
# Reglas a la vista para el usuario: minutos que el post espera en ERROR antes de volver solo a OFERTA, y
# cuántas veces vuelve solo; después queda en ERROR y lo reintenta el botón Analizar. Nunca con el análisis
# en pausa, sin cuota de AGY ni con AGY saturado (modo serie por 503).
ESPERA_REINTENTO_AUTOMATICO_MIN = 30
MAX_REINTENTOS_AUTOMATICOS = 2
# Generación: una que falló por AGY (timeout del Redactor, «Fallo al ejecutar agy CLI», 503) vuelve sola a
# GENERANDO pasados ESPERA_REINTENTO_GENERACION_MIN, hasta MAX_REINTENTOS_GENERACION_AUTO veces; nunca con la generación
# en pausa, sin cuota ni con AGY saturado. Un rechazo de la revisión de calidad tiene CORRECCIONES_DE_CALIDAD pasadas
# del auditor (lo que hace «remediar») antes de ir a ERROR; las reglas de calidad no cambian.
ESPERA_REINTENTO_GENERACION_MIN = 30
MAX_REINTENTOS_GENERACION_AUTO = 2
CORRECCIONES_DE_CALIDAD = 1
# Segundos que el Radar reintenta guardar un resultado (análisis) si la base está bloqueada por otra
# escritura; si igual no puede, el post vuelve a la cola en vez de quedar a medias.
ESPERA_MAX_ESCRITURA_S = 60
_RE_TEXTO_CORTADO = re.compile(r"ver más|…\s*más|activar para ver una imagen", re.I)
TICK_PLAZO_S = 5.0
REINICIO_WORKER_S = 2.0
MAX_INTENTOS_GENERACION = 2
ERROR_PLAZO = "Falló la generación: superó {minutos} min de trabajo (sin contar la espera de cuota) dos veces"
AVISO_PLAZO = ("La generación superó {minutos} min de trabajo (sin contar la espera de cuota): "
               "se reintenta una vez")

# Envíos (spec §2.4 / §6)
PAUSA_ENTRE_ENVIOS = (30.0, 90.0)
BACKOFF_SMTP_TEMPORAL = (30, 120, 300)

_FMT_FECHA = "%Y-%m-%d %H:%M:%S"


class AprobacionInvalidadaError(Exception):
    """El contenido cambió antes de entregar el mensaje: requiere otra revisión."""


def pausa_busquedas(ahora: Optional[datetime] = None, cuenta: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Calcula si las búsquedas están en pausa escalonada por bloqueo de LinkedIn: las horas de la pausa por bloqueo
    del perfil (`reglas_radar.json`), duplicadas con cada bloqueo en 24 h hasta la máxima. Con `cuenta`, solo mira
    las lecturas de esa cuenta (cada una tiene su propia pausa). Sin reglas válidas, None: el Radar no lee nada."""
    filtro = {"cuenta": cuenta} if cuenta else {}
    try:
        reglas = leer_reglas()
    except ReglasInvalidas:
        return None
    try:
        if ahora is None:
            ahora = datetime.now()
        if ahora.tzinfo is not None:
            ahora = ahora.astimezone().replace(tzinfo=None)

        resumen = radar_store.resumen_lecturas(**filtro)
        candidatos = [
            info for info in resumen.values()
            if info.get("ultimo_inicio") is not None and info.get("motivos")
        ]
        if not candidatos:
            return None

        mas_reciente = max(candidatos, key=lambda x: x["ultimo_inicio"])
        motivos = mas_reciente.get("motivos") or []
        if not motivos or motivos[0] != "bloqueo":
            return None

        ultimo = mas_reciente["ultimo_inicio"]
        if ultimo.tzinfo is not None:
            ultimo = ultimo.astimezone().replace(tzinfo=None)

        ultimo_utc = ultimo.astimezone(timezone.utc)
        desde_bloqueos = ultimo_utc - timedelta(hours=24)
        bloqueos = radar_store.inicios_bloqueo(desde_bloqueos, **filtro)
        k = max(1, len(bloqueos))
        horas = min(reglas.bloqueo_primera_h * (2 ** (k - 1)), reglas.bloqueo_maxima_h)
        hasta = ultimo + timedelta(hours=horas)

        if hasta > ahora:
            return {
                "desde": ultimo.isoformat(timespec="seconds"),
                "hasta": hasta.isoformat(timespec="seconds"),
                "horas": horas,
                "bloqueos_24h": k,
            }
        return None
    except Exception:
        logger.exception("[Radar] Error calculando pausa de búsquedas")
        return None


class RadarLoop:
    def __init__(
        self,
        harvester_factory: Callable[[], Any],
        triage_fn: Callable[[List[Dict[str, Any]]], Awaitable[Dict[int, Dict[str, Any]]]],
        analyze_fn: Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]],
        pipeline_fn: Callable[[Dict[str, Any]], Awaitable[int]],
        compose_fn: Callable[[Dict[str, Any], int], Awaitable[Dict[str, Any]]],
        send_fn: Callable[[Dict[str, Any]], Awaitable[str]],
        broadcast: Callable[[str, Dict[str, Any]], Awaitable[None]],
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rng: Optional[random.Random] = None,
        queries: Optional[List[str]] = None,
        debug_dir: Path = DATA_DIR / "radar_debug",
        now: Callable[[], datetime] = datetime.now,
        plazo_trabajo_s: float = PLAZO_TRABAJO_S,
        tick_plazo_s: float = TICK_PLAZO_S,
        reloj: Callable[[], float] = time.monotonic,
        cuota: Callable[[], Dict[str, Any]] = CUOTA.estado,
        ingest_fn: Optional[Callable[[dict], Awaitable[dict]]] = None,
        generation_workers: int = 1,
        image_reader_fn: Optional[Callable[[List[str]], Awaitable[str]]] = None,
        prepare_fn: Optional[Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]] = None,
        perfil_secundario: Optional[Path] = None,
        portales_fn: Optional[Callable[[str, str, ReglaPortal], Awaitable[List[Dict[str, Any]]]]] = None,
    ):
        if type(generation_workers) is not int or generation_workers not in (1, 2):
            raise ValueError("generation_workers debe ser 1 o 2")
        self._harvester_factory = harvester_factory
        # Segunda cuenta de LinkedIn (opcional): su sesión vive en otra carpeta de perfil. Está activa
        # cuando existe la marca que deja el inicio de sesión. Nunca hay dos Edge abiertos a la vez.
        self._perfil_secundario = Path(perfil_secundario) if perfil_secundario else DATA_DIR / "linkedin_profile_2"
        self._cuenta_activa = radar_store.CUENTA_PRINCIPAL
        self._cuenta_lectura = radar_store.CUENTA_PRINCIPAL
        self._login_secundaria_pedido = False
        self._triage_fn = triage_fn
        self._analyze_fn = analyze_fn
        self._pipeline_fn = pipeline_fn
        self._compose_fn = compose_fn
        self._send_fn = send_fn
        self._broadcast = broadcast
        self._sleep = sleep
        self._rng = rng or random.Random()
        # Sin `queries` las búsquedas salen de busquedas.json y se releen en cada vuelta (Mi perfil)
        self._busquedas_del_archivo = queries is None
        # El orden del archivo es la prioridad del usuario: se leen así, sin agrupar por idioma
        self._queries = list(DEFAULT_QUERIES if queries is None else queries)
        self._reglas: Optional[Reglas] = None
        self._debug_dir = Path(debug_dir)
        self._now = now
        self._plazo_trabajo_s = plazo_trabajo_s
        self._tick_plazo_s = tick_plazo_s
        self._reloj = reloj
        self._cuota = cuota
        self._ingest_fn = ingest_fn
        self._generation_workers = generation_workers
        self._image_reader_fn = image_reader_fn if image_reader_fn is not None else leer_imagenes
        self._prepare_fn = prepare_fn
        # Portales: la consulta a un portal (core/portales.buscar); en los tests, respuestas grabadas
        self._portales_fn = portales_fn if portales_fn is not None else portales.buscar
        self._lecturas_fallidas: Set[int] = set()
        self._ultimo_rescate: Optional[float] = None
        self._rescatando: Set[int] = set()

        self._harvester: Any = None
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._generation_event = asyncio.Event()
        self._generation_change = asyncio.Event()
        self._lifecycle_lock = asyncio.Lock()

        # Worker de procesamiento (clasificación + análisis)
        self._proc_task: Optional[asyncio.Task] = None
        self._proc_event = asyncio.Event()
        # Mitades de lotes de clasificación que fallaron (ids): esperan su turno en la cola
        self._mitades: List[List[int]] = []
        # Ids tomados por un trabajo en curso (no se vuelven a tomar hasta que termine)
        self._en_vuelo: Set[int] = set()
        # Worker de generación (`_etapas`: última etapa informada de cada post que se está generando)
        self._gen_task: Optional[asyncio.Task] = None
        self._etapas: Dict[int, str] = {}
        # La reserva precede al arranque de cada vigilante y se conserva hasta drenarlo.
        self._reservas_gen: Set[int] = set()
        self._vigilantes_gen: Dict[int, asyncio.Task] = {}
        self._trabajos_gen: Dict[int, asyncio.Task] = {}
        self._progresos: Dict[int, Dict[str, Any]] = {}
        self._reinicio_gen: Optional[asyncio.TimerHandle] = None
        # Worker de envío: cola de ids + ids en cola o en proceso (evita duplicados)
        self._send_task: Optional[asyncio.Task] = None
        self._send_queue: "asyncio.Queue[int]" = asyncio.Queue()
        self._en_cola: Set[int] = set()
        self._send_idle = False  # True solo mientras espera en `queue.get()` (cancelable sin riesgo)
        # Solo lo activa stop_for_shutdown: interrumpe las esperas del worker de envío
        self._send_stop_event = asyncio.Event()
        # `_cerrando`: hay un _shutdown en curso. `_apagado`: el servidor se cerró
        # (stop_for_shutdown); no se crean navegador ni workers hasta start()/resume_if_needed().
        self._cerrando = False
        self._apagado = False

        # Errores del ciclo en curso: se mantienen visibles (estado ERROR) durante la pausa
        self._errores_ciclo: List[str] = []
        self._ultimo_html = ""
        # Ciclos seguidos con el feed de inicio leído sin posts (solo para avisar en el log)
        self._feed_vacios_consecutivos = 0
        # Rotación de búsquedas: última lectura de cada una y lo guardado en la lectura en curso
        self._ultima_lectura: Dict[str, datetime] = {}
        self._guardados_lectura = 0
        self._ultimo_motivo_fin: Optional[str] = None
        self._ultimo_parseados = 0

    @property
    def etapas_generacion(self) -> Dict[int, str]:
        """Última etapa informada de cada post que se está generando (para el estado de la cola)."""
        return dict(self._etapas)

    @property
    def generando_ahora(self) -> Optional[int]:
        """Representante estable: el primero reservado que todavía no terminó."""
        return next(iter(self._vigilantes_gen), None)

    @property
    def generando_ids(self) -> Set[int]:
        """Copia de todos los ids reservados por la generación."""
        return set(self._reservas_gen)

    @property
    def en_vuelo(self) -> Set[int]:
        """Copia del conjunto de ids de posts actualmente en procesamiento."""
        return set(self._en_vuelo)

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        """Marca `desired_running` y lanza el loop si no está corriendo (idempotente)."""
        async with self._lifecycle_lock:
            radar_store.update_radar_state(desired_running=True)
            self._apagado = False
            if self.is_running:
                return
            self._stop_event = asyncio.Event()
            await self._set_estado("CORRIENDO", "", next_cycle_at=None)
            self._task = asyncio.create_task(self._harvest_loop(), name="radar-harvest")
            # Los otros workers: procesamiento, generación (espera wake) y envío (espera la cola)
            self._wake_processing()
            self._ensure_generation_worker()
            self._encolar_aprobados()
        await self.generar_automaticos()

    async def stop(self) -> None:
        """Detiene el radar: `desired_running=False`, cancela lectura y generación, cierra el
        navegador, `DETENIDO`. El envío en curso y lo aprobado en cola salen igual."""
        async with self._lifecycle_lock:
            radar_store.update_radar_state(desired_running=False)
            await self._shutdown(servidor=False)

    async def stop_for_shutdown(self) -> None:
        """Como `stop()` pero conserva `desired_running`, para reanudar en el próximo arranque.

        Además corta el worker de envío sin interrumpir un `send_fn` en curso: lo aprobado que
        quede en cola sigue `LISTO` con `aprobado_at` y se reencola en el próximo arranque.
        """
        async with self._lifecycle_lock:
            await self._shutdown(servidor=True)

    async def resume_if_needed(self) -> None:
        """Al arrancar el servidor: reconcilia envíos interrumpidos y reanuda si el usuario lo dejó corriendo.

        Aunque el radar quede detenido, retoma los manuales pendientes, los `GENERANDO` y reencola los `LISTO` aprobados
        (salvo con `envios_pausados`): lo que el usuario aprobó se envía igual.
        """
        self._apagado = False
        radar_store.reconcile_on_startup()
        if radar_store.get_radar_state()["desired_running"]:
            await self.start()
        elif not self.is_running:
            # Estado persistido de una ejecución anterior (p. ej. PAUSA tras un cierre abrupto)
            await self._set_estado("DETENIDO", "", next_cycle_at=None)
        try:
            hay_generando = bool(radar_store.list_posts(estado="GENERANDO", limit=1))
        except Exception:
            logger.exception("[Radar] No se pudieron leer los posts en GENERANDO")
            hay_generando = False
        if hay_generando or (radar_store.get_radar_state().get("backlog_activo")
                             and radar_store.candidatas_cola()):
            await self.wake_generation()
        # Los manuales y lo priorizado se procesan aunque el radar esté detenido.
        await self.wake_processing()
        self._encolar_aprobados()

    async def show_browser(self) -> None:
        """Muestra la ventana de Edge (para iniciar sesión); abre el harvester si hace falta.

        Toma el lock de ciclo de vida: un clic durante `stop()` espera a que se cierre el
        navegador anterior, así nunca hay dos Edge sobre el mismo perfil. Tras
        `stop_for_shutdown()` no abre nada.
        """
        async with self._lifecycle_lock:
            if self._apagado:
                logger.info("[Radar] show_browser ignorado: el servidor se está cerrando")
                return
            await self._get_harvester().show()

    async def wake_generation(self) -> None:
        """Avisa al worker de generación que hay posts en `GENERANDO` (lo lanza si no está corriendo)."""
        self._generation_event.set()
        self._generation_change.set()
        self._ensure_generation_worker()

    async def wake_processing(self) -> None:
        """Retoma manuales analizados y despierta el procesamiento de `NUEVO`/`OFERTA`."""
        manuales = radar_store.list_posts(estado="ANALIZADA", fuente=radar_store.FUENTE_MANUAL)
        await self.generar_automaticos([post["id"] for post in manuales])
        self._wake_processing()

    async def enqueue_send(self, post_id: int) -> None:
        """Encola el envío de un post `LISTO` ya aprobado (`aprobado_at` lo setea la API).

        Lanza el worker de envío si no está corriendo, aunque el radar esté detenido. También
        reencola otros aprobados pendientes (p. ej. diferidos por el tope diario de ayer).
        Lanza `ValueError` si el post no existe o no está `LISTO` y aprobado.
        """
        post = radar_store.get_post(post_id)
        if post is None:
            raise ValueError(f"El post {post_id} no existe")
        if not radar_store.es_canal_email(post):
            raise ValueError(f"El post {post_id} se postula por {post.get('canal')}: no se envía por Gmail")
        if post["estado"] != "LISTO" or not post.get("aprobado_at"):
            raise ValueError(
                f"El post {post_id} no se puede enviar: debe estar LISTO y aprobado "
                f"(estado={post['estado']}, aprobado_at={post.get('aprobado_at')})"
            )
        self._poner_en_cola(post_id)
        self._encolar_aprobados()

    async def retry(self, post_id: int) -> None:
        """Reintenta un post en `ERROR` desde el paso que corresponda (resetea `intentos` y `error`).

        Sin `triage` → `NUEVO`; sin mensaje generado → `GENERANDO` (+ wake); con mensaje → `LISTO`
        sin aprobación (el usuario vuelve a aprobar antes de cualquier envío). El mensaje generado
        es `email_subject` en el canal email y `email_body` en los demás (no llevan asunto).
        Lanza `ValueError` si el post no existe o no está en `ERROR`.
        """
        post = radar_store.get_post(post_id)
        if post is None:
            raise ValueError(f"El post {post_id} no existe")
        if post["estado"] != "ERROR":
            raise ValueError(f"Solo se reintentan posts en ERROR (estado={post['estado']})")
        generado = post.get("email_subject") if radar_store.es_canal_email(post) else post.get("email_body")
        fallo_generacion = (post.get("error") or "").startswith((
            "Falló la generación", "Generación detenida por saturación AGY (503)")) or post.get("postulacion_id")
        if not post.get("triage"):
            destino = "NUEVO"
            radar_store.update_post(post_id, estado=destino, intentos=0, error=None)
        elif not (generado or "").strip() and not fallo_generacion:
            # Falló el análisis: vuelve a la cola de ofertas
            destino = "OFERTA"
            radar_store.update_post(post_id, estado=destino, intentos=0, error=None)
        elif not (generado or "").strip():
            destino = "GENERANDO"
            radar_store.update_post(post_id, estado=destino, gen_prioridad=PRIORIDAD_FORZADA, gen_manual=0,
                                    intentos=0, error=None)
        else:
            destino = "LISTO"
            radar_store.update_post(post_id, estado=destino, aprobado_at=None, intentos=0, error=None)
        await self._emitir_post(post_id)
        if destino == "GENERANDO":
            await self.wake_generation()
        elif destino in ("NUEVO", "OFERTA"):
            self._wake_processing()

    async def prioritize(self, post_id: int) -> None:
        """Botón Analizar: pone el post primero en la cola (clasificación si aún no la tiene, si no
        análisis), aunque haya fallado antes o el radar esté detenido. `ValueError` si no existe
        o ya tiene CV (salvo una lista sin aprobar)."""
        post = radar_store.get_post(post_id)
        if post is None:
            raise ValueError(f"El post {post_id} no existe")
        # Una lista sin aprobar se vuelve a analizar y conserva su CV: el mensaje se borra y la cola lo rehace
        if post["estado"] in ESTADOS_CON_CV and not (post["estado"] == "LISTO" and not post.get("aprobado_at")):
            raise ValueError(f"El post {post_id} ya tiene CV (estado={post['estado']})")
        self._lecturas_fallidas.discard(post_id)
        if post["estado"] != "ANALIZANDO":
            destino = "OFERTA" if post.get("triage") else "NUEVO"
            radar_store.update_post(post_id, estado=destino, prioridad=1, intentos=0, error=None,
                                    email_subject="", email_body="")
            await self._emitir_post(post_id)
        self._wake_processing()

    async def generate_cv(self, post_id: int) -> None:
        """Botón Generar CV: arma el CV con el circuito de Postulaciones (reusa el análisis si ya
        lo tiene) y el mensaje del canal → `LISTO`. `ValueError` si no existe, ya tiene CV o se
        está analizando. Queda `gen_manual`: corre aunque la generación automática esté en pausa. Generar no es enviar: el envío sigue pidiendo aprobación."""
        post = radar_store.get_post(post_id)
        if post is None:
            raise ValueError(f"El post {post_id} no existe")
        if post["estado"] in ESTADOS_CON_CV:
            raise ValueError(f"El post {post_id} ya tiene CV (estado={post['estado']})")
        if post["estado"] == "ANALIZANDO":
            raise ValueError(f"El post {post_id} se está analizando: esperá a que termine")
        cambios: Dict[str, Any] = {}
        if post.get("postulacion_id") and not radar_store.postulacion_con_cv_listo(post["postulacion_id"]):
            # CV en ERROR, a medio redactar o vacío (p. ej. el post 6941): se rehace el CV completo
            # sobre la misma fila antes del mensaje. Nunca se salta directo al mensaje.
            cambios["postulacion_id"] = None
        radar_store.update_post(post_id, estado="GENERANDO", prioridad=0, gen_prioridad=PRIORIDAD_FORZADA,
                                gen_manual=1, intentos=0, error=None, **cambios)
        await self._emitir_post(post_id)
        await self.wake_generation()

    async def generar_automaticos(self, post_ids: Optional[List[int]] = None) -> List[int]:
        """Encola las manuales explícitas con prioridad 2 y despierta la generación.

        Las automáticas elegibles (score > 70, captura suficiente, oferta abierta) ya no se encolan acá:
        quedan ANALIZADA y el generador toma en cada turno la mejor de las colas dinámicas
        (`core.colas`). Devuelve los ids encolados (solo manuales)."""
        candidatos = ([radar_store.get_post(i) for i in post_ids] if post_ids is not None
                      else radar_store.list_posts(estado="ANALIZADA"))
        ids = []
        hay_candidata = False
        for post in candidatos:
            manual = bool(post and post.get("fuente") == radar_store.FUENTE_MANUAL)
            if post and post["estado"] == "ANALIZADA" and manual:
                if radar_store.tomar_automatico(post["id"], gen_prioridad=PRIORIDAD_FORZADA):
                    await self._emitir_post(post["id"])
                    ids.append(post["id"])
            elif post and post["estado"] == "ANALIZADA" and elegible_automatico(post):
                hay_candidata = True
                if post_ids is not None:
                    await self._emitir_post(post["id"])  # su lugar en la cola cambió
        if ids:
            logger.info(f"[Radar] CV encolados: posts {ids}")
        if ids or hay_candidata or radar_store.get_radar_state().get("backlog_activo"):
            await self.wake_generation()
        return ids

    async def reprocesar(self) -> Dict[str, List[int]]:
        """Vuelve a pasar lo obtenido por el flujo actual, con los workers de siempre (de a uno, a su
        ritmo): `ERROR` → `retry`; `LISTO` sin aprobar → `GENERANDO` (mensaje nuevo; el compose
        corrige el CV si hace falta); `ANALIZADA` → `OFERTA` (análisis nuevo y, sobre el umbral, CV).
        No toca `DESCARTADO` (decisión del usuario o del triage), `ENVIADO`, `ENVIANDO` ni un `LISTO`
        aprobado (está en la cola de envío)."""
        hechos: Dict[str, List[int]] = {"reintento": [], "mensaje": [], "analisis": []}
        for post in radar_store.list_posts(estado="ERROR"):
            try:
                await self.retry(post["id"])
                hechos["reintento"].append(post["id"])
            except ValueError:
                pass
        for post in radar_store.list_posts(estado="LISTO"):
            if post.get("aprobado_at") or not post.get("postulacion_id"):
                continue
            radar_store.update_post(post["id"], estado="GENERANDO", prioridad=0, gen_prioridad=PRIORIDAD_FORZADA,
                                    gen_manual=0, intentos=0, error=None)
            await self._emitir_post(post["id"])
            hechos["mensaje"].append(post["id"])
        for post in radar_store.list_posts(estado="ANALIZADA"):
            radar_store.update_post(post["id"], estado="OFERTA", intentos=0, error=None)
            await self._emitir_post(post["id"])
            hechos["analisis"].append(post["id"])
        logger.info(f"[Radar] Reproceso: {', '.join(f'{k} {len(v)}' for k, v in hechos.items())}")
        if hechos["mensaje"] or hechos["reintento"]:
            await self.wake_generation()
        self._wake_processing()
        return hechos

    async def resume_sends(self) -> None:
        """Limpia `envios_pausados` (tras corregir credenciales) y reencola los `LISTO` aprobados."""
        radar_store.update_radar_state(envios_pausados=False)
        await self._emitir_estado()
        self._encolar_aprobados()

    async def _shutdown(self, *, servidor: bool) -> None:
        self._cerrando = True
        try:
            self._stop_event.set()
            if servidor:
                self._apagado = True
                self._send_stop_event.set()
            task, self._task = self._task, None
            await self._cancelar(task, "El loop")
            proc, self._proc_task = self._proc_task, None
            await self._cancelar(proc, "El worker de procesamiento")
            # Un análisis cortado vuelve a la cola de ofertas
            radar_store.devolver_analizando_a_oferta()
            if self._reinicio_gen is not None:
                self._reinicio_gen.cancel()
                self._reinicio_gen = None
            gen, self._gen_task = self._gen_task, None
            # El supervisor cancela y drena todas las hijas; los posts siguen en cola.
            await self._cancelar(gen, "El worker de generación")
            await self._detener_envios(servidor)
            harvester, self._harvester = self._harvester, None
            if harvester is not None:
                try:
                    await harvester.close()
                except Exception:
                    logger.exception("[Radar] No se pudo cerrar el navegador")
            try:
                from core import navegador_portales
                await navegador_portales.cerrar()  # el Edge de Portales (Bumeran), si se abrió
            except Exception:
                logger.exception("[Portales] No se pudo cerrar el navegador de Portales")
            await self._set_estado("DETENIDO", "", next_cycle_at=None)
        finally:
            self._cerrando = False
        if not servidor:
            # Los wakes recibidos durante el cierre no pudieron crear workers. Retomar
            # manuales (incluso ANALIZADA) y generaciones antes de dar el cierre por terminado.
            hay_procesamiento = any(
                p.get("prioridad") or p.get("fuente") == radar_store.FUENTE_MANUAL
                for estado in ("NUEVO", "OFERTA") for p in radar_store.list_posts(estado=estado)
            )
            if (hay_procesamiento
                    or radar_store.list_posts(estado="ANALIZADA", fuente=radar_store.FUENTE_MANUAL, limit=1)):
                await self.wake_processing()
            if radar_store.list_posts(estado="GENERANDO", limit=1):
                await self.wake_generation()
            # Aprobaciones que llegaron durante el stop: salen igual con el radar detenido
            self._ensure_send_worker()

    @staticmethod
    async def _cancelar(task: Optional[asyncio.Task], nombre: str) -> None:
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception(f"[Radar] {nombre} terminó con error al detenerlo")

    async def _detener_envios(self, servidor: bool) -> None:
        """`stop()`: corta el worker de envío solo si está ocioso (esperando la cola).

        Cierre del servidor: además, si está ocupado espera a que termine el `send_fn` o la
        espera en curso (`_send_stop_event` ya está seteado, así que no toma otro id) y vacía la
        cola; lo aprobado sigue `LISTO` + `aprobado_at` en la DB para el próximo arranque.
        """
        task = self._send_task
        if task is not None and not task.done():
            if self._send_idle:
                # Bloqueado en `queue.get()`: cancelarlo no puede cortar ningún envío
                self._send_task = None
                await self._cancelar(task, "El worker de envío")
            elif servidor:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    logger.exception("[Radar] El worker de envío terminó con error al cerrarlo")
                self._send_task = None
        if servidor:
            while not self._send_queue.empty():
                self._send_queue.get_nowait()
            self._en_cola.clear()

    # ------------------------------------------------------------------
    # Loop principal
    # ------------------------------------------------------------------

    async def _harvest_loop(self) -> None:
        """Ciclo → pausa → ciclo… hasta `stop()`. Ninguna excepción (salvo cancelación) lo termina."""
        while not self._stop_event.is_set():
            # Envíos diferidos por el tope diario: tras el cambio de día vuelven a salir solos
            self._encolar_aprobados()
            # Si el worker de generación se cortó por un error inesperado, se relanza
            self._ensure_generation_worker()
            try:
                await self.run_cycle()
            except Exception as e:
                logger.exception("[Radar] Error inesperado en el ciclo")
                self._errores_ciclo.append(f"Error inesperado en el ciclo: {e}")
            if self._stop_event.is_set():
                break
            # Portales: su propia vuelta, sin LinkedIn ni sus cuentas (corre aunque LinkedIn no tenga reglas)
            try:
                await self.vuelta_portales()
            except Exception:
                logger.exception("[Radar] Error inesperado en la vuelta de Portales")
            if self._stop_event.is_set():
                break
            # Entre ciclos (el navegador queda libre): rescate de las capturas incompletas
            try:
                await self._rescate_si_toca()
            except Exception:
                logger.exception("[Radar] Error inesperado en el rescate de capturas")
            try:
                await self.reintentar_analisis_fallidos()
            except Exception:
                logger.exception("[Radar] Error inesperado en el reintento de análisis fallidos")
            try:
                await self.reintentar_generacion_fallida()
            except Exception:
                logger.exception("[Radar] Error inesperado en el reintento de generaciones fallidas")
            segundos = self._segundos_pausa()
            try:
                await self._entrar_en_pausa(segundos)
            except Exception:
                logger.exception("[Radar] No se pudo registrar la pausa")
            if not await self._wait(segundos):
                break

    def _busquedas_pausadas_hasta(self) -> Optional[datetime]:
        """Hasta cuándo LinkedIn pausó a la cuenta de búsquedas del perfil (None: no está pausada). Las búsquedas
        se hacen solo con esa cuenta: la pausa de otra no importa."""
        reglas = self._reglas_vigentes()
        if reglas is None or reglas.cuenta_busquedas is None:
            return None
        return self._pausa_de(reglas.cuenta_busquedas)

    def _ahora_utc(self) -> datetime:
        """El reloj del Radar (`now`: hora local sin zona, como datetime.now()) en UTC."""
        ahora = self._now()
        return (ahora.astimezone() if ahora.tzinfo is None else ahora).astimezone(timezone.utc)

    def _presupuesto_busquedas(self, cuenta: Optional[str] = None) -> int:
        """Cantidad de búsquedas disponibles según el presupuesto por hora y por día del perfil.
        Con `cuenta`, el de esa cuenta: cada una tiene el suyo. Si en las últimas 24 h LinkedIn
        pausó a todas las cuentas, el presupuesto de cada una baja al porcentaje del perfil. Sin reglas, 0."""
        reglas = self._reglas_vigentes()
        if not busquedas_activas() or reglas is None:
            return 0
        filtro = {"cuenta": cuenta} if cuenta else {}
        try:
            # El reloj del Radar (`now`), no el del sistema: lecturas y presupuesto miden con el mismo
            ahora_utc = self._ahora_utc()
            medianoche = ahora_utc.astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
            usadas_hora = radar_store.contar_busquedas(ahora_utc - timedelta(hours=1), **filtro)
            usadas_dia = radar_store.contar_busquedas(medianoche, **filtro)
            dos_pausadas = False
            if cuenta:
                hace_un_dia = ahora_utc - timedelta(hours=24)
                cuentas = self.cuentas()
                dos_pausadas = len(cuentas) > 1 and all(
                    radar_store.inicios_bloqueo(hace_un_dia, cuenta=c) for c in cuentas)
            por_hora, por_dia = reglas.presupuesto(dos_pausadas)
            return max(0, min(por_hora - usadas_hora, por_dia - usadas_dia))
        except Exception:
            logger.exception("[Radar] No se pudo consultar el presupuesto de búsquedas")
            return 0

    async def run_cycle(self) -> None:
        """Una vuelta: el feed (si está prendido) y después la ronda de búsquedas en el orden del archivo, con las
        reglas del perfil. Sin reglas válidas no se lee nada."""
        self._errores_ciclo = []
        radar_store.get_radar_state()  # aplica el reinicio diario de contadores
        reglas = await self._cargar_reglas()
        if reglas is None:
            return
        if self._busquedas_del_archivo:
            self._recargar_busquedas()
        if self._login_secundaria_pedido and not await self._login_secundaria():
            return
        pausado_hasta = self._busquedas_pausadas_hasta()
        feed_activo, con_busquedas = interruptores()
        # Cada lectura usa la cuenta del perfil; si falta en el perfil o no tiene sesión, esa
        # lectura no se hace: nunca se cae en la otra cuenta
        sin_cuenta_feed = self._cuenta_no_disponible(reglas.cuenta_feed, "cuenta_feed", "el feed")
        sin_cuenta_busquedas = self._cuenta_no_disponible(
            reglas.cuenta_busquedas, "cuenta_busquedas", "las búsquedas")
        feed_activo = feed_activo and sin_cuenta_feed is None
        if con_busquedas and sin_cuenta_busquedas is not None:
            await self._set_estado("CORRIENDO", f"Sin búsquedas: {sin_cuenta_busquedas}")
            con_busquedas = False
        elif not con_busquedas:
            await self._set_estado("CORRIENDO", "Solo feed: búsquedas desactivadas" if feed_activo
                                   else "Sin lecturas: feed y búsquedas desactivados")
        elif pausado_hasta is not None:
            await self._set_estado("CORRIENDO", f"Búsquedas pausadas por LinkedIn hasta las {pausado_hasta:%H:%M}")
        else:
            await self._set_estado("CORRIENDO", "")
        con_ronda = con_busquedas and pausado_hasta is None
        vistas: Set[str] = set()

        def lecturas():
            """El feed primero; después la ronda en el orden del archivo, desde donde cortó la vuelta anterior."""
            if feed_activo:
                yield FEED_QUERY, True
            while con_ronda:
                q = self._siguiente_de_la_ronda(vistas)
                if q is None:
                    return
                vistas.add(q)
                yield q, False

        consultas_ok = 0
        leidos_ciclo = 0
        primera = True
        for query, es_feed in lecturas():
            if self._stop_event.is_set():
                return
            # Apagadas a mitad del ciclo: no se hace ni una búsqueda más (el feed ya se leyó primero)
            if not es_feed and not busquedas_activas():
                break
            # El feed, con su cuenta del perfil; cada búsqueda, con la cuenta de búsquedas si puede (si no, ninguna)
            cuenta = reglas.cuenta_feed if es_feed else self._cuenta_para_buscar()
            if cuenta is None:
                break
            if not primera and not await self._wait(self._rng.uniform(*reglas.pausa_entre_lecturas_s)):
                return
            primera = False
            if not es_feed:
                self._ultima_lectura[query] = self._now()
            self._guardados_lectura = 0
            try:
                leidos = await self._leer_con_cuenta(cuenta, query, es_feed)
            except _Detenido:
                return
            except Exception as e:
                # Un error de Playwright en una lectura no corta el ciclo: se sigue con la próxima
                nombre = "el feed" if es_feed else f"la consulta {query!r}"
                logger.exception(f"[Radar] Error leyendo {nombre}", extra={"sin_ticket": True})
                await self._ticket_con_captura(
                    "lectura_fallida", f"Error leyendo {nombre}: {e}", excepcion=e,
                    datos={"consulta": "feed" if es_feed else query, "cuenta": self._cuenta_lectura})
                detalle = (
                    f"Error leyendo el feed: {e}" if es_feed
                    else f"Error leyendo la consulta '{query}': {e}"
                )
                self._errores_ciclo.append(detalle)
                await self._set_estado("ERROR", detalle)
                if not es_feed:
                    self._marcar_en_la_ronda(query)  # una búsqueda que falla no traba la ronda
                continue
            if es_feed:
                self._registrar_lectura_feed(leidos)
            # El feed no cuenta para detectar selectores rotos de la búsqueda: si anda, taparía el fallo
            if not es_feed:
                if self._ultimo_motivo_fin == "bloqueo":
                    await self._capturar_bloqueo(query)
                    break
                if self._ultimo_motivo_fin == "sin_resultados" or (
                    self._ultimo_parseados == 0 and self._ultimo_motivo_fin != "login"
                ):
                    await self._reportar_sin_resultados(query)
                self._marcar_en_la_ronda(query)
                consultas_ok += 1
                leidos_ciclo += leidos

        await self._evaluar_ciclo_vacio(consultas_ok, leidos_ciclo)

        if self._errores_ciclo:
            await self._set_estado("ERROR", " | ".join(self._errores_ciclo))

    # ------------------------------------------------------------------ Portales

    async def vuelta_portales(self) -> Dict[str, Dict[str, Any]]:
        """Una consulta por portal, si le toca según sus reglas del perfil (ritmo y consultas por día). Un portal sin
        reglas válidas no corre y abre un ticket; uno apagado no corre. Devuelve lo que hizo cada portal."""
        validas, invalidas = leer_reglas_portales()
        for portal, motivo in invalidas.items():
            _ticket("reglas_invalidas", f"{NOMBRES_PORTALES[portal]} no corre: {motivo}",
                    componente=COMPONENTE_PORTALES, datos={"portal": portal})
        hecho: Dict[str, Dict[str, Any]] = {}
        pausados = portales_store.pausados()
        for portal, regla in validas.items():
            if self._stop_event.is_set():
                break
            # Un radar de portal pausado desde el panel de procesos no corre
            if not regla.activo or portal in pausados or not self._le_toca_al_portal(regla):
                continue
            busqueda = self._siguiente_busqueda_de_portal(regla)
            if busqueda is None:
                continue
            hecho[portal] = await self._consultar_portal(regla, busqueda)
        return hecho

    def _le_toca_al_portal(self, regla: ReglaPortal) -> bool:
        """Quedan consultas del día (desde la medianoche local) y pasó el ritmo desde la última."""
        ahora = self._ahora_utc()
        medianoche = ahora.astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
        if portales_store.consultas_desde(regla.portal, portales_store.ahora_iso(medianoche)) >= regla.consultas_por_dia:
            return False
        ultima = portales_store.ultima_consulta(regla.portal)
        if ultima is None:
            return True
        try:
            desde = datetime.fromisoformat(ultima)
        except ValueError:
            return True
        return ahora - desde >= timedelta(minutes=regla.minutos_entre_consultas)

    @staticmethod
    def _siguiente_busqueda_de_portal(regla: ReglaPortal) -> Optional[Dict[str, str]]:
        """La búsqueda del portal que hace más que no se consulta (las nunca consultadas primero; empate: el orden
        del archivo). Con el modo «todas» (sin búsquedas), cada listado del perfil es una «búsqueda» (Indeed,
        sin listados: una sola, sin palabras)."""
        portal = regla.portal
        if regla.modo == "todas":
            busquedas = [{"nombre": l, "palabras": l} for l in regla.listados] or [{"nombre": "todas", "palabras": ""}]
        else:
            busquedas = busquedas_de_portal(portal)
        if not busquedas:
            return None
        ultimas = portales_store.ultimas_por_busqueda(portal)
        return min(busquedas, key=lambda b: ultimas.get(b["nombre"]) or "")

    async def _consultar_portal(self, regla: ReglaPortal, busqueda: Dict[str, str]) -> Dict[str, Any]:
        portal, nombre = regla.portal, NOMBRES_PORTALES[regla.portal]
        inicio = portales_store.ahora_iso(self._ahora_utc())
        error: Optional[str] = None
        ofertas: List[Dict[str, Any]] = []
        try:
            ofertas = list(await self._portales_fn(portal, busqueda["palabras"], regla) or [])
        except asyncio.CancelledError:
            raise
        except portales.ErrorPortal as e:
            error = str(e)
        except Exception as e:
            logger.exception(f"[Portales] Falló la consulta a {nombre}", extra={"sin_ticket": True})
            error = f"{type(e).__name__}: {e}"
        nuevas = duplicadas = 0
        if error is None:
            for oferta in ofertas:
                try:
                    resultado, post_id = portales_store.guardar_oferta(oferta, busqueda["nombre"])
                except Exception as e:
                    logger.exception(f"[Portales] No se pudo guardar una oferta de {nombre}",
                                     extra={"sin_ticket": True})
                    error = f"No se pudo guardar una oferta: {type(e).__name__}: {e}"
                    continue
                if resultado == portales_store.NUEVA:
                    nuevas += 1
                    await self._emitir_post(post_id)
                elif resultado == portales_store.DUPLICADA:
                    duplicadas += 1
        portales_store.registrar_lectura(
            portal, busqueda["nombre"], busqueda["palabras"], inicio, portales_store.ahora_iso(self._ahora_utc()),
            traidas=len(ofertas), nuevas=nuevas, duplicadas=duplicadas, error=error)
        if error is not None:
            _ticket("consulta_fallida", f"{nombre}: {error}", componente=COMPONENTE_PORTALES,
                    datos={"portal": portal, "busqueda": busqueda["nombre"], "palabras": busqueda["palabras"]})
        if nuevas:
            self._wake_processing()
        detalle_error = f", error: {error}" if error else ""
        logger.info(f"[Portales] {nombre} · {busqueda['nombre']}: {len(ofertas)} traídas, {nuevas} nuevas, "
                    f"{duplicadas} duplicadas{detalle_error}")
        return {"busqueda": busqueda["nombre"], "traidas": len(ofertas), "nuevas": nuevas,
                "duplicadas": duplicadas, "error": error}

    @staticmethod
    def _lee_pagina_aviso(fuente: Any) -> bool:
        """El portal trae un resumen y el aviso completo se lee de su página (regla del perfil)."""
        if fuente not in NOMBRES_PORTALES:
            return False
        regla = leer_reglas_portales()[0].get(fuente)
        return bool(regla and regla.leer_pagina_aviso)

    async def _leer_pagina_de_portal(self, post: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """El aviso completo, leído de su página; None si no se pudo (se reintenta, nunca se analiza el resumen)."""
        try:
            if self._ingest_fn is None:
                raise RuntimeError("sin lector de páginas")
            contenido = await self._ingest_fn(post)
            texto = (contenido.get("texto") or "").strip()
            if not _RE_LETRA.search(texto):
                raise RuntimeError("página sin contenido")
            radar_store.update_post(post["id"], texto_pagina=texto,
                                    emails=list(dict.fromkeys([*(post.get("emails") or []),
                                                               *(contenido.get("emails") or [])])))
            return radar_store.get_post(post["id"]) or post
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"[Portales] No se pudo leer la página del post {post['id']}: {e}")
            return None

    async def _cargar_reglas(self) -> Optional[Reglas]:
        """Las reglas del perfil para esta vuelta. Si faltan o son inválidas, no se lee nada (la cuenta primero): el
        motivo queda en el panel y en un ticket, y se vuelve a mirar en INTERVALO_REGLAS_S."""
        try:
            self._reglas = leer_reglas()
            return self._reglas
        except ReglasInvalidas as e:
            motivo = str(e)
        except Exception as e:
            logger.exception("[Radar] No se pudieron leer las reglas del perfil", extra={"sin_ticket": True})
            motivo = f"No se pudieron leer las reglas del Radar: {e}"
        self._reglas = None
        _ticket("reglas_invalidas", f"El Radar no lee nada: {motivo}")
        self._errores_ciclo.append(f"Sin reglas del Radar: {motivo}")
        await self._set_estado("ERROR", f"Sin reglas del Radar: {motivo}")
        return None

    def _reglas_vigentes(self) -> Optional[Reglas]:
        """Las reglas de esta vuelta; fuera de una vuelta (o antes de la primera), las del perfil. None si no hay."""
        if self._reglas is not None:
            return self._reglas
        try:
            return leer_reglas()
        except ReglasInvalidas:
            return None

    def _ronda_leidas(self) -> List[str]:
        """Las búsquedas ya leídas en la ronda en curso (guardadas en `radar_state`: sobreviven a un reinicio)."""
        try:
            leidas = radar_store.get_radar_state().get("ronda_leidas") or []
        except Exception:
            logger.exception("[Radar] No se pudo leer la ronda de búsquedas; se toma como recién empezada")
            leidas = []
        return [q for q in leidas if q in self._queries]

    def _siguiente_de_la_ronda(self, vistas: Set[str]) -> Optional[str]:
        """La próxima búsqueda, en el orden del archivo: la primera que falta leer en la ronda y no
        se leyó en esta vuelta. Completa la ronda, empieza la siguiente desde la primera. None: no queda ninguna."""
        leidas = set(self._ronda_leidas())
        for q in self._queries:
            if q not in leidas and q not in vistas:
                return q
        return None

    def _marcar_en_la_ronda(self, query: str) -> None:
        """Anota `query` como leída en la ronda; con todas leídas, la ronda termina y la próxima empieza de cero."""
        try:
            leidas = self._ronda_leidas()
            if query not in leidas:
                leidas.append(query)
            if all(q in leidas for q in self._queries):
                leidas = []
            radar_store.update_radar_state(ronda_leidas=leidas)
        except Exception:
            logger.exception("[Radar] No se pudo guardar la ronda de búsquedas")

    def _recargar_busquedas(self) -> None:
        """Relee busquedas.json: lo que se guarda en el perfil rige desde la próxima vuelta, en el orden del archivo."""
        try:
            todas = consultas_activas()
        except Exception:
            logger.exception("[Radar] No se pudieron releer las búsquedas; siguen las de la vuelta anterior")
            return
        self._queries = list(todas)

    async def _leer_con_cuenta(self, cuenta: str, query: str, es_feed: bool) -> int:
        await self._usar_cuenta(cuenta)
        self._cuenta_lectura = cuenta
        return await self._leer_consulta(self._get_harvester(), query, feed=es_feed)

    async def _reportar_sin_resultados(self, query: str) -> None:
        """Cero resultados en una búsqueda es un error: captura + HTML y reporte."""
        nombre = ""
        try:
            nombre = nombre_de_consulta(query)
        except Exception:
            logger.exception("[Radar] No se pudo leer el nombre de la búsqueda", extra={"sin_ticket": True})
        base = self._debug_dir / "sin_resultados"
        slug = re.sub(r"[^0-9A-Za-z]+", "-", query).strip("-")[:40] or "consulta"
        carpeta = base / f"{self._now():%Y%m%d-%H%M%S}-{slug}"
        n = 2
        while carpeta.exists():
            carpeta = base / f"{self._now():%Y%m%d-%H%M%S}-{slug}-{n}"
            n += 1
        try:
            carpeta.mkdir(parents=True, exist_ok=True)
            try:
                await self._get_harvester().dump_debug(carpeta)
            except Exception:
                logger.exception("[Radar] Falló la captura de la búsqueda sin resultados; se guarda el último HTML",
                                 extra={"sin_ticket": True})
                (carpeta / "page.html").write_text(self._ultimo_html or "", encoding="utf-8")
        except Exception:
            logger.exception("[Radar] No se pudo guardar la captura de la búsqueda sin resultados",
                             extra={"sin_ticket": True})
        visible = f"«{nombre}» ({query})" if nombre else f"«{query}»"
        # Ticket (core/tickets.py) con la captura y el HTML de esa carpeta
        await self._ticket_con_captura(
            "sin_resultados", f"La búsqueda {visible} no trajo resultados en LinkedIn",
            datos={"consulta": query, "nombre": nombre, "cuenta": self._cuenta_lectura,
                   "motivo_fin": self._ultimo_motivo_fin, "parseados": self._ultimo_parseados,
                   "carpeta": str(carpeta)},
            carpeta=carpeta)
        self._errores_ciclo.append(f"Búsqueda sin resultados: {visible}. Captura en {carpeta}")

    async def _capturar_bloqueo(self, query: str = "") -> None:
        destino = self._debug_dir / "bloqueo"
        try:
            await self._get_harvester().dump_debug(destino)
        except Exception:
            logger.exception("[Radar] No se pudo guardar la captura de debug del bloqueo", extra={"sin_ticket": True})
        cuenta = getattr(self, "_cuenta_lectura", None) or "principal"
        await self._ticket_con_captura(
            "bloqueo", f"LinkedIn bloqueó las búsquedas de la cuenta «{cuenta}»",
            datos={"consulta": query, "cuenta": cuenta}, carpeta=destino)

    async def _ticket_con_captura(self, tipo: str, mensaje: str, *, datos: Dict[str, Any],
                                  excepcion: Optional[BaseException] = None,
                                  carpeta: Optional[Path] = None) -> int:
        """Ticket del Radar con captura y HTML de la página. Sin `carpeta`, se toman en una carpeta temporal."""
        import tempfile
        temporal = None
        try:
            if carpeta is None:
                temporal = tempfile.TemporaryDirectory(prefix="ticket_radar_", ignore_cleanup_errors=True)
                carpeta = Path(temporal.name)
                try:
                    await self._get_harvester().dump_debug(carpeta)
                except Exception:
                    pass
            captura = carpeta / "screenshot.png"
            html = carpeta / "page.html"
            return _ticket(tipo, mensaje, excepcion=excepcion, datos=datos,
                           captura=captura if captura.is_file() else None,
                           html=html if html.is_file() else (self._ultimo_html or None))
        except Exception:
            return 0
        finally:
            if temporal is not None:
                temporal.cleanup()

    # ------------------------------------------------------------------
    # Lectura
    # ------------------------------------------------------------------

    def _get_harvester(self) -> Any:
        if self._harvester is None:
            if self._cuenta_activa == radar_store.CUENTA_SECUNDARIA:
                self._harvester = self._harvester_factory(profile_dir=self._perfil_secundario)
            else:
                self._harvester = self._harvester_factory()
        return self._harvester

    # ------------------------------------------------------------------
    # Cuentas de LinkedIn
    # ------------------------------------------------------------------

    @property
    def _marca_secundaria(self) -> Path:
        return self._perfil_secundario / MARCA_SESION

    def cuentas(self) -> List[str]:
        """Cuentas con sesión iniciada: la principal siempre; la secundaria si ya inició sesión."""
        cuentas = [radar_store.CUENTA_PRINCIPAL]
        try:
            if sesion_marcada(self._perfil_secundario):
                cuentas.append(radar_store.CUENTA_SECUNDARIA)
        except OSError:
            pass
        return cuentas

    async def _usar_cuenta(self, cuenta: str) -> None:
        """Deja activa `cuenta`. Si cambia, cierra antes el Edge de la otra: nunca hay dos abiertos."""
        if cuenta == self._cuenta_activa:
            return
        harvester, self._harvester = self._harvester, None
        if harvester is not None:
            try:
                await harvester.close()
            except Exception:
                logger.exception("[Radar] No se pudo cerrar el navegador al cambiar de cuenta")
        self._cuenta_activa = cuenta

    def _pausa_de(self, cuenta: str) -> Optional[datetime]:
        try:
            p = pausa_busquedas(self._now(), cuenta=cuenta)
            return datetime.fromisoformat(p["hasta"]) if p else None
        except Exception:
            logger.exception("[Radar] No se pudo leer la pausa de la cuenta %s", cuenta)
            return None

    def _cuenta_no_disponible(self, cuenta: Optional[str], campo: str, que: str) -> Optional[str]:
        """El motivo por el que `cuenta` (la del perfil para `que`) no puede leer, o None si puede. Si falta en el
        perfil, abre un ticket: la lectura no se hace hasta que se cargue."""
        if cuenta is None:
            motivo = f"falta «{campo}» en las reglas del Radar (Mi perfil)"
            _ticket("reglas_invalidas", f"El Radar no lee {que}: {motivo}")
            return motivo
        if cuenta not in self.cuentas():
            return f"la cuenta {cuenta} no tiene la sesión iniciada"
        return None

    def _cuenta_para_buscar(self) -> Optional[str]:
        """La cuenta de búsquedas del perfil, si tiene sesión, no está pausada y le queda presupuesto. Si no, None:
        no se busca. Nunca otra cuenta (anoche se buscó con la principal)."""
        reglas = self._reglas_vigentes()
        cuenta = reglas.cuenta_busquedas if reglas is not None else None
        if cuenta is None or cuenta not in self.cuentas():
            return None
        if self._pausa_de(cuenta) is None and self._presupuesto_busquedas(cuenta) > 0:
            return cuenta
        return None

    def estado_cuentas(self) -> List[Dict[str, Any]]:
        """Para el panel: cada cuenta, si es la que está en uso y hasta cuándo está pausada."""
        activas = self.cuentas()
        resultado = []
        for cuenta in (radar_store.CUENTA_PRINCIPAL, radar_store.CUENTA_SECUNDARIA):
            con_sesion = cuenta in activas
            pausa = self._pausa_de(cuenta) if con_sesion and len(activas) > 1 else None
            resultado.append({
                "cuenta": cuenta, "con_sesion": con_sesion, "en_uso": cuenta == self._cuenta_activa,
                "pausada_hasta": pausa.isoformat(timespec="seconds") if pausa else None,
            })
        return resultado

    def pedir_login_secundaria(self) -> None:
        """El próximo ciclo abre Edge con el perfil de la cuenta 2 y espera a que el usuario inicie sesión."""
        self._login_secundaria_pedido = True

    async def _login_secundaria(self) -> bool:
        """Abre el perfil de la cuenta 2, espera el inicio de sesión del usuario y deja la marca.
        False si se detuvo el radar mientras esperaba."""
        self._login_secundaria_pedido = False
        await self._usar_cuenta(radar_store.CUENTA_SECUNDARIA)
        harvester = self._get_harvester()
        try:
            listo = await harvester.check_logged_in()
        except Exception:
            logger.exception("[Radar] No se pudo abrir el navegador de la cuenta 2")
            listo = False
        if not listo and not await self._esperar_login(
            harvester, "Iniciá sesión con la cuenta 2 de LinkedIn en la ventana de Edge."
        ):
            return False
        try:
            self._perfil_secundario.mkdir(parents=True, exist_ok=True)
            self._marca_secundaria.write_text(self._now().isoformat(timespec="seconds"), encoding="utf-8")
        except OSError:
            logger.exception("[Radar] No se pudo guardar la marca de sesión de la cuenta 2")
        return True

    async def _leer_consulta(self, harvester: Any, query: str, feed: bool = False) -> int:
        """Lee una consulta o el feed (reintentándola tras resolver un login) y devuelve los posts crudos contados."""
        total = 0
        parseados = 0
        inicio = self._ahora_utc().isoformat()
        pantallas = []
        scrolls = 0
        motivo_fin = "login"
        destinos = {}
        con_email: Set[str] = set()  # urns leídos con email en el texto capturado
        reglas = self._reglas_vigentes() or leer_reglas()  # el largo de cada lectura es del perfil
        try:
            while True:
                # Los ya leídos permiten cortar el scroll en cuanto no aparece nada nuevo
                conocidos = radar_store.urns_vistos()
                if feed:
                    resultado = await harvester.harvest_feed(
                        max_posts=reglas.feed_posts, max_scrolls=reglas.feed_scrolls, conocidos=conocidos
                    )
                else:
                    # Un solo largo para toda búsqueda, sin idioma: corta tras N pantallas sin nada nuevo
                    resultado = await harvester.harvest(
                        query, max_posts=reglas.busqueda_posts, max_scrolls=reglas.busqueda_scrolls,
                        conocidos=conocidos, paciencia=reglas.busqueda_pantallas_sin_nuevos,
                    )
                if resultado.html:
                    self._ultimo_html = resultado.html
                detalle = getattr(resultado, "lectura", {}) or {}
                pantallas.extend(detalle.get("pantallas", []))
                scrolls += detalle.get("scrolls", 0)
                motivo_fin = detalle.get("motivo_fin") or ("login" if resultado.needs_login else "max_scrolls")
                destinos_intento = {}
                urns_parseados = detalle.get("urns_parseados", [p["urn"] for p in (resultado.posts or [])])
                parseados += len(urns_parseados)
                con_email.update(p["urn"] for p in (resultado.posts or []) if p.get("urn") and p.get("emails"))
                total += await self._guardar_posts(
                    list(resultado.posts or []), query, fuente="feed" if feed else "busqueda",
                    destinos=destinos_intento,
                )
                for urn in urns_parseados:
                    # Parseado pero recortado por el largo de la lectura (el harvester no lo devolvió)
                    destinos_intento.setdefault(urn, "fuera_de_cupo")
                for urn, destino in destinos_intento.items():
                    # Conserva el destino previo si el reintento solo lo ve o lo recorta.
                    if urn not in destinos or destino not in ("ya_visto", "fuera_de_cupo"):
                        destinos[urn] = destino
                if not resultado.needs_login:
                    return total
                if not await self._esperar_login(harvester):
                    raise _Detenido()
                # Sesión recuperada: se repite la consulta, que quedó a medias
        finally:
            self._ultimo_motivo_fin = motivo_fin
            self._ultimo_parseados = parseados
            try:
                radar_store.registrar_lectura(
                    query=query, fuente="feed" if feed else "busqueda", inicio=inicio,
                    fin=self._ahora_utc().isoformat(), scrolls=scrolls,
                    motivo_fin=motivo_fin, pantallas=pantallas, parseados=parseados,
                    guardados=sum(d in radar_store.DESTINOS_NUEVOS for d in destinos.values()),
                    destinos=destinos,
                    con_mail=sum(1 for u, d in destinos.items() if d in radar_store.DESTINOS_NUEVOS and u in con_email),
                    **({"cuenta": self._cuenta_lectura}
                       if self._cuenta_lectura != radar_store.CUENTA_PRINCIPAL else {}),
                )
            except Exception:
                logger.exception("[Radar] No se pudo registrar la lectura de %r", query)

    async def _guardar_posts(
        self, posts: List[Dict[str, Any]], query: str, fuente: str = "busqueda",
        destinos: Optional[Dict[str, str]] = None,
    ) -> int:
        """Cuenta y guarda los posts nunca vistos: todos los que tienen texto (o imagen), en cualquier idioma y
        sin filtro de palabras (el triage decide si es una búsqueda laboral). Contadores del día: `leidos_feed` y
        `leidos_busquedas` (este último es el que mide el tope de posts del perfil). El contador `con_email`
        conserva su clave por compatibilidad con la UI pero cuenta los posts guardados.
        Devuelve los posts crudos leídos (también los ya vistos: sirven para detectar selectores rotos)."""
        if not posts:
            return 0
        destinos = destinos if destinos is not None else {}
        leidos = len(posts)
        por_urn = {p.get("urn"): p for p in posts if p.get("urn")}
        nunca_vistos = radar_store.filtrar_no_vistos(por_urn)
        destinos.update({u: "ya_visto" for u in por_urn})
        if not nunca_vistos:
            return leidos
        self._guardados_lectura += len(nunca_vistos)
        radar_store.registrar_vistos(nunca_vistos, query)
        radar_store.bump_counter("leidos_feed" if fuente == "feed" else "leidos_busquedas", len(nunca_vistos))

        nuevos: List[int] = []
        for post in (por_urn[u] for u in nunca_vistos):
            tiene_imagen = any(_es_imagen_feedshare_valida(url) for url in (post.get("imagenes") or []))
            if not (post.get("texto") or "").strip() and not tiene_imagen:
                destinos[post["urn"]] = "sin_texto"
                continue
            destinos[post["urn"]] = "error_guardado"
            try:
                if (post.get("texto") or "").strip() and radar_store.existe_texto(post["texto"]):
                    destinos[post["urn"]] = "texto_repetido"
                    continue  # la misma oferta republicada con otro urn
                post_id = radar_store.insert_post_if_new(post, query, fuente=fuente)
            except Exception:
                logger.exception(f"[Radar] No se pudo guardar el post {post.get('urn')!r}")
                continue
            if post_id is None:
                continue
            if radar_store.get_post(post_id)["estado"] == "IGNORADA":
                destinos[post["urn"]] = "ignorada_vieja"
                await self._emitir_post(post_id)  # publicada hace un mes o más: no se procesa
                continue
            if not necesita_lectura(post) and await self._descartar_exterior(
                dict(post, id=post_id, texto=texto_capturado(post))
            ):
                destinos[post["urn"]] = "exterior"
                continue
            destinos[post["urn"]] = "guardado"
            nuevos.append(post_id)
        if nuevos:
            radar_store.bump_counter("con_email", len(nuevos))
            # Se clasifican mientras sigue la lectura (Mi feed se lee primero)
            self._wake_processing()
        for post_id in nuevos:
            await self._emitir_post(post_id)
        await self._emitir_estado()
        return leidos

    async def _esperar_login(self, harvester: Any, mensaje: Optional[str] = None) -> bool:
        """`ESPERANDO_LOGIN`: muestra la ventana y sondea cada 30 s. False si se detuvo el radar."""
        await self._set_estado(
            "ESPERANDO_LOGIN", mensaje or "Iniciá sesión en LinkedIn en la ventana de Edge para continuar."
        )
        try:
            await harvester.show()
        except Exception:
            logger.exception("[Radar] No se pudo mostrar la ventana del navegador")
        while True:
            if not await self._wait(INTERVALO_LOGIN):
                return False
            try:
                if await harvester.check_logged_in():
                    break
            except Exception:
                # p. ej. el usuario cerró Edge: el harvester se recupera en la próxima llamada
                logger.exception("[Radar] Falló la verificación de sesión")
        await self._set_estado("CORRIENDO", "")
        return True

    def _registrar_lectura_feed(self, leidos: int) -> None:
        """Avisa en el log si el feed devolvió 0 posts en N ciclos seguidos (no cambia el estado)."""
        if leidos > 0:
            self._feed_vacios_consecutivos = 0
            return
        self._feed_vacios_consecutivos += 1
        if self._feed_vacios_consecutivos >= CICLOS_FEED_VACIO_PARA_AVISO:
            logger.warning(
                f"[Radar] El feed de inicio devolvió 0 posts en {self._feed_vacios_consecutivos} ciclos "
                "seguidos: puede haber cambiado el DOM del feed o la sesión no carga publicaciones"
            )

    async def _evaluar_ciclo_vacio(self, consultas_ok: int, leidos_ciclo: int) -> None:
        """2 ciclos seguidos con 0 posts en todas las consultas → ERROR + dump de depuración."""
        if leidos_ciclo > 0:
            radar_store.update_radar_state(ciclos_vacios_consecutivos=0)
            return
        if consultas_ok == 0:
            # Ninguna consulta se completó (errores o tope diario): no hay evidencia de selectores rotos
            return
        vacios = int(radar_store.get_radar_state()["ciclos_vacios_consecutivos"]) + 1
        radar_store.update_radar_state(ciclos_vacios_consecutivos=vacios)
        if vacios < CICLOS_VACIOS_PARA_ERROR:
            return
        detalle = (
            f"Posibles selectores rotos: 0 posts en todas las consultas durante {vacios} ciclos consecutivos."
        )
        if vacios == CICLOS_VACIOS_PARA_ERROR:
            destino = await self._volcar_debug()
            if destino is not None:
                detalle += f" Debug en {destino}"
            _ticket("selectores_rotos", "Posibles selectores rotos: 0 posts en todas las consultas varios ciclos seguidos",
                    datos={"ciclos_vacios": vacios, "cuenta": getattr(self, "_cuenta_lectura", None)},
                    captura=(destino / "screenshot.png") if destino and (destino / "screenshot.png").is_file() else None,
                    html=(destino / "page.html") if destino and (destino / "page.html").is_file() else None)
        self._errores_ciclo.append(detalle)
        await self._set_estado("ERROR", detalle)

    async def _volcar_debug(self) -> Optional[Path]:
        destino = self._debug_dir / self._now().strftime("%Y%m%d-%H%M%S")
        try:
            destino.mkdir(parents=True, exist_ok=True)
        except Exception:
            logger.exception("[Radar] No se pudo crear el directorio de debug")
            return None
        try:
            await self._get_harvester().dump_debug(destino)
        except Exception:
            logger.exception("[Radar] Falló dump_debug; se guarda el último HTML leído")
            try:
                (destino / "page.html").write_text(self._ultimo_html, encoding="utf-8")
            except Exception:
                logger.exception("[Radar] No se pudo guardar el HTML de debug")
        return destino

    # ------------------------------------------------------------------
    # Triage
    # ------------------------------------------------------------------

    def _wake_processing(self) -> None:
        """Avisa al worker de procesamiento que hay trabajo (lo lanza si no está corriendo)."""
        self._proc_event.set()
        self._ensure_processing_worker()

    def _ensure_processing_worker(self) -> None:
        if self._cerrando or self._apagado:
            return
        if self._proc_task is None or self._proc_task.done():
            self._proc_task = asyncio.create_task(self._processing_worker(), name="radar-proceso")

    async def _processing_worker(self) -> None:
        """Hasta `CONCURRENCIA_PROCESO` trabajos a la vez, tomados en el orden de `_siguiente_trabajo`.
        Sin trabajo espera un wake o, con el radar detenido, termina. Al cancelarlo (stop) cancela
        los trabajos en curso."""
        en_curso: Set[asyncio.Task] = set()
        self._en_vuelo = set()
        try:
            while True:
                # Se limpia antes de buscar trabajo: un wake posterior no se pierde
                self._proc_event.clear()
                while len(en_curso) < CONCURRENCIA_PROCESO:
                    trabajo = self._siguiente_trabajo(todo=self.is_running)
                    if trabajo is None:
                        break
                    # Se reservan ya: la tarea no arranca hasta el próximo await y si no, el mismo
                    # post se volvería a elegir en esta vuelta
                    ids = {p["id"] for p in trabajo[1]}
                    self._en_vuelo |= ids
                    en_curso.add(asyncio.create_task(self._ejecutar_reservado(trabajo, ids)))
                if not en_curso:
                    if not self.is_running:
                        return
                    await self._proc_event.wait()
                    continue
                aviso = asyncio.ensure_future(self._proc_event.wait())
                try:
                    hechos, _ = await asyncio.wait(en_curso | {aviso}, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    if not aviso.done():
                        aviso.cancel()
                for tarea in hechos - {aviso}:
                    en_curso.discard(tarea)
                    if not tarea.cancelled() and tarea.exception() is not None:
                        logger.error("[Radar] Error inesperado en un trabajo de procesamiento",
                                     exc_info=tarea.exception())
        finally:
            for tarea in en_curso:
                tarea.cancel()
            await asyncio.gather(*en_curso, return_exceptions=True)
            self._en_vuelo = set()

    async def _ejecutar_reservado(self, trabajo: Tuple[str, List[Dict[str, Any]]], ids: Set[int]) -> None:
        """Ejecuta el trabajo y al final libera sus posts reservados (`ids`, en `_en_vuelo`)."""
        try:
            await self._ejecutar(trabajo)
        finally:
            self._en_vuelo -= ids
            # Se liberó un lugar: el worker busca el próximo trabajo
            self._proc_event.set()

    async def procesar_pendientes(self, todo: bool = True) -> None:
        """Procesa la cola hasta vaciarla, en el orden del worker (`todo=False`: solo lo priorizado)."""
        while (trabajo := self._siguiente_trabajo(todo)) is not None:
            await self._ejecutar(trabajo)

    async def _ejecutar(self, trabajo: Tuple[str, List[Dict[str, Any]]]) -> None:
        tipo, posts = trabajo
        with carril_agy("analisis"):
            if tipo == "clasificar":
                await self._clasificar(posts)
            else:
                await self._analizar(posts[0])

    def _siguiente_trabajo(self, todo: bool) -> Optional[Tuple[str, List[Dict[str, Any]]]]:
        """Orden: priorizados, Mi feed, cargados a mano y después las búsquedas en el orden del
        perfil (las que ya no están en el archivo, al final); en cada nivel clasificar `NUEVO` antes de analizar
        `OFERTA`, ordenando por email, rol y fecha descendente.
        Con `todo=False` (radar detenido) cuenta lo priorizado y los manuales.
        Con `analisis_pausado` no toma nada: la cosecha sigue guardando posts `NUEVO`."""
        if radar_store.get_radar_state().get("analisis_pausado"):
            return None
        corriendo = todo

        posicion = {q: i for i, q in enumerate(self._queries)}

        def nivel(p: Dict[str, Any]) -> int:
            if p.get("prioridad"):
                return 0
            if p.get("fuente") == "feed":
                return 1
            if p.get("fuente") == radar_store.FUENTE_MANUAL:
                return 2
            return 3 + posicion.get(p.get("query"), len(posicion))

        def disponible(p: Dict[str, Any]) -> bool:
            return (corriendo or bool(p.get("prioridad"))
                    or p.get("fuente") == radar_store.FUENTE_MANUAL) and p["id"] not in self._en_vuelo

        nuevos = [p for p in radar_store.list_posts(estado="NUEVO") if disponible(p)]
        ofertas = [p for p in radar_store.list_posts(estado="OFERTA") if disponible(p)]
        por_id = {p["id"]: p for p in nuevos}
        # Mitades pendientes: solo sus posts que siguen NUEVO (el usuario pudo cambiarlos)
        self._mitades = [m for m in ([i for i in ids if i in por_id] for ids in self._mitades) if m]
        en_mitades = {i for ids in self._mitades for i in ids}
        niveles = sorted({nivel(p) for p in nuevos} | {nivel(p) for p in ofertas})
        for n in niveles:
            mitad = next((ids for ids in self._mitades if nivel(por_id[ids[0]]) == n), None)
            if mitad is not None:
                self._mitades.remove(mitad)
                return "clasificar", [por_id[i] for i in mitad]
            lote = sorted(
                (p for p in nuevos if nivel(p) == n and p["id"] not in en_mitades), key=clave_analisis
            )[:LOTE_TRIAGE]
            if lote:
                return "clasificar", lote

            pendientes = sorted((p for p in ofertas if nivel(p) == n), key=clave_analisis)
            if pendientes:
                return "analizar", pendientes[:1]
        return None

    async def _descartar_exterior(self, post: Dict[str, Any]) -> bool:
        """Aplica el prefiltro solo cuando el llamador ya completó la lectura necesaria."""
        if not self._sigue_en(post["id"], "NUEVO"):
            return True  # El usuario pudo cambiarlo mientras se leía la imagen.
        exterior = prefiltro_exterior(post.get("texto") or "")
        if not exterior:
            return False
        pais, motivo = exterior
        radar_store.update_post(post["id"], estado="DESCARTADO", triage={
            "alcance": ALCANCE_EXTERIOR, "pais": pais, "motivo_descarte": "prefiltro",
            "alcance_motivo": f"Descartado sin IA: {motivo}"})
        await self._emitir_post(post["id"])
        return True

    @staticmethod
    def _preparar_contenido(post: Dict[str, Any]) -> Dict[str, Any]:
        """Combina texto y conserva las sugerencias de contacto para todos los consumidores."""
        preparado = dict(post, texto=texto_capturado(post),
                         _texto_original=post.get("_texto_original", post.get("texto")) or "")
        if (post.get("texto_imagen") or "").strip():
            texto = preparado["texto"]
            preparado["emails"] = list(dict.fromkeys([*(post.get("emails") or []), *extract_emails(texto)]))
            preparado["links"] = extract_links(texto, post.get("links") or [])
            preparado["whatsapp"] = list(dict.fromkeys([
                *(post.get("whatsapp") or []), *extract_whatsapp(texto, post.get("links") or []),
            ]))
            cambios = {k: preparado[k] for k in ("emails", "links", "whatsapp")
                       if preparado[k] != (post.get(k) or [])}
            if cambios:
                # Son sugerencias extraídas, no un destino elegido ni una aprobación.
                radar_store.update_post(post["id"], **cambios)
                preparado = dict(radar_store.get_post(post["id"]) or preparado,
                                 texto=preparado["texto"], _texto_original=preparado["_texto_original"])
        return preparado

    async def _preparar_lectura(self, post: Dict[str, Any]) -> Dict[str, Any]:
        """Lee una vez por intento y devuelve una copia enriquecida, sin pisar el original."""
        post_id = post["id"]
        post = radar_store.get_post(post_id) or post
        lectura = dict(post, triage=post.get("triage") if isinstance(post.get("triage"), dict) else {})
        if post_id not in self._lecturas_fallidas and necesita_lectura(lectura):
            try:
                texto = await self._image_reader_fn(post["imagenes"])
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(f"[Radar] No se pudo leer la imagen del post {post_id}")
                texto = ""
            if texto and texto.strip():
                radar_store.update_post(post_id, texto_imagen=texto)
                post = radar_store.get_post(post_id) or post
            else:
                self._lecturas_fallidas.add(post_id)
        return self._preparar_contenido(post)

    async def _clasificar(self, lote: List[Dict[str, Any]]) -> None:
        """Una llamada al LLM por lote. Si falla, se parte en mitades que vuelven a la cola (nunca post
        por post de entrada: un post problemático cuesta log2(lote) llamadas, no una por post); así lo
        priorizado y Mi feed no esperan detrás de los reintentos."""
        preparados = []
        for post in lote:
            if post.get("fuente") == radar_store.FUENTE_MANUAL and es_solo_url(post.get("texto")):
                try:
                    if self._ingest_fn is None:
                        raise RuntimeError("sin lector de páginas")
                    contenido = await self._ingest_fn(post)
                    if not (contenido.get("texto") or "").strip() and not contenido.get("imagenes"):
                        raise RuntimeError("página sin contenido")
                    radar_store.update_post(post["id"], **{
                        k: contenido[k] for k in ("texto", "autor", "cargo_autor", "emails", "links", "whatsapp", "imagenes")
                        if k in contenido
                    })
                    post = radar_store.get_post(post["id"])
                except OfertaInexistente as e:
                    # El aviso ya no existe: reintentar no sirve (oferta 7764)
                    radar_store.update_post(post["id"], estado="ERROR", intentos=MAX_INTENTOS_TRIAGE, error=str(e))
                    await self._emitir_post(post["id"])
                    continue
                except Exception:
                    await self._fallo_triage(post, "No se pudo leer la página de la oferta: pegá el texto")
                    continue
            elif (self._lee_pagina_aviso(post.get("fuente"))
                    and not _RE_LETRA.search(post.get("texto_pagina") or "")):
                leido = await self._leer_pagina_de_portal(post)
                if leido is None:
                    await self._fallo_triage(post, "No se pudo leer el aviso completo en el portal")
                    continue
                post = leido
            preparado = await self._preparar_lectura(post)
            if preparado.get("fuente") != radar_store.FUENTE_MANUAL and await self._descartar_exterior(preparado):
                continue
            preparados.append(preparado)
        lote = preparados
        if not lote:
            return
        try:
            resultados = await self._triage_fn(lote)
        except Exception as e:
            logger.warning(f"[Radar] Falló la clasificación de un lote de {len(lote)}: {e}")
            if len(lote) == 1:
                await self._fallo_triage(lote[0], str(e))
                return
            mitad = len(lote) // 2
            self._mitades.append([p["id"] for p in lote[:mitad]])
            self._mitades.append([p["id"] for p in lote[mitad:]])
            return
        for post in lote:
            resultado = (resultados or {}).get(post["id"])
            if resultado is None:
                await self._fallo_triage(post, "la clasificación no devolvió resultado para el post")
                continue
            await self._aplicar_clasificacion(post, resultado)
        await self._emitir_estado()

    async def _aplicar_clasificacion(self, post: Dict[str, Any], resultado: Dict[str, Any]) -> None:
        """No es oferta → se borra (se ignora). Exterior sin aceptar Argentina → `DESCARTADO`.
        El resto → `OFERTA` (se analiza después). Si el usuario lo cambió mientras tanto, no se toca."""
        post_id = post["id"]
        actual = radar_store.get_post(post_id)
        if actual is None or actual["estado"] != "NUEVO":
            return
        manual = actual.get("fuente") == radar_store.FUENTE_MANUAL
        if not manual and not resultado.get("es_busqueda_laboral"):
            if _RE_SENAL_BUSQUEDA.search(actual.get("texto") or ""):
                # Habla de una búsqueda: puede ser una oferta mal clasificada. Se descarta (recuperable
                # con «Deshacer») en vez de borrarse (se borraron ofertas reales).
                radar_store.update_post(
                    post_id, estado="DESCARTADO", intentos=0, error=None,
                    triage={**dict(resultado), "motivo": MOTIVO_NO_OFERTA},
                )
                await self._emitir_post(post_id)
                return
            radar_store.delete_post(post_id)
            await self._emitir_borrado(post_id)
            return
        alcance = resultado.get("alcance") or ALCANCE_DESCONOCIDO
        estado = "DESCARTADO" if not manual and alcance == ALCANCE_EXTERIOR else "OFERTA"
        triage = dict(resultado)
        if estado == "OFERTA" and not manual:
            # La misma oferta republicada por el mismo autor no se analiza otra vez (se recupera con «Deshacer»)
            original = radar_store.republicacion_de(actual, resultado.get("puesto"))
            if original is not None:
                estado = "DESCARTADO"
                triage.update(motivo=MOTIVO_REPUBLICADA, republicacion_de=original)
        radar_store.update_post(
            post_id,
            triage=triage,
            canal=str(resultado.get("canal") or ""),
            destino_canal=str(resultado.get("destino_canal") or ""),
            estado=estado,
            intentos=0,
            error=None,
        )
        if estado == "OFERTA":
            if not manual:
                radar_store.bump_counter("match")
            self._proc_event.set()
        await self._emitir_post(post_id)

    async def _analizar(self, post: Dict[str, Any]) -> None:
        """`OFERTA` → `ANALIZANDO` → `ANALIZADA` con el análisis del Estratega. Un fallo vuelve a
        `OFERTA` sumando `intentos`; al 3º queda en `ERROR` (el botón Analizar lo reintenta)."""
        post_id = post["id"]
        if not self._sigue_en(post_id, "OFERTA"):
            return
        radar_store.update_post(post_id, estado="ANALIZANDO")
        await self._emitir_post(post_id)
        if not self._sigue_en(post_id, "ANALIZANDO"):
            return
        try:
            preparado = await self._preparar_lectura(post)
            if not self._sigue_en(post_id, "ANALIZANDO"):
                return
            if self._prepare_fn is not None:
                preparado = await self._prepare_fn(preparado)
            if not self._sigue_en(post_id, "ANALIZANDO"):
                return
            if preparado.get("fuente") != radar_store.FUENTE_MANUAL and not aviso_util(preparado):
                error = "Captura incompleta: falta texto suficiente o un puesto detectado"
                radar_store.update_post(post_id, estado="ERROR", prioridad=0, error=error)
                await self._emitir_post(post_id)
                return
            analisis = await self._analyze_fn(preparado)
            if not isinstance(analisis, dict) or not analisis:
                raise ValueError("el análisis no devolvió datos")
        except Exception as e:
            logger.warning(f"[Radar] Falló el análisis del post {post_id}: {e}")
            _ticket("analisis_fallido", f"Falló el análisis del post {post_id}: {e}", excepcion=e,
                    datos={"post_id": post_id})
            if not self._sigue_en(post_id, "ANALIZANDO"):
                return
            intentos = int(post.get("intentos") or 0) + 1
            error = f"Falló el análisis ({intentos}/{MAX_INTENTOS_ANALISIS}): {e}"
            if intentos >= MAX_INTENTOS_ANALISIS:
                radar_store.update_post(post_id, estado="ERROR", prioridad=0, intentos=intentos, error=error)
            else:
                radar_store.update_post(post_id, estado="OFERTA", intentos=intentos, error=error)
            await self._emitir_post(post_id)
            return
        if not self._sigue_en(post_id, "ANALIZANDO"):
            return
        if analisis.get("es_oferta") is False:
            radar_store.update_post(
                post_id,
                analisis=analisis,
                estado="IGNORADA",
                prioridad=0,
                intentos=0,
                error="no es una oferta",
            )
            await self._emitir_post(post_id)
            return

        # En inglés y sin señal de que acepte candidatos de Argentina: no apta (la carga manual queda marcada)
        descartes.aplicar_condicion_ubicacion(
            analisis, preparado.get("texto") or "", descartes.cargar_restricciones(),
            manual=preparado.get("fuente") == radar_store.FUENTE_MANUAL, contacto_local=_senal_argentina(preparado))
        estado = "ANALIZADA"
        if (analisis.get("no_apto") or {}).get("motivos") and not radar_store.get_post(post_id).get("aprobado_at"):
            analisis["no_apto"]["desde"] = "ANALIZADA"
            estado = "IGNORADA"
        try:
            await escribir_post_con_espera(
                post_id, analisis=analisis, estado=estado, prioridad=0, intentos=0, error=None,
            )
        except sqlite3.OperationalError as e:
            await self._devolver_a_oferta_sin_guardar(post_id, e)
            return
        await self._emitir_post(post_id)
        await self.generar_automaticos([post_id])

    async def _devolver_a_oferta_sin_guardar(self, post_id: int, error: Exception) -> None:
        """No se pudo guardar el análisis: el post vuelve a la cola de ofertas (se analiza de nuevo) en vez
        de quedar en ANALIZANDO con el análisis perdido."""
        logger.error(f"[Radar] No se pudo guardar el análisis del post {post_id}: {error}")
        _ticket("guardado_fallido", f"No se pudo guardar el análisis del post {post_id}: {error}", excepcion=error,
                datos={"post_id": post_id})
        try:
            radar_store.update_post(post_id, estado="OFERTA", prioridad=0,
                                    error=f"No se pudo guardar el análisis ({error}): se vuelve a analizar")
        except sqlite3.OperationalError:
            # Ni eso: la base sigue bloqueada. El arranque (`reconcile_on_startup`) y el stop lo devuelven.
            logger.exception(f"[Radar] El post {post_id} quedó en ANALIZANDO: la base sigue bloqueada")
            return
        await self._emitir_post(post_id)

    # ------------------------------------------------------------------
    # Rescate de «Captura incompleta»
    # ------------------------------------------------------------------

    async def _rescate_si_toca(self) -> None:
        """Un barrido cada `RESCATE_CADA_S` mientras el Radar corre y el análisis no está en pausa."""
        ahora = self._reloj()
        if self._ultimo_rescate is not None and ahora - self._ultimo_rescate < RESCATE_CADA_S:
            return
        if not self.is_running or radar_store.get_radar_state().get("analisis_pausado"):
            return
        self._ultimo_rescate = ahora
        await self.rescatar_capturas()

    async def reintentar_generacion_fallida(self) -> int:
        """Devuelve a GENERANDO los posts en ERROR por una generación que falló por AGY, pasados
        `ESPERA_REINTENTO_GENERACION_MIN` y hasta `MAX_REINTENTOS_GENERACION_AUTO` veces por post. Nada con la generación
        en pausa, sin cuota de AGY o con AGY saturado. Devuelve cuántos volvieron."""
        estado = radar_store.get_radar_state()
        if estado.get("generacion_pausada") or self._cuota().get("agotada"):
            return 0
        hasta_serie = estado.get("agy_serie_hasta")
        if hasta_serie is not None and float(hasta_serie) > time.time():
            return 0
        corte = self._ahora_utc() - timedelta(minutes=ESPERA_REINTENTO_GENERACION_MIN)
        vueltos = 0
        for post in radar_store.candidatos_reintento_generacion(MAX_REINTENTOS_GENERACION_AUTO,
                                                                 corte.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]):
            if post["id"] in self.generando_ids or colas.nivel(post) is None:
                continue
            vuelta = int(post.get("reintentos_gen_auto") or 0) + 1
            radar_store.update_post(
                post["id"], estado="GENERANDO", intentos=0, reintentos_gen_auto=vuelta,
                error=f"Reintento automático {vuelta}/{MAX_REINTENTOS_GENERACION_AUTO} tras: {post.get('error')}")
            await self._emitir_post(post["id"])
            vueltos += 1
        if vueltos:
            logger.info(f"[Radar] {vueltos} generaciones fallidas por AGY vuelven a la cola")
            await self.wake_generation()
        return vueltos

    async def reintentar_analisis_fallidos(self) -> int:
        """Devuelve a OFERTA los posts en ERROR por un análisis que falló por la IA, pasados
        `ESPERA_REINTENTO_AUTOMATICO_MIN` y hasta `MAX_REINTENTOS_AUTOMATICOS` veces por post. Nada con el
        análisis en pausa, sin cuota de AGY o con AGY saturado. Devuelve cuántos volvieron."""
        estado = radar_store.get_radar_state()
        if estado.get("analisis_pausado") or self._cuota().get("agotada"):
            return 0
        hasta_serie = estado.get("agy_serie_hasta")
        if hasta_serie is not None and float(hasta_serie) > time.time():
            return 0
        corte = self._ahora_utc() - timedelta(minutes=ESPERA_REINTENTO_AUTOMATICO_MIN)
        vueltos = 0
        for post in radar_store.candidatos_reintento_analisis(MAX_REINTENTOS_AUTOMATICOS,
                                                               corte.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]):
            if post["id"] in self.en_vuelo:
                continue
            vuelta = int(post.get("reintentos_auto") or 0) + 1
            radar_store.update_post(
                post["id"], estado="OFERTA", intentos=0, reintentos_auto=vuelta,
                error=f"Reintento automático {vuelta}/{MAX_REINTENTOS_AUTOMATICOS} tras: {post.get('error')}")
            await self._emitir_post(post["id"])
            vueltos += 1
        if vueltos:
            logger.info(f"[Radar] {vueltos} análisis fallidos por la IA vuelven a la cola")
            self._wake_processing()
        return vueltos

    async def rescatar_capturas(self, maximo: int = RESCATE_POR_BARRIDO) -> Dict[str, int]:
        """Revisa hasta `maximo` ofertas en «Captura incompleta»: relee publicación, imagen y links;
        si con eso es un aviso útil vuelve a `OFERTA` (análisis → CV y mail), si no queda en `ERROR`
        con lo intentado. Con `analisis_pausado` no hace nada."""
        resultado = {"revisados": 0, "rescatados": 0, "siguen_incompletos": 0}
        if radar_store.get_radar_state().get("analisis_pausado"):
            return resultado
        for post in radar_store.candidatos_rescate(RESCATE_MAX_INTENTOS, maximo):
            if post["id"] in self._rescatando or post["id"] in self.en_vuelo:
                continue
            self._rescatando.add(post["id"])
            try:
                rescatado = await self._rescatar(post)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(f"[Radar] Falló el rescate del post {post['id']}")
                rescatado = None
            finally:
                self._rescatando.discard(post["id"])
            if rescatado is None:
                continue
            resultado["revisados"] += 1
            resultado["rescatados" if rescatado else "siguen_incompletos"] += 1
        if resultado["rescatados"]:
            self._wake_processing()
        return resultado

    @staticmethod
    def _sigue_incompleto(post_id: int) -> bool:
        actual = radar_store.get_post(post_id)
        return bool(actual and actual.get("estado") == "ERROR"
                    and str(actual.get("error") or "").startswith(radar_store.PREFIJO_CAPTURA_INCOMPLETA))

    async def _recapturar(self, post: Dict[str, Any], intentado: List[str]) -> None:
        """Vuelve a abrir la publicación en LinkedIn si no hay imagen o el texto quedó cortado."""
        texto = post.get("texto") or ""
        if (post.get("imagenes") and not _RE_TEXTO_CORTADO.search(texto)) or not post.get("post_url"):
            return
        recapturar = getattr(self._get_harvester(), "recapturar_post", None)
        if recapturar is None:
            return
        try:
            nuevo = await recapturar(post["post_url"])
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"[Radar] No se pudo releer la publicación del post {post['id']}: {e}")
            nuevo = None
        if not nuevo:
            intentado.append("no se pudo releer la publicación")
            return
        cambios: Dict[str, Any] = {}
        texto_nuevo = (nuevo.get("texto") or "").strip()
        if len(texto_nuevo) > len(texto.strip()):
            cambios["texto"] = texto_nuevo
        for campo, tope in (("imagenes", 4), ("links", None), ("emails", None), ("whatsapp", None)):
            previos = list(post.get(campo) or [])
            unidos = list(dict.fromkeys([*previos, *(nuevo.get(campo) or [])]))
            unidos = unidos[:tope] if tope else unidos
            if unidos != previos:
                cambios[campo] = unidos
        if cambios and self._sigue_incompleto(post["id"]):
            radar_store.update_post(post["id"], **cambios)
        intentado.append("publicación releída" if cambios else "publicación releída sin datos nuevos")

    async def _rescatar(self, post: Dict[str, Any]) -> Optional[bool]:
        """True: rescatada (vuelve a `OFERTA`); False: sigue incompleta; None: el post cambió en el medio."""
        post_id = post["id"]
        intentado: List[str] = []
        await self._recapturar(post, intentado)
        if not self._sigue_incompleto(post_id):
            return None
        post = radar_store.get_post(post_id) or post
        if post.get("imagenes") and not (post.get("texto_imagen") or "").strip():
            # El rescate vuelve a intentar aunque la lectura automática ya haya fallado
            self._lecturas_fallidas.discard(post_id)
        preparado = await self._preparar_lectura(post)
        if post.get("imagenes"):
            intentado.append("imagen leída" if (preparado.get("texto_imagen") or "").strip() else "imagen ilegible")
        else:
            intentado.append("sin imagen")
        if self._prepare_fn is not None:
            # Los links se vuelven a leer aunque el análisis ya lo haya intentado
            preparado = await self._prepare_fn(dict(preparado, intentos=0))
        tenia_links = bool(preparado.get("links") or post.get("links"))
        if (preparado.get("texto_pagina") or "").strip():
            intentado.append("página leída")
        else:
            intentado.append("sin página legible" if tenia_links else "sin links")
        if not self._sigue_incompleto(post_id):
            return None
        actual = radar_store.get_post(post_id) or post
        intentos = int(actual.get("rescate_intentos") or 0) + 1
        ahora = self._now().strftime("%Y-%m-%d %H:%M:%S")
        if aviso_util(preparado):
            radar_store.update_post(post_id, estado="OFERTA", intentos=0, error=None, prioridad=0,
                                    rescate_intentos=intentos, rescate_at=ahora)
            logger.info(f"[Radar] Rescate del post {post_id}: rescatada → análisis ({', '.join(intentado)})")
            await self._emitir_post(post_id)
            return True
        if not puesto_util(preparado):
            intentado.append("sin puesto detectado")
        if not tiene_email(preparado) and not extract_emails(texto_capturado(preparado)):
            intentado.append("sin email")
        cuando = self._now().strftime("%d/%m %H:%M")
        error = f"{radar_store.PREFIJO_CAPTURA_INCOMPLETA} (revisada {cuando}): {', '.join(intentado)}"
        radar_store.update_post(post_id, error=error, rescate_intentos=intentos, rescate_at=ahora)
        logger.info(f"[Radar] Rescate del post {post_id}: sigue incompleta ({', '.join(intentado)})")
        await self._emitir_post(post_id)
        return False

    @staticmethod
    def _sigue_en(post_id: int, estado: str) -> bool:
        actual = radar_store.get_post(post_id)
        return actual is not None and actual["estado"] == estado

    async def _fallo_triage(self, post: Dict[str, Any], motivo: str) -> None:
        """El post sigue `NUEVO` para reintentar; al 3er fallo consecutivo pasa a `ERROR`."""
        post = radar_store.get_post(post["id"]) or post
        intentos = int(post.get("intentos") or 0) + 1
        error = f"Triage falló ({intentos}/{MAX_INTENTOS_TRIAGE}): {motivo}"
        if intentos >= MAX_INTENTOS_TRIAGE:
            radar_store.update_post(post["id"], estado="ERROR", intentos=intentos, error=error)
            await self._emitir_post(post["id"])
        else:
            radar_store.update_post(post["id"], intentos=intentos, error=error)

    # ------------------------------------------------------------------
    # Pausas y esperas
    # ------------------------------------------------------------------

    def _segundos_pausa(self) -> float:
        """La pausa entre vueltas del perfil. Sin reglas válidas, INTERVALO_REGLAS_S para volver a mirarlas."""
        reglas = self._reglas_vigentes()
        if reglas is None:
            return float(INTERVALO_REGLAS_S)
        return self._rng.uniform(*reglas.pausa_entre_vueltas_s)

    async def _entrar_en_pausa(self, segundos: float) -> None:
        proximo = (self._now() + timedelta(seconds=segundos)).strftime(_FMT_FECHA)
        if self._errores_ciclo:
            # El error del ciclo queda visible hasta el próximo intento
            await self._set_estado("ERROR", " | ".join(self._errores_ciclo), next_cycle_at=proximo)
            return
        detalle = ""
        await self._set_estado("PAUSA", detalle, next_cycle_at=proximo)

    async def _wait(self, segundos: float, evento: Optional[asyncio.Event] = None) -> bool:
        """Espera interrumpible por `evento` (por defecto el de `stop()`). False si se interrumpió.

        El worker de envío pasa `_send_stop_event`: `stop()` no corta sus esperas (lo aprobado
        sale igual, con la separación de 30–90 s); solo el cierre del servidor las corta.
        """
        if evento is None:
            evento = self._stop_event
        if evento.is_set():
            return False
        espera = asyncio.ensure_future(self._sleep(segundos))
        parada = asyncio.ensure_future(evento.wait())
        try:
            await asyncio.wait({espera, parada}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in (espera, parada):
                if not t.done():
                    t.cancel()
            await asyncio.gather(espera, parada, return_exceptions=True)
        return not evento.is_set()

    # ------------------------------------------------------------------
    # Worker de generación
    # ------------------------------------------------------------------

    def _ensure_generation_worker(self) -> None:
        if self._cerrando or self._apagado:
            # Durante un stop no se crean workers: los GENERANDO se retoman en el próximo start/wake
            return
        if self._gen_task is None or self._gen_task.done():
            self._gen_task = asyncio.create_task(self._generation_worker(), name="radar-generation")
            self._gen_task.add_done_callback(self._supervisar_worker_generacion)

    def _supervisar_worker_generacion(self, task: asyncio.Task) -> None:
        """Si el worker de generación murió por un error inesperado (ya registrado), se relanza a los
        `REINICIO_WORKER_S` segundos: la cola no queda parada hasta el próximo wake o ciclo."""
        if task.cancelled() or task.exception() is None:
            return
        if self._cerrando or self._apagado or task is not self._gen_task:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._reinicio_gen is not None:
            self._reinicio_gen.cancel()
        self._reinicio_gen = loop.call_later(REINICIO_WORKER_S, self._relanzar_worker_generacion)

    def _relanzar_worker_generacion(self) -> None:
        self._reinicio_gen = None
        self._ensure_generation_worker()

    def _siguiente_generacion(self) -> Optional[dict]:
        estado = radar_store.get_radar_state()
        # La pausa frena solo lo automático: lo pedido a mano (`gen_manual`) y toda oferta
        # cargada a mano (fuente manual, aunque haya entrado antes de marcarse: post 7657) corren igual.
        pausada = bool(estado.get("generacion_pausada"))
        # 1) Lo ya tomado (forzadas, regeneraciones, una generación cortada por un reinicio).
        for post in orden_generacion(radar_store.list_posts(estado="GENERANDO")):
            manual = post.get("gen_manual") or post.get("fuente") == radar_store.FUENTE_MANUAL
            if post["id"] not in self._reservas_gen and (not pausada or manual):
                return post
        if pausada:
            return None  # conserva GENERANDO y las colas hasta la reanudación
        # 2) Colas dinámicas: la mejor candidata en este momento (core.colas); la de fondo solo si no
        #    hay ninguna prioritaria y está activa.
        armadas = colas.armar_colas(radar_store.candidatas_cola())
        niveles = [colas.PRIORITARIA] + ([colas.FONDO] if estado.get("backlog_activo") else [])
        for tier in niveles:
            for post in armadas[tier]:
                if post["id"] in self._reservas_gen:
                    continue
                if radar_store.tomar_candidata(post["id"], tier):
                    return radar_store.get_post(post["id"])
        return None

    async def _generation_worker(self) -> None:
        """Supervisa hasta dos generaciones con reservas exclusivas y plazos propios.

        Los wakes y las terminaciones despiertan el mismo supervisor. Un fallo inesperado
        drena todas las hijas antes del relanzamiento; la pausa deja terminar las activas.
        """
        try:
            while True:
                # Se limpia antes de listar: un wake posterior no se pierde
                self._generation_event.clear()
                self._generation_change.clear()
                for post_id, vigilante in list(self._vigilantes_gen.items()):
                    if vigilante.done():
                        del self._vigilantes_gen[post_id]
                        self._reservas_gen.discard(post_id)
                        if not vigilante.cancelled():
                            vigilante.result()  # propaga fallos inesperados para relanzar el supervisor
                while len(self._reservas_gen) < self._generation_workers:
                    elegido = self._siguiente_generacion()
                    if elegido is None:
                        break
                    post_id = elegido["id"]
                    self._reservas_gen.add(post_id)  # antes de crear la hija y de cualquier await
                    vigilante = asyncio.create_task(
                        self._vigilar_generacion(elegido), name=f"radar-vigilar-{post_id}"
                    )
                    self._vigilantes_gen[post_id] = vigilante
                    vigilante.add_done_callback(lambda tarea: self._generation_change.set())
                if not self._vigilantes_gen:
                    if not self.is_running and not radar_store.get_radar_state().get("generacion_pausada"):
                        return  # radar detenido y sin trabajo: termina (wake_generation lo relanza)
                    await self._generation_event.wait()
                else:
                    # La espera de trabajo sigue significando que no hay generaciones activas.
                    await self._generation_change.wait()
        except Exception:
            # p. ej. la DB falló al marcar ERROR: se corta para no girar en falso; el supervisor lo relanza
            logger.exception("[Radar] Error inesperado en el worker de generación")
            raise
        finally:
            # CancelledError deriva de BaseException: también pasa por este drenaje.
            vigilantes = list(self._vigilantes_gen.values())
            for vigilante in vigilantes:
                if not vigilante.done():
                    vigilante.cancel()
            await asyncio.gather(*vigilantes, return_exceptions=True)
            for post_id in self._reservas_gen:
                self._etapas.pop(post_id, None)
                self._progresos.pop(post_id, None)
                self._trabajos_gen.pop(post_id, None)
            self._vigilantes_gen.clear()
            self._reservas_gen.clear()

    async def _vigilar_generacion(self, post: Dict[str, Any]) -> None:
        """Corre `_generar_post` como tarea hija y le controla el plazo: cada `_tick_plazo_s` suma el
        tiempo de trabajo efectivo (no mientras AGY espera cuota: eso se marca en el progreso) y, al
        pasar `_plazo_trabajo_s`, la cancela (un reintento; al segundo vencimiento, `ERROR_PLAZO`).

        Si el worker se cancela (stop) cancela y espera a la hija: el post sigue GENERANDO, sin
        progreso (vuelve a la cola). `cancelar_generacion` corta solo la hija."""
        post_id = post["id"]
        ultimo = self._reloj()
        self._progresos[post_id] = progreso.nuevo(self._now())
        trabajo = asyncio.create_task(self._generar_post(post), name=f"radar-cv-{post_id}")
        self._trabajos_gen[post_id] = trabajo
        activo = 0.0
        esperando = False
        renueva = None
        vencido = False
        try:
            try:
                while not trabajo.done():
                    await asyncio.wait({trabajo}, timeout=self._tick_plazo_s)
                    ahora = self._reloj()
                    transcurrido, ultimo = max(0.0, ahora - ultimo), ahora
                    cuota = self._estado_cuota()
                    agotada = bool(cuota.get("agotada"))
                    if trabajo.done():
                        if not agotada:
                            self._sumar_activo(post_id, transcurrido)
                        break
                    nueva_renovacion = cuota.get("renueva") if agotada else None
                    if (agotada, nueva_renovacion) != (esperando, renueva):
                        esperando, renueva = agotada, nueva_renovacion
                        await self._marcar_cuota(post_id, agotada, renueva)
                    if agotada:
                        continue  # la espera de cuota no cuenta para el plazo
                    activo += transcurrido
                    self._sumar_activo(post_id, transcurrido)
                    if activo >= self._plazo_trabajo_s and not trabajo.done():
                        vencido = True
                        trabajo.cancel()
                        await asyncio.wait({trabajo})
            except asyncio.CancelledError:
                trabajo.cancel()
                await asyncio.wait({trabajo})
                _excepcion_de(trabajo)
                prog = self._progresos.get(post_id)
                if prog is None or prog.get("fin") is None:
                    # El cierre puede llegar durante el último broadcast, con el CV ya listo.
                    self._guardar_progreso(post_id, None)
                raise
            excepcion = _excepcion_de(trabajo)
            if vencido:
                await self._vencer_plazo(post_id)
            elif trabajo.cancelled():
                await self._terminar_progreso(post_id, "cancelado")  # cancelar_generacion
            elif excepcion is not None:
                # Error inesperado de la hija (p. ej. la DB al marcar ERROR): sin progreso viejo, y el
                # worker se corta (el supervisor lo relanza)
                self._guardar_progreso(post_id, None)
                raise excepcion
        finally:
            # También drena la hija si falla el propio vigilante (DB/progreso/reloj).
            if not trabajo.done():
                trabajo.cancel()
                await asyncio.gather(trabajo, return_exceptions=True)
                prog = self._progresos.get(post_id)
                if prog is None or prog.get("fin") is None:
                    self._guardar_progreso(post_id, None)
            else:
                _excepcion_de(trabajo)
            self._trabajos_gen.pop(post_id, None)
            self._progresos.pop(post_id, None)
            self._etapas.pop(post_id, None)

    async def _vencer_plazo(self, post_id: int) -> None:
        """El CV superó el plazo de trabajo: suma un intento y vuelve a la cola; al 2º, `ERROR`."""
        await self._terminar_progreso(post_id, "vencido")
        actual = radar_store.get_post(post_id)
        if actual is None or actual["estado"] != "GENERANDO":
            return  # el usuario lo cambió mientras tanto
        intentos = int(actual.get("intentos") or 0) + 1
        minutos = max(1, round(self._plazo_trabajo_s / 60))
        if intentos < MAX_INTENTOS_GENERACION:
            logger.warning(f"[Radar] El CV del post {post_id} superó {minutos} min de trabajo: se reintenta")
            radar_store.update_post(post_id, intentos=intentos, error=AVISO_PLAZO.format(minutos=minutos))
        else:
            logger.warning(f"[Radar] El CV del post {post_id} superó {minutos} min de trabajo dos veces: ERROR")
            radar_store.update_post(post_id, estado="ERROR", intentos=intentos,
                                    error=ERROR_PLAZO.format(minutos=minutos))
        await self._emitir_post(post_id)

    async def cancelar_generacion(self, post_id: int) -> bool:
        """Corta el CV que se está generando para `post_id` (p. ej. el usuario lo descartó) y la cola
        sigue con el próximo. False si ese post no se está generando ahora (en cola, ya terminado u
        otro post): no toca nada."""
        vigilante = self._vigilantes_gen.get(post_id)
        trabajo = self._trabajos_gen.get(post_id)
        if vigilante is None or vigilante.done() or (trabajo is not None and trabajo.done()):
            return False
        logger.info(f"[Radar] Se corta la generación del post {post_id}")
        if trabajo is None:
            # Reservado, pero el vigilante todavía no tuvo su primera vuelta.
            vigilante.cancel()
        else:
            trabajo.cancel()
        await asyncio.gather(vigilante, return_exceptions=True)
        return True

    def _estado_cuota(self) -> Dict[str, Any]:
        try:
            estado = self._cuota()
        except Exception:
            logger.exception("[Radar] No se pudo leer el estado de la cuota de AGY")
            return {}
        return estado if isinstance(estado, dict) else {}

    async def _generar_post(self, post: Dict[str, Any]) -> None:
        """CV (si hace falta) + mensaje → `LISTO`. Cualquier fallo → `ERROR` con el motivo (suma un
        intento). Cada paso (`core.progreso.reportar`, también los del pipeline) se guarda en el post
        y se emite como `radar_progreso`."""
        with carril_agy("generacion"):
            return await self._generar_post_impl(post)

    async def _generar_post_impl(self, post: Dict[str, Any]) -> None:
        post_id = post["id"]
        postulacion_id = post.get("postulacion_id")
        preparado = self._preparar_contenido(post)

        async def reportero(paso: str, sub: Optional[str] = None) -> None:
            await self._avanzar_progreso(post_id, paso, sub)

        with progreso.usar_reportero(reportero):
            await progreso.reportar("preparar")
            try:
                if not postulacion_id:
                    postulacion_id = int(await self._pipeline_fn(dict(preparado)))
                    actual = radar_store.get_post(post_id)
                    if (actual is None or actual["estado"] != "GENERANDO"
                            or actual.get("postulacion_id") not in (None, postulacion_id)):
                        await self._terminar_progreso(post_id, "cancelado")
                        return  # no restaurar un vínculo o estado cambiado durante el pipeline
                    if actual.get("postulacion_id") != postulacion_id:
                        # Los pipelines heredados no guardan el vínculo. El productivo ya lo
                        # hizo: repetir la escritura invalidaría la versión de su borrador.
                        if (actual["updated_at"] != preparado["updated_at"]
                                or not radar_store.update_post(
                                    post_id, postulacion_id=postulacion_id,
                                    updated_at_esperado=actual["updated_at"])):
                            await self._terminar_progreso(post_id, "cancelado")
                            return
                actual = radar_store.get_post(post_id)
                if (actual is None or actual["estado"] != "GENERANDO"
                        or actual.get("postulacion_id") != postulacion_id):
                    await self._terminar_progreso(post_id, "cancelado")
                    return
                version = actual["updated_at"]
                await progreso.reportar("mensaje")
                email = await self._compose_fn(preparado, postulacion_id)
                to, subject, body = self._validar_email(
                    email, requiere_asunto=radar_store.es_canal_email(preparado)
                )
            except Exception as e:
                logger.warning(f"[Radar] Falló la generación del post {post_id}: {e}")
                _ticket("generacion_fallida", f"Falló la generación del post {post_id}: {e}", excepcion=e,
                        datos={"post_id": post_id})
                actual = radar_store.get_post(post_id)
                if actual is not None and actual["estado"] == "GENERANDO":
                    intentos = int(actual.get("intentos") or 0) + 1
                    motivo = ("Generación detenida por saturación AGY (503)" if es_saturacion_agy(e)
                              else "Falló la generación")
                    radar_store.update_post(post_id, estado="ERROR", intentos=intentos,
                                            error=f"{motivo}: {e}")
                    await self._terminar_progreso(post_id, "ERROR")
                    await self._emitir_post(post_id)
                else:
                    await self._terminar_progreso(post_id, "cancelado")
                return
        actual = radar_store.get_post(post_id)
        if (actual is None or actual["estado"] != "GENERANDO"
                or actual.get("postulacion_id") != postulacion_id or actual["updated_at"] != version):
            # El usuario lo cambió mientras se generaba (p. ej. lo descartó): no se pisa
            logger.info(f"[Radar] El post {post_id} dejó de estar en GENERANDO; se descarta el email")
            await self._terminar_progreso(post_id, "cancelado")
            return
        guardado = radar_store.update_post(
            post_id,
            updated_at_esperado=version,
            estado="LISTO",
            postulacion_id=postulacion_id,
            email_to=to,
            email_subject=subject,
            email_body=body,
            aprobado_at=None,  # contenido nuevo: requiere aprobación explícita
            intentos=0,
            error=None,
        )
        if not guardado:
            await self._terminar_progreso(post_id, "cancelado")
            return
        if post.get("fuente") != radar_store.FUENTE_MANUAL:
            radar_store.bump_counter("listos")
        await self._terminar_progreso(post_id, "LISTO")
        await self._emitir_post(post_id)
        await self._emitir_estado()

    # -------------------------------------------------------------- progreso del CV en curso

    async def _avanzar_progreso(self, post_id: int, paso: str, sub: Optional[str] = None) -> None:
        ahora = self._now()
        prog = progreso.avanzar(self._progresos.get(post_id) or progreso.nuevo(ahora), paso, ahora, sub)
        self._progresos[post_id] = prog
        self._etapas[post_id] = prog["etiqueta"]
        await self._publicar_progreso(post_id, prog)

    async def _marcar_cuota(self, post_id: int, esperando: bool, renueva: Optional[str]) -> None:
        prog = self._progresos.get(post_id)
        if prog is None:
            return
        prog = progreso.marcar_cuota(prog, esperando, renueva)
        self._progresos[post_id] = prog
        await self._publicar_progreso(post_id, prog)

    def _sumar_activo(self, post_id: int, segundos: float) -> None:
        """Guarda el tiempo también durante pasos largos, sin emitir un evento por cada tick."""
        prog = self._progresos.get(post_id)
        if prog is not None:
            actualizado = progreso.sumar_activo(prog, segundos)
            self._progresos[post_id] = actualizado
            if actualizado["activo_s"] != prog["activo_s"]:
                self._guardar_progreso(post_id, actualizado)

    async def _terminar_progreso(self, post_id: int, resultado: str) -> None:
        """Cierra el progreso del trabajo (idempotente: si ya terminó no hace nada)."""
        prog = self._progresos.get(post_id)
        if prog is None or prog.get("fin") is not None:
            return
        prog = progreso.terminar(prog, resultado, self._now())
        self._progresos[post_id] = prog
        await self._publicar_progreso(post_id, prog)

    async def _publicar_progreso(self, post_id: int, prog: Dict[str, Any]) -> None:
        self._guardar_progreso(post_id, prog)
        await self._emitir_progreso(post_id, prog)

    @staticmethod
    def _guardar_progreso(post_id: int, prog: Optional[Dict[str, Any]]) -> None:
        try:
            radar_store.guardar_progreso(post_id, prog)
        except Exception:
            logger.exception(f"[Radar] No se pudo guardar el progreso del post {post_id}")

    @staticmethod
    def _sigue_generando(post_id: int) -> bool:
        actual = radar_store.get_post(post_id)
        return actual is not None and actual["estado"] == "GENERANDO"

    @staticmethod
    def _validar_email(email: Any, requiere_asunto: bool = True) -> Tuple[str, str, str]:
        """Devuelve (to, subject, body); `ValueError` si falta el asunto (solo canal email) o el cuerpo."""
        if not isinstance(email, dict):
            raise ValueError("el composer no devolvió un email")
        subject = email.get("subject")
        body = email.get("body")
        if not requiere_asunto and subject is None:
            subject = ""
        if not isinstance(subject, str) or (requiere_asunto and not subject.strip()):
            raise ValueError("el composer devolvió un email sin asunto")
        if not isinstance(body, str) or not body.strip():
            raise ValueError("el composer devolvió un email sin cuerpo")
        return str(email.get("to") or "").strip(), subject.strip(), body.strip()

    # ------------------------------------------------------------------
    # Worker de envío
    # ------------------------------------------------------------------

    def _poner_en_cola(self, post_id: int) -> None:
        if post_id in self._en_cola:
            return
        self._en_cola.add(post_id)
        self._send_queue.put_nowait(post_id)

    def _encolar_aprobados(self) -> None:
        """Encola los `LISTO` con `aprobado_at` (más antiguos primero) y asegura el worker."""
        try:
            if not radar_store.get_radar_state()["envios_pausados"]:
                aprobados = [p for p in radar_store.list_posts(estado="LISTO") if p.get("aprobado_at")]
                for post in [p for p in aprobados if not radar_store.es_canal_email(p)]:
                    self._quitar_aprobacion_no_email(post)  # nunca salen por Gmail
                aprobados = [p for p in aprobados if radar_store.es_canal_email(p)]
                for post in sorted(aprobados, key=lambda p: (str(p["aprobado_at"]), p["id"])):
                    self._poner_en_cola(post["id"])
        except Exception:
            logger.exception("[Radar] No se pudieron reencolar los envíos aprobados")
        self._ensure_send_worker()

    @staticmethod
    def _quitar_aprobacion_no_email(post: Dict[str, Any]) -> None:
        """Un post de canal no email no se envía por Gmail: se le saca la aprobación (sigue LISTO)."""
        radar_store.update_post(
            post["id"], aprobado_at=None, error=f"Canal {post.get('canal')}: usá «Ya me postulé»",
        )

    def _ensure_send_worker(self) -> None:
        if self._cerrando or self._apagado:
            return
        if self._send_task is not None and not self._send_task.done():
            return
        if self._send_queue.empty() and not self.is_running:
            return
        self._send_stop_event = asyncio.Event()
        self._send_task = asyncio.create_task(self._send_worker(), name="radar-send")

    async def _send_worker(self) -> None:
        """Toma ids de la cola y los envía de a uno, separados 30–90 s.

        Con el radar detenido termina cuando la cola queda vacía; con el radar corriendo espera
        nuevos ids (ese es el único punto donde `stop()` lo cancela: nunca a mitad de un envío).
        """
        while not self._send_stop_event.is_set():
            if self._send_queue.empty():
                if not self.is_running:
                    return
                self._send_idle = True
                try:
                    post_id = await self._send_queue.get()
                finally:
                    self._send_idle = False
            else:
                post_id = self._send_queue.get_nowait()
            try:
                enviado = await self._procesar_envio(post_id)
            except Exception:
                logger.exception(f"[Radar] Error inesperado enviando el post {post_id}")
                enviado = False
            finally:
                self._en_cola.discard(post_id)
            if enviado and not await self._wait(
                self._rng.uniform(*PAUSA_ENTRE_ENVIOS), self._send_stop_event
            ):
                return

    async def _procesar_envio(self, post_id: int) -> bool:
        """Envía un post aprobado. Devuelve True si salió (para espaciar el siguiente)."""
        # Relectura justo antes de enviar; sin `await` entre esta verificación y marcar ENVIANDO
        post = radar_store.get_post(post_id)
        if post is None or post["estado"] != "LISTO" or not post.get("aprobado_at"):
            logger.info(f"[Radar] El post {post_id} ya no está LISTO y aprobado; no se envía")
            return False
        if not radar_store.es_canal_email(post):
            logger.warning(f"[Radar] El post {post_id} es de canal {post.get('canal')}: no se envía por Gmail")
            self._quitar_aprobacion_no_email(post)
            await self._emitir_post(post_id)
            return False
        estado = radar_store.get_radar_state()  # aplica el reinicio diario de contadores
        if estado["envios_pausados"]:
            logger.info(f"[Radar] Envíos pausados: el post {post_id} queda aprobado en espera")
            return False
        try:
            tope_envios = leer_reglas().tope_envios_dia
        except ReglasInvalidas as e:
            # Sin el tope del perfil no se envía nada (lo seguro): queda aprobado en espera
            logger.warning(f"[Radar] Sin reglas del Radar: el post {post_id} queda aprobado en espera ({e})",
                           extra={"sin_ticket": True})
            return False
        if int(estado["contadores_dia"]["enviados"]) >= tope_envios:
            logger.info(
                f"[Radar] Tope diario de {tope_envios} envíos alcanzado: "
                f"el post {post_id} queda aprobado para el día siguiente"
            )
            return False
        if not (post.get("email_to") or "").strip() or not (post.get("email_body") or "").strip():
            radar_store.update_post(
                post_id, estado="ERROR", aprobado_at=None,
                error="No se envió: falta el destinatario o el cuerpo del email. Corregilo y volvé a aprobar.",
            )
            await self._emitir_post(post_id)
            return False

        # Condicional en la DB: si otra ruta lo sacó de LISTO/aprobado, no se envía
        if not radar_store.claim_for_sending(post_id):
            logger.info(f"[Radar] El post {post_id} cambió antes de enviarse; no se envía")
            return False
        await self._emitir_post(post_id)
        if self._send_stop_event.is_set():
            # El servidor se cerró durante el broadcast: no se empieza un envío nuevo
            radar_store.update_post(post_id, estado="LISTO")
            return False
        post = radar_store.get_post(post_id) or post

        reintentos = 0
        while True:
            try:
                actual = radar_store.get_post(post_id)
                if not actual or actual["estado"] != "ENVIANDO":
                    return False
                if not actual.get("aprobado_at"):
                    raise AprobacionInvalidadaError("El CV cambió: revisalo y volvé a aprobar el envío")
                post = actual
                message_id = await self._send_fn(post)
                break
            except AprobacionInvalidadaError as e:
                actual = radar_store.get_post(post_id)
                if actual and actual["estado"] == "ENVIANDO":
                    radar_store.update_post(post_id, estado="LISTO", aprobado_at=None, error=str(e))
                    await self._emitir_post(post_id)
                return False
            except MailerAuthError as e:
                # Credenciales rechazadas: se pausan todos los envíos; el post sigue aprobado
                _ticket("envio_fallido", f"Gmail rechazó las credenciales: {e}", excepcion=e, datos={"post_id": post_id})
                radar_store.update_post(
                    post_id, estado="LISTO",
                    error=f"Gmail rechazó las credenciales; envíos pausados: {e}",
                )
                radar_store.update_radar_state(envios_pausados=True)
                await self._emitir_post(post_id)
                await self._emitir_estado()
                return False
            except MailerAmbiguousError as e:
                # Se cortó mientras se transmitía: puede haber salido. Nunca se reintenta solo
                logger.warning(f"[Radar] Envío incierto del post {post_id}: {e}")
                _ticket("envio_incierto", f"Envío incierto del post {post_id}: {e}", excepcion=e,
                        datos={"post_id": post_id})
                radar_store.update_post(
                    post_id, estado="ERROR", aprobado_at=None,
                    error=f"{radar_store.ERROR_ENVIO_INCIERTO} ({e})",
                )
                await self._emitir_post(post_id)
                return False
            except MailerTemporaryError as e:
                # Solo fallos previos a la entrega (conexión/login, 4xx explícito): reintentable
                if reintentos >= len(BACKOFF_SMTP_TEMPORAL):
                    _ticket("envio_fallido", f"Error temporal de SMTP tras {reintentos} reintentos: {e}",
                            excepcion=e, datos={"post_id": post_id})
                    radar_store.update_post(
                        post_id, estado="ERROR", intentos=reintentos, aprobado_at=None,
                        error=f"Error temporal de SMTP tras {reintentos} reintentos: {e}",
                    )
                    await self._emitir_post(post_id)
                    return False
                espera = BACKOFF_SMTP_TEMPORAL[reintentos]
                reintentos += 1
                radar_store.update_post(
                    post_id, intentos=reintentos,
                    error=(f"Error temporal de SMTP (reintento {reintentos}/"
                           f"{len(BACKOFF_SMTP_TEMPORAL)} en {espera} s): {e}"),
                )
                if not await self._wait(espera, self._send_stop_event):
                    # Cierre del servidor durante el backoff: queda aprobado para el próximo arranque
                    radar_store.update_post(
                        post_id, estado="LISTO",
                        error=f"Reintento de envío interrumpido por el cierre del servidor: {e}",
                    )
                    await self._emitir_post(post_id)
                    return False
            except Exception as e:
                # Fallo permanente o desconocido: nunca se reintenta solo (evita emails duplicados)
                logger.warning(f"[Radar] Falló el envío del post {post_id}: {e}")
                _ticket("envio_fallido", f"Falló el envío del post {post_id}: {e}", excepcion=e,
                        datos={"post_id": post_id})
                radar_store.update_post(post_id, estado="ERROR", aprobado_at=None, error=f"Falló el envío: {e}")
                await self._emitir_post(post_id)
                return False

        radar_store.update_post(
            post_id,
            estado="ENVIADO",
            message_id=str(message_id or ""),
            sent_at=self._now().strftime(_FMT_FECHA),  # hora local, como compara recipient_sent_within
            intentos=0,
            error=None,
        )
        radar_store.bump_counter("enviados")
        await self._emitir_post(post_id)
        await self._emitir_estado()
        return True

    # ------------------------------------------------------------------
    # Estado y eventos
    # ------------------------------------------------------------------

    async def notify_status(self) -> None:
        """Emite `radar_status` (para cambios hechos fuera del loop, p. ej. desde la API)."""
        await self._emitir_estado()

    async def notify_post(self, post_id: int) -> None:
        """Emite `radar_post` con el resumen del post (cambios hechos desde la API)."""
        await self._emitir_post(post_id)

    async def _set_estado(self, estado: str, detalle: str = "", **extra: Any) -> None:
        radar_store.update_radar_state(estado=estado, estado_detalle=detalle, **extra)
        await self._emitir_estado()

    async def _emitir_estado(self) -> None:
        try:
            await self._broadcast("radar_status", radar_store.get_radar_state())
        except Exception:
            logger.exception("[Radar] Falló el broadcast de radar_status")

    async def _emitir_post(self, post_id: int) -> None:
        try:
            post = radar_store.get_post(post_id)
            if post is None:
                return
            triage = post.get("triage") if isinstance(post.get("triage"), dict) else {}
            analisis = post.get("analisis") if isinstance(post.get("analisis"), dict) else {}
            await self._broadcast("radar_post", {
                "id": post["id"],
                "estado": post["estado"],
                "autor": post.get("autor") or "",
                "puesto": analisis.get("puesto") or triage.get("puesto") or "",
                "match_score": analisis.get("match_score"),
                "fuente": post.get("fuente") or "busqueda",
                "canal": post.get("canal") or "email",
            })
        except Exception:
            logger.exception("[Radar] Falló el broadcast de radar_post")

    async def _emitir_progreso(self, post_id: int, prog: Dict[str, Any]) -> None:
        """`radar_progreso` del CV en curso: `percent`/`step` (la barra) y el `progreso` completo. El
        pipeline emite además sus propios eventos con `radar_post_id`."""
        try:
            await self._broadcast("radar_progreso", {
                "id": post_id, "percent": prog.get("pct"), "step": prog.get("etiqueta") or "", "progreso": prog,
            })
        except Exception:
            logger.exception(f"[Radar] No se pudo emitir el progreso del post {post_id}")

    async def _emitir_borrado(self, post_id: int) -> None:
        """`radar_post_removed`: el post no era una oferta y se borró (la UI lo saca de la lista)."""
        try:
            await self._broadcast("radar_post_removed", {"id": post_id})
        except Exception:
            logger.exception("[Radar] Falló el broadcast de radar_post_removed")


def _excepcion_de(tarea: asyncio.Task) -> Optional[BaseException]:
    """La excepción de una tarea terminada (None si terminó bien o se canceló). Leerla evita el aviso
    de «excepción nunca recuperada»."""
    return None if tarea.cancelled() else tarea.exception()


class _Detenido(Exception):
    """Señal interna: `stop()` interrumpió la espera de login a mitad de una consulta."""
