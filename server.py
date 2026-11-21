"""Micro-Dashboard Web Local para LinkedOut (FastAPI + SSE + REST)."""
import asyncio
import dataclasses
import json
import logging
import os
import re
import time
import weakref
from copy import deepcopy
from contextlib import asynccontextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from fastapi import FastAPI, BackgroundTasks, HTTPException, Request, Depends
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, StrictBool, StrictStr

from core.database import (
    init_db,
    create_postulacion,
    get_postulacion,
    list_postulaciones,
    update_postulacion,
    update_postulacion_lifecycle,
    toggle_tag,
    delete_postulacion,
)
from core.cv_guardas import COMPLETO, FORMATOS_CV, UNA_PAGINA, corregir_encabezados, es_requisito_logistico, es_requisito_no_habilidad, normalizar_formato
from core.strategist import StrategistAgent
from core.writer import WriterAgent
from core.auditor import AuditorAgent
from core.auditor import AuditorAgent as AuditorTextoFinal
from core import auditor as auditoria_cv
from core import candidato_datos
from core.text_normalization import normalizar_guiones, transformar_fragmentos
from core.docx_generator import compile_markdown_to_docx, generate_cv_filename
from core.pdf_generator import compile_markdown_to_pdf, generate_cv_pdf_filename, puesto_del_cv
from core.scraper import OfertaInexistente, scrape_job_url, filter_job_boilerplate
from core.bridge import etapa, resumen_etapas, AntigravityBridge
from core.expander import CapabilityExpanderAgent
from core.aprendizajes import (
    aplicar_aprendizajes_a_analisis, aplicar_aprendizajes_a_requisitos, obtener_aprendizajes, registrar_aprendizaje,
)
from core.profile_parser import load_candidate_profile
from core.perfil_areas import asegurar_anios_por_area
from core import perfil_candidato, progreso, radar_store, revision_store
from core.completitud import completar, contradicciones_cv, corregir_puesto, placeholders
from core.titulo_cv import DecisionTitulo, decidir_titulo, aplicar_titulo, asegurar_mencion_ats
from core.radar import CORRECCIONES_DE_CALIDAD, AprobacionInvalidadaError, RadarLoop
from core.radar_hilo import RadarEnHilo
from core.vigia import Vigia
from core.radar_api import (
    build_radar_router, _solo_operacion_local,
    _solo_pc_o_emparejado, _require_gmail,
)
from core.linkedin_harvester import LinkedInHarvester
from core.triage import triage_batch
from core.email_composer import (
    compose_message, email_html, email_texto_plano, problemas_de_tono, MensajeRequiereRevision,
    validar_mensaje_final, build_email_signature, build_signature, asunto_exigido,
)
from core.idioma import EN, idioma_cv_de_oferta, idioma_de_cv
from core import cv_maestro
from core import cv_final
from core import auditoria_envio, correccion_auditoria
from core.prioridades import texto_capturado as texto_para_analisis
from core.mailer import MailerAuthError, MailerError, load_gmail_config, send_application_email
from core.paths import DATA_DIR, variable_entorno
from core import tickets
from core.tickets_api import MiddlewareTickets, build_tickets_router
from core.cabeceras_seguridad import CabecerasSeguridad

logger = logging.getLogger(__name__)
tickets.instalar_logging()

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
EXPORTS_DIR = DATA_DIR / "exports"
GENERACIONES_EN_PARALELO = 1


async def _analizar_perfil_al_iniciar():
    """Tarea acotada: un proveedor ausente nunca impide el arranque del portal."""
    try:
        await asyncio.wait_for(asegurar_anios_por_area(), timeout=100)
    except Exception:
        logger.warning("No se pudo preparar el análisis de años por área", exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Reanuda el Radar al iniciar (si el usuario lo dejó corriendo) y lo cierra al apagar.

    Un fallo del Radar nunca impide que el portal arranque o se cierre.
    """
    tickets.capturar_logs_de_uvicorn()
    # Vigía: si el event loop se traba más de 10 s, las pilas de todos los hilos quedan en vigia.log
    app.state.vigia = Vigia(DATA_DIR / "vigia.log")
    await app.state.vigia.iniciar()
    if variable_entorno("SIN_RADAR") == "1":
        # Portales de prueba: nunca abren Edge (perfil vacío, sin sesión de LinkedIn).
        logger.info("[Radar] LINKEDOUT_SIN_RADAR=1: el Radar no se reanuda en este portal")
    else:
        try:
            await radar_loop.resume_if_needed()
        except Exception:
            logger.exception("[Radar] No se pudo reanudar el radar al iniciar; el portal sigue sin él")
    tarea_perfil = asyncio.create_task(_analizar_perfil_al_iniciar(), name="anios-por-area")
    try:
        yield
    finally:
        tarea_perfil.cancel()
        await asyncio.gather(tarea_perfil, return_exceptions=True)
        try:
            await radar_loop.stop_for_shutdown()
        except Exception:
            logger.exception("[Radar] Error al cerrar el radar")
        await app.state.vigia.detener()


app = FastAPI(title="LinkedOut", description="Portal local de búsqueda de empleo", lifespan=lifespan)
# Tickets de errores: excepciones no manejadas y respuestas 4xx/5xx de la API (core/tickets_api.py)
app.add_middleware(MiddlewareTickets)
# Ninguna respuesta se muestra en un iframe ajeno (clickjacking; docs/seguridad/auditoria-20261117.md M4)
app.add_middleware(CabecerasSeguridad)
app.include_router(build_tickets_router())

# Toda la API (lecturas incluidas: devuelven datos del candidato) solo para la PC o un celular emparejado.
# tests/test_guardas_rutas.py recorre las rutas y falla si alguna queda sin guarda fuera de su lista blanca.
_GUARDA = [Depends(_solo_pc_o_emparejado)]

# Instancia global del agente expansor de competencias
expander_agent = CapabilityExpanderAgent()

# Inicializar Base de Datos en startup
init_db()

# Broker de SSE en memoria para notificaciones en vivo
subscribers: List[asyncio.Queue] = []
# Loop de cada cola del SSE: el Radar emite desde su propio hilo y una cola solo se toca desde su loop
_LOOPS_SSE: "weakref.WeakKeyDictionary[asyncio.Queue, asyncio.AbstractEventLoop]" = weakref.WeakKeyDictionary()


# Origen ('manual' | 'linkedin') de la postulación cuyo pipeline corre en este contexto async.
# `run_pipeline_task` lo fija y `broadcast_event` lo agrega a los eventos del pipeline (también a
# los heartbeats, que corren en tareas hijas y heredan el contexto): así app.js ignora los
# pipelines del Radar en la vista manual.
_origen_pipeline: ContextVar[Optional[str]] = ContextVar("origen_pipeline", default=None)
# Post del Radar cuyo CV se está generando: sus eventos del pipeline lo llevan (barra de progreso)
# Reescrituras del Auditor cuando el CV no aprueba (keywords, señales de escritura IA)
INTENTOS_REMEDIACION_CV = 1
_radar_post_pipeline: ContextVar[Optional[int]] = ContextVar("radar_post_pipeline", default=None)
# Una invocación posee sus ramas y su borrador; las hijas heredan este objeto.
_finalizacion_radar: ContextVar[Optional[Dict[str, Any]]] = ContextVar("finalizacion_radar", default=None)
_EVENTOS_PIPELINE = frozenset({"status", "status_heartbeat", "complete", "error"})


async def broadcast_event(event_type: str, data: Dict[str, Any]):
    if event_type == "radar_status" and data.get("estado") == "DETENIDO":
        _descartar_borradores()
    elif event_type in ("radar_post", "radar_post_removed") and data.get("estado") != "GENERANDO":
        _descartar_borradores(data.get("id"))
    origen = _origen_pipeline.get()
    if origen and event_type in _EVENTOS_PIPELINE and isinstance(data, dict) and "origen" not in data:
        data = {**data, "origen": origen}
    radar_post_id = _radar_post_pipeline.get()
    if radar_post_id is not None and event_type in _EVENTOS_PIPELINE and isinstance(data, dict):
        data = {**data, "radar_post_id": radar_post_id}
    message = f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
    try:
        actual = asyncio.get_running_loop()
    except RuntimeError:
        actual = None
    for queue in list(subscribers):
        loop = _LOOPS_SSE.get(queue)
        if loop is None or loop is actual:
            _entregar_evento(queue, message)
        elif not loop.is_closed():
            # Desde el hilo del Radar: la cola es del loop del portal y se le entrega allá, en orden
            try:
                loop.call_soon_threadsafe(_entregar_evento, queue, message)
            except RuntimeError:
                _quitar_suscriptor(queue)
        else:
            _quitar_suscriptor(queue)


def _entregar_evento(queue: asyncio.Queue, message: str) -> None:
    try:
        queue.put_nowait(message)
    except Exception:
        _quitar_suscriptor(queue)


def _quitar_suscriptor(queue: asyncio.Queue) -> None:
    if queue in subscribers:
        subscribers.remove(queue)


def _keywords_del_cv(keywords: List[str], analisis: Optional[Dict[str, Any]]) -> List[str]:
    """En un CV en inglés, cada requisito se busca con el término en inglés que usó el Redactor
    (`keywords_cv`): los nombres de los requisitos del Estratega están en español."""
    analisis = analisis if isinstance(analisis, dict) else {}
    # Residencia, modalidad o jornada no son keywords del CV: exigirlas obligaba a copiarlas al perfil
    #. Si no queda ninguna, se conserva la lista original.
    keywords = [k for k in keywords if not es_requisito_logistico(k)] or keywords
    # Rubro, formación y años tampoco: la remediación los copiaba a HABILIDADES TÉCNICAS
    keywords = [k for k in keywords if not es_requisito_no_habilidad(k)] or keywords
    mapa = analisis.get("keywords_cv") if analisis.get("idioma_cv") == EN else None
    if not isinstance(mapa, dict):
        return keywords
    return list(dict.fromkeys(mapa.get(k) or k for k in keywords))


def _guardar_idioma_del_cv(postulacion_id: int, redaccion: Dict[str, Any]) -> str:
    """Deja en el análisis guardado el idioma del CV redactado y sus keywords en inglés (para
    re-auditar y exportar). Parte de lo que está en la base, sin tocar el resto del análisis."""
    analisis = (get_postulacion(postulacion_id) or {}).get("analisis") or {}
    if isinstance(analisis, str):
        try:
            analisis = json.loads(analisis)
        except Exception:
            analisis = {}
    # El fallback determinístico del Redactor no trae idioma: se deduce del CV que dejó
    analisis["idioma_cv"] = redaccion.get("idioma_cv") or idioma_de_cv(redaccion.get("cv_markdown") or "")
    if redaccion.get("keywords_cv"):
        analisis["keywords_cv"] = redaccion["keywords_cv"]
    else:
        analisis.pop("keywords_cv", None)
    update_postulacion(postulacion_id, analisis=analisis)
    return analisis["idioma_cv"]


def extract_matriz_oferta(requisitos: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Clasifica los requisitos de la vacante en categorías semánticas para la Matriz de Oferta."""
    herramientas = []
    metodologias = []
    responsabilidades = []
    otros = []

    # Herramientas genéricas más las del stack del candidato (Mi perfil)
    HERRAMIENTAS_KW = {
        "python", "sql", "bpmn", "visio", "jira", "confluence", "jenkins",
        "docker", "kubernetes", "git", "gitlab", "power bi", "powerbi", "tableau", "aws", "gcp",
        "servicenow", "excel", "rpa", "uipath", "postman", "linux", "oracle", "sap"
    } | candidato_datos.palabras_stack()
    METODOLOGIAS_KW = {
        "scrum", "kanban", "agile", "ágil", "lean", "six sigma", "kaizen", "itil",
        "as is", "as-is", "to be", "to-be", "kpi", "kpis", "mejora continua", "cascada", "uml"
    }

    for req in requisitos:
        nombre_lower = req.get("nombre", "").lower()
        tipo = req.get("tipo", "")
        if any(h in nombre_lower for h in HERRAMIENTAS_KW):
            herramientas.append(req)
        elif any(m in nombre_lower for m in METODOLOGIAS_KW):
            metodologias.append(req)
        elif tipo == "RESPONSABILIDAD" or any(w in nombre_lower for w in ("relevamiento", "diseño", "coordinación", "documentación", "análisis", "gestión")):
            responsabilidades.append(req)
        else:
            otros.append(req)

    return {
        "herramientas": herramientas,
        "metodologias": metodologias,
        "responsabilidades": responsabilidades,
        "otros": otros,
        "total": len(requisitos),
    }


HEARTBEAT_INTERVAL: float = 1.0
# Etapas medidas de cada postulación en curso (`track_phase`); se guardan en su telemetría
_ETAPAS_PIPELINE: Dict[int, List[Dict[str, Any]]] = {}
_NOMBRE_ETAPA = {"ESTRATEGA": "analisis", "REDACTOR": "redactor", "AUDITOR": "auditoria"}


def _registrar_revision(cambios: List[Dict[str, str]], postulacion_id: Optional[int],
                        post_id: Optional[int], donde: str) -> None:
    for c in cambios:
        try:
            revision_store.registrar(c.get("tipo") or "dato_inventado", c.get("campo") or "", c.get("valor") or "",
                                     f"Completado en el {donde}", postulacion_id=postulacion_id, post_id=post_id)
            if c.get("tipo") == "contradiccion" and donde == "CV":
                # Corregido en el CV: los «No se pudo corregir» anteriores de ese campo ya no valen
                # (si el chequeo final la sigue encontrando, `registrar` los reabre)
                revision_store.resolver_anteriores(postulacion_id, c.get("campo") or "")
        except Exception:
            logger.exception("[Completitud] No se pudo registrar un ítem de revisión")


def _contexto_oferta(postulacion: Dict[str, Any]) -> str:
    return "\n".join(str(x) for x in (postulacion.get("titulo"), postulacion.get("empresa"),
                                       postulacion.get("contenido_oferta")) if x)


def _perfil_o_vacio() -> Dict[str, Any]:
    try:
        return load_candidate_profile()
    except Exception:
        return {}


def _normalizar_redaccion(redaccion: dict) -> dict:
    """Rayas resueltas antes del auditor, sin cambiar identidad ni referencias."""
    perfil = _perfil_o_vacio()
    protegidos = (perfil.get("nombre"), perfil.get("contacto"),
                  *build_email_signature(perfil).splitlines(), *build_signature(perfil).splitlines())
    return {**redaccion,
            **{c: normalizar_guiones(redaccion.get(c) or "", protegidos) for c in ("cv_markdown", "pitch")},
            "pitches_multicanal": {c: normalizar_guiones(t, protegidos) if isinstance(t, str) else t
                                   for c, t in (redaccion.get("pitches_multicanal") or {}).items()}}


def _sanear_texto_final(texto: str, campo: str, literal: str = "") -> str:
    """Solo reparaciones deterministas; identidad, encabezados y referencias son literales."""
    auditor = AuditorTextoFinal()
    perfil = _perfil_o_vacio()
    nombre = perfil.get("nombre") or ""
    contacto = perfil.get("contacto") or ""
    firmas = [build_email_signature(perfil), build_signature(perfil)]
    if literal and texto == literal:
        return texto
    lineas = []
    cabecera = False
    for linea in texto.splitlines(keepends=True):
        if campo == "CV" and linea.lstrip().startswith("# "):
            cabecera = True
        elif linea.lstrip().startswith("## "):
            cabecera = False
        protegidos = [nombre, contacto, *[p for f in firmas for p in f.splitlines() if p.strip()]]
        if campo == "CV" and linea.lstrip().startswith("# "):
            protegidos.append(linea.strip())
        def sanear(parte):
            if not (cabecera or linea.lstrip().startswith("#")):
                parte = auditor.remediate_text_deterministic(parte)
            # El contexto de identidad ya se protegió por fragmento.
            return normalizar_guiones("Texto " + parte)[len("Texto "):]
        lineas.append(transformar_fragmentos(linea, sanear, protegidos))
    return "".join(lineas)


def _motivos_texto_final(texto: str, campo: str, literal: str = "") -> List[str]:
    """Señales deterministas del texto final. El tono del mail (fórmulas formales, largo,
    frases fijas) se valida solo sobre el asunto y el cuerpo que se envían."""
    perfil = _perfil_o_vacio()
    protegidos = (perfil.get("nombre"), perfil.get("contacto"),
                  *build_email_signature(perfil).splitlines(), *build_signature(perfil).splitlines(), literal)
    # El escáner sólo recibe prosa; los tokens literales no son señales de redacción.
    revision = transformar_fragmentos(texto, lambda parte: parte, protegidos, ocultar_protegidos=True)
    motivos = list(auditoria_cv.scan_anti_ai_writing(revision).get("detected_words") or [])
    if campo in ("subject", "body"):
        motivos.extend(problemas_de_tono(revision))
    motivos.extend(f"campo sin completar: {p}" for p in placeholders(texto))
    if campo == "CV":
        motivos.extend(c["detalle"] for c in contradicciones_cv(texto, _perfil_o_vacio()))
    return list(dict.fromkeys(motivos))


def _registrar_senales_finales(campo: str, texto: str, motivos: List[str],
                              postulacion_id: Optional[int], post_id: Optional[int] = None) -> None:
    if motivos:
        try:
            revision_store.registrar("senal_ia", campo, texto, "; ".join(motivos),
                                     postulacion_id=postulacion_id,
                                     post_id=post_id if post_id is not None else _radar_post_pipeline.get())
        except ValueError:
            # Un registro de revisión incompatible no debe perder el documento ni
            # convertir su rechazo en READY. El diagnóstico también queda en el reporte.
            logger.exception("[Calidad final] No se pudo registrar la señal de revisión")


def _formato_cv_de(postulacion_id: int) -> str:
    """Formato pedido para el CV de la postulación: «completo» (por defecto) o «una_pagina»."""
    return normalizar_formato((get_postulacion(postulacion_id) or {}).get("formato_cv"))


def _problemas_de_veracidad(cv: str, formato: Optional[str]) -> List[str]:
    """Roles o empresas de la base que faltan y encabezados sin respaldo (sin IA)."""
    roles = _perfil_o_vacio().get("roles") or []
    # Sin encabezados de rol no hay qué contrastar: las secciones y empresas faltantes ya las marca el ATS
    if not roles or not re.search(r"^### ", cv or "", flags=re.MULTILINE):
        return []
    _, _, problemas = corregir_encabezados(cv, roles, normalizar_formato(formato), ingles=idioma_de_cv(cv) == EN)
    return problemas


def _dictamen_final(postulacion: dict, cv: str, pitch: str, pitches: dict,
                     reporte: Optional[dict], keywords: Optional[List[str]] = None,
                     motivos_extra: Optional[List[str]] = None,
                     cv_antes: Optional[str] = None,
                     conservar_sin_dictamen: bool = False) -> Tuple[dict, List[str]]:
    """Revalida el contenido final sin IA: señales deterministas, campos sin completar y
    contradicciones sobre el texto real, más el dictamen recibido. Nunca promueve un rechazo:
    un reporte rechazado se conserva tal cual; uno aprobado pasa a rechazado si el texto final
    no cumple la auditoría sin IA. Ambas mediciones usan los criterios del reporte
    recibido cuando están disponibles; nunca se compara con el ATS de otro análisis."""
    previo = reporte if isinstance(reporte, dict) else {}
    motivos = list(motivos_extra or [])
    canales_no_enviados = {"web_ats", "inmail"}
    for campo, texto in [("CV", cv), ("pitch", pitch),
                         *((f"mensaje ({c})", t) for c, t in pitches.items() if isinstance(t, str))]:
        hallazgos = _motivos_texto_final(texto, campo)
        _registrar_senales_finales(campo, texto, hallazgos, postulacion["id"])
        canal_nombre = campo[9:-1] if campo.startswith("mensaje (") and campo.endswith(")") else None
        if canal_nombre in canales_no_enviados:
            continue
        motivos.extend(f"{campo}: {m}" for m in hallazgos)
    motivos.extend(f"CV: {p}" for p in _problemas_de_veracidad(cv, postulacion.get("formato_cv")))
    auditor = AuditorTextoFinal()
    empresas = _perfil_o_vacio().get("empresas") or []
    claves = keywords if keywords is not None else _extract_postulacion_keywords(postulacion)
    criterios = previo.get("ats") or {}
    if "found_keywords" in criterios and "missing_keywords" in criterios:
        claves = [*criterios["found_keywords"], *criterios["missing_keywords"]]
    if "found_experiences" in criterios and "missing_experiences" in criterios:
        empresas = [*criterios["found_experiences"], *criterios["missing_experiences"]]
    antes = auditor.audit(cv_antes if cv_antes is not None else postulacion.get("cv_markdown") or "",
                          pitch, claves, expected_companies=empresas)
    despues = auditoria_cv.aceptar_limites_del_perfil(auditor.audit(cv, pitch, claves, expected_companies=empresas))
    if previo and previo.get("approved") is False:
        anteriores = ((previo.get("hallazgos_unificados") or {}).get("lista_observaciones")
                      or previo.get("deducciones") or ["La auditoría no aprobó el contenido"])
        motivos = list(dict.fromkeys([*motivos, *anteriores]))
        _registrar_senales_finales("CV", cv, motivos, postulacion["id"])
        return previo, motivos
    perdidas = []
    for campo, etiqueta in (("missing_keywords", "keywords"), ("missing_sections", "secciones"),
                             ("missing_experiences", "empresas")):
        nuevas = set(despues["ats"].get(campo) or []) - set(antes["ats"].get(campo) or [])
        if nuevas:
            perdidas.append(f"La completitud quitó {etiqueta} obligatorias: {', '.join(sorted(nuevas))}")
    if perdidas:
        motivos.extend(perdidas)
        previo = despues
    if previo.get("approved") is True and (not despues["approved"] or despues.get("limites_perfil")):
        # El dictamen refleja el CV final; con límites del perfil sigue aprobado con observaciones
        previo = despues
        if not despues["approved"]:
            motivos.extend(despues.get("deducciones") or ["La auditoría final no aprobó el CV"])
    elif not previo and not conservar_sin_dictamen:
        previo = despues
        if not despues["approved"]:
            motivos.extend(despues.get("deducciones") or ["La auditoría final no aprobó el CV"])
    motivos = list(dict.fromkeys(motivos))
    if not motivos:
        return previo, []
    final = deepcopy(previo)
    final["approved"] = False
    final["verdict"] = "REQUIERE REMEDIACIÓN"
    final["bloqueos_finales"] = motivos  # corregibles: no se aceptan como límites del perfil
    final["hallazgos_unificados"] = {**(final.get("hallazgos_unificados") or {}),
                                     "requiere_accion": True, "lista_observaciones": motivos}
    final["evaluacion_profesional"] = {**(final.get("evaluacion_profesional") or {}),
                                       "approved": False, "verdict": final["verdict"],
                                       "hallazgos_unificados": final["hallazgos_unificados"]}
    final["diagnostico_documento"] = {**(final.get("diagnostico_documento") or {}),
                                      "hallazgos_unificados": final["hallazgos_unificados"]}
    _registrar_senales_finales("CV", cv, motivos, postulacion["id"])
    return final, motivos


async def _finalizar_cv(postulacion_id: int, cv: str, pitch: str, pitches: dict,
                        reporte: dict, keywords: List[str], hallazgos_por_etapa=None):
    """Conserva el dictamen y el documento incluso si falla la completitud del mail."""
    # lo que solo falla por límites del perfil (keywords o métricas que no se pueden
    # inventar) sigue con observaciones; los defectos corregibles bloquean igual.
    reporte = auditoria_cv.aceptar_limites_del_perfil(reporte)
    cv_antes = cv
    update_postulacion(postulacion_id, cv_markdown=cv, pitch=pitch,
                       pitches_multicanal=pitches, reporte_auditoria=reporte)
    if isinstance(reporte, dict) and reporte.get("approved") is False:
        # Dictamen ya rechazado: no se completa ni se compone el mail (cuota sin destino); el
        # contenido queda para revisar tal como lo dejó la auditoría.
        incompleto = placeholders(cv)
    else:
        cv, pitch, pitches, incompleto = await _asegurar_completo(postulacion_id, cv, pitch, pitches)
    final, motivos = _dictamen_final(get_postulacion(postulacion_id), cv, pitch, pitches, reporte, keywords,
                                     cv_antes=cv_antes)
    # El reporte es la evidencia de calidad; las duraciones no lo sustituyen.
    for medida in reversed(_ETAPAS_PIPELINE.get(postulacion_id, [])):
        if medida.get("etapa") == "auditoria":
            medida["hallazgos"] = deepcopy(final.get("hallazgos_unificados") or {
                "lista_observaciones": final.get("deducciones") or [],
                "requiere_accion": final.get("approved") is False})
            medida["hallazgos_por_etapa"] = deepcopy(hallazgos_por_etapa or [])
            break
    telemetry = _telemetria_con_etapas(postulacion_id, (
        _ultima_telemetria(auditoria_cv)
        or {"model": AntigravityBridge.active_model, "status": "ERROR"}
    )) if final.get("approved") is False else None
    if telemetry is not None:
        telemetry["status"] = "ERROR"
    update_postulacion(postulacion_id, cv_markdown=cv, pitch=pitch,
                       pitches_multicanal=pitches, reporte_auditoria=final,
                       **({"telemetry": telemetry} if telemetry else {}),
                       status="PROCESSING" if final.get("approved") is not False else "ERROR")
    if final.get("approved") is False:
        raise RuntimeError("Revisá el CV: " + "; ".join(motivos))
    return cv, pitch, pitches, incompleto, final


async def _decidir_titulo_cv(postulacion: Dict[str, Any], cv: str, *, sin_ia: bool = False) -> DecisionTitulo:
    """Titular decidido por código. Sin línea de título bajo el nombre no hay nada que fijar: no se
    llama a la IA y el header queda igual (la mención ATS del perfil se sigue aplicando)."""
    actual = puesto_del_cv(cv)
    decision = await decidir_titulo(postulacion, titulo_actual=actual, sin_ia=sin_ia or not actual,
                                    ingles=idioma_de_cv(cv) == EN)
    return decision if actual else dataclasses.replace(decision, titulo="")


async def _asegurar_completo(postulacion_id: int, cv: str, pitch: str,
                             pitches: Optional[Dict[str, str]],
                             informar_paso: bool = True, *,
                             _decision_titulo: Optional[DecisionTitulo] = None) -> Tuple[str, str, Dict[str, str], List[str]]:
    """Nada sale con campos a rellenar: completa los del CV, el pitch y los mensajes por canal, y
    corrige lo que infla el perfil (todo queda en `revision_pendiente`). Devuelve los campos que
    quedaron sin completar en el CV (el Radar no ofrece ese CV).

    `informar_paso=False` cuando corre dentro de la redacción del mensaje (`_cv_al_dia`): el
    progreso del CV del Radar no vuelve a un paso anterior."""
    if informar_paso:
        await progreso.reportar("completar")
    postulacion = get_postulacion(postulacion_id) or {}
    post_id = _radar_post_pipeline.get()
    contexto = _contexto_oferta(postulacion)
    perfil = _perfil_o_vacio()
    cv, pitch, pitches = cv or "", pitch or "", dict(pitches or {})
    finales = {"cv": cv, "pitch": pitch, "pitches": pitches}

    async def completar_cv():
        with etapa("completar") as medida:
            try:
                decision = _decision_titulo
                if decision is None:
                    decision = await _decidir_titulo_cv(postulacion, cv)
                if decision.titulo:
                    nuevo, cambio = aplicar_titulo(cv, decision)
                else:
                    nuevo, cambio = await corregir_puesto(cv, contexto=contexto)
                _registrar_revision([cambio] if cambio else [], postulacion_id, post_id, "CV")
                nuevo, cambios = await completar(nuevo, tipo="CV", contexto=contexto,
                                                contradicciones=[c for c in contradicciones_cv(nuevo, perfil)
                                                                 if c["campo"] != "puesto objetivo"])
                _registrar_revision(cambios, postulacion_id, post_id, "CV")
                nuevo = _sanear_texto_final(nuevo, "CV")
                nuevo, cambio = aplicar_titulo(nuevo, decision)
                if cambio:
                    _registrar_revision([cambio], postulacion_id, post_id, "CV")
                nuevo = asegurar_mencion_ats(nuevo, decision.puesto_oferta)
                finales["cv"] = nuevo
                return nuevo
            finally:
                if medida["llamadas"]:
                    _ETAPAS_PIPELINE.setdefault(postulacion_id, []).append(medida)

    async def completar_presentacion():
        nuevo, canales = pitch, dict(pitches)
        with etapa("completar_pitch") as medida:
            try:
                if nuevo:
                    nuevo, cambios = await completar(nuevo, tipo="pitch", contexto=contexto)
                    _registrar_revision(cambios, postulacion_id, post_id, "pitch")
                for canal, texto in canales.items():
                    if isinstance(texto, str) and texto:
                        canales[canal], cambios = await completar(texto, tipo=f"mensaje ({canal})", contexto=contexto)
                        _registrar_revision(cambios, postulacion_id, post_id, f"mensaje ({canal})")
            finally:
                if medida["llamadas"]:
                    _ETAPAS_PIPELINE.setdefault(postulacion_id, []).append(medida)
        nuevo = _sanear_texto_final(nuevo, "pitch")
        canales = {c: _sanear_texto_final(t, f"mensaje ({c})") if isinstance(t, str) else t
                   for c, t in canales.items()}
        finales.update(pitch=nuevo, pitches=canales)
        ejecucion = _finalizacion_radar.get()
        if (ejecucion is not None and ejecucion["pid"] == postulacion_id and informar_paso
                and ejecucion["post"].get("estado") == "GENERANDO"):
            # El borrador solo se consume durante una generación del worker.
            # Un pedido aislado de CV no debe depender también del compositor.
            snapshot = {**postulacion, "pitch": nuevo, "pitches_multicanal": canales}
            mensaje = await _componer_mensaje_validado(ejecucion["post"], snapshot)
            ejecucion["borrador"] = (snapshot, mensaje)
        return nuevo, canales

    ejecucion = _finalizacion_radar.get()
    try:
        if ejecucion is not None and ejecucion["pid"] == postulacion_id and informar_paso:
            tareas = [asyncio.create_task(completar_cv(), name="completar-cv"),
                      asyncio.create_task(completar_presentacion(), name="completar-pitch-mail")]
            try:
                cv, (pitch, pitches) = await asyncio.gather(*tareas)
            finally:
                # gather no cancela la hermana cuando una rama falla (CancelledError
                # tampoco deriva de Exception). No devolver con trabajo pendiente.
                for tarea in tareas:
                    if not tarea.done():
                        tarea.cancel()
                await asyncio.gather(*tareas, return_exceptions=True)
        else:
            cv = await completar_cv()
            pitch, pitches = await completar_presentacion()
    except Exception as exc:
        actual = get_postulacion(postulacion_id) or postulacion
        informe, _ = _dictamen_final(actual, finales["cv"], finales["pitch"], finales["pitches"],
                                     actual.get("reporte_auditoria"), motivos_extra=[str(exc)])
        update_postulacion(postulacion_id, cv_markdown=finales["cv"], pitch=finales["pitch"],
                           pitches_multicanal=finales["pitches"], reporte_auditoria=informe, status="ERROR")
        raise
    restos = {"CV": placeholders(cv), "pitch": placeholders(pitch)}
    restos.update({f"mensaje ({c})": placeholders(t) for c, t in pitches.items() if isinstance(t, str)})
    for donde, campos in restos.items():
        for campo in campos:
            revision_store.registrar("placeholder", campo, campo, f"Quedó sin completar en el {donde}",
                                     postulacion_id=postulacion_id, post_id=post_id)
    for c in contradicciones_cv(cv, perfil):
        revision_store.registrar("contradiccion", c["campo"], c["frase"], f"{revision_store.PREFIJO_SIN_CORREGIR}: {c['detalle']}",
                                 postulacion_id=postulacion_id, post_id=post_id)
    for donde, texto in [("CV", cv), ("pitch", pitch),
                         *((f"mensaje ({c})", t) for c, t in pitches.items() if isinstance(t, str))]:
        _registrar_senales_finales(donde, texto, _motivos_texto_final(texto, donde), postulacion_id, post_id)
    if ejecucion is not None and informar_paso and restos["CV"]:
        raise RuntimeError(f"El CV quedó con campos sin completar ({', '.join(restos['CV'])}): reintentá")
    return cv, pitch, pitches, restos["CV"]


def _ultima_telemetria(agente: Any) -> Optional[Dict[str, Any]]:
    return getattr(getattr(agente, "bridge", None), "last_telemetry", None)


def _telemetria_con_etapas(postulacion_id: int, base: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Telemetría de la postulación: la base (la del Redactor, que es la llamada larga) más las
    etapas medidas en este pipeline con su total."""
    nuevas = _ETAPAS_PIPELINE.pop(postulacion_id, [])
    # La captura exterior vuelve a consolidar tras _finalizar_cv: un drenaje
    # vacío no debe borrar las etapas y el total ya persistidos.
    if not nuevas and base and "etapas" in base:
        return dict(base)
    # Si hay medidas nuevas, pertenecen al intento actual: la base puede ser
    # telemetría persistida de un intento previo y no se debe sumar otra vez.
    return {**(base or {}), **resumen_etapas(nuevas)}


def _limpiar_fallback_radar(postulacion_id: int) -> None:
    """Limpia la marca/telemetría de fallback de un intento previo."""
    try:
        postu = get_postulacion(postulacion_id)
        if not postu:
            return
        telemetria = postu.get("telemetry")
        if isinstance(telemetria, str):
            try:
                telemetria = json.loads(telemetria)
            except Exception:
                telemetria = None
        if isinstance(telemetria, dict) and telemetria.get("is_fallback"):
            tel_limpia = {k: v for k, v in telemetria.items() if k not in ("is_fallback", "fallback_reason")}
            if tel_limpia.get("status") == "DETERMINISTIC_FALLBACK":
                tel_limpia.pop("status", None)
            update_postulacion(postulacion_id, telemetry=tel_limpia if tel_limpia else None)
    except Exception:
        logger.exception(f"[Radar] No se pudo limpiar la telemetría de fallback de la postulación {postulacion_id}")


@asynccontextmanager
async def track_phase(
    postulacion_id: int,
    phase: int,
    agent: str,
    agent_title: str,
    step: str,
    base_detail: str,
    base_percent: float,
    target_percent: float,
    start_time: Optional[float] = None,
    heartbeat_interval: Optional[float] = None,
):
    """Context manager asíncrono que emite eventos de heartbeat periódicos durante la inferencia de un subagente."""
    if start_time is None:
        start_time = time.time()
    phase_start = time.time()
    interval = heartbeat_interval if heartbeat_interval is not None else HEARTBEAT_INTERVAL

    async def _heartbeat_worker():
        try:
            while True:
                await asyncio.sleep(interval)
                now = time.time()
                elapsed = int(now - start_time)
                phase_elapsed = int(now - phase_start)

                pct = min(target_percent - 1.0, base_percent + (phase_elapsed * 1.5))
                substep = f"{agent_title} en curso ({phase_elapsed}s)"
                detail = f"{base_detail} ({phase_elapsed}s transcurridos)"

                await broadcast_event("status_heartbeat", {
                    "id": postulacion_id,
                    "status": "PROCESSING",
                    "phase": phase,
                    "total_phases": 3,
                    "agent": agent,
                    "agent_title": agent_title,
                    "step": step,
                    "substep": substep,
                    "detail": detail,
                    "elapsed_seconds": elapsed,
                    "phase_elapsed_seconds": phase_elapsed,
                    "percent": round(pct, 1),
                    "heartbeat": True,
                    "model": AntigravityBridge.active_model,
                    "effort": AntigravityBridge.active_effort,
                })
        except asyncio.CancelledError:
            raise

    with etapa(_NOMBRE_ETAPA.get(agent, agent.lower())) as registro:
        hb_task = asyncio.create_task(_heartbeat_worker())
        try:
            yield {
                "start_time": start_time,
                "phase_start": phase_start,
            }
        finally:
            hb_task.cancel()
            try:
                await hb_task
            except asyncio.CancelledError:
                pass
    _ETAPAS_PIPELINE.setdefault(postulacion_id, []).append(registro)


async def emit_milestone(
    postulacion_id: int,
    phase: int,
    agent: str,
    agent_title: str,
    step: str,
    detail: str,
    percent: float,
    start_time: float,
    phase_start: float,
    metrics: Dict[str, Any],
):
    """Emite un evento de hito intermedio con métricas reales tras la conclusión de un subagente."""
    now = time.time()
    await broadcast_event("status_heartbeat", {
        "id": postulacion_id,
        "status": "PROCESSING",
        "phase": phase,
        "total_phases": 3,
        "agent": agent,
        "agent_title": agent_title,
        "step": step,
        "substep": step,
        "detail": detail,
        "elapsed_seconds": int(now - start_time),
        "phase_elapsed_seconds": int(now - phase_start),
        "percent": percent,
        "heartbeat": False,
        "milestone": True,
        "metrics": metrics,
        "model": AntigravityBridge.active_model,
        "effort": AntigravityBridge.active_effort,
    })


async def run_pipeline_task(postulacion_id: int, url: Optional[str] = None):
    """Ejecuta el pipeline de 3 subagentes de forma asíncrona con telemetría heartbeat en tiempo real.

    Sus eventos SSE llevan `origen` de la postulación (ver `_origen_pipeline`).
    """
    postulacion = get_postulacion(postulacion_id)
    token = _origen_pipeline.set((postulacion or {}).get("origen") or "manual")
    try:
        await _run_pipeline_task_impl(postulacion_id, url)
    finally:
        _origen_pipeline.reset(token)


async def _run_pipeline_task_impl(postulacion_id: int, url: Optional[str] = None):
    postulacion = get_postulacion(postulacion_id)
    if not postulacion:
        return
    # Todo texto de oferta es de terceros (posible prompt injection);
    # Estratega y Redactor lo reciben delimitado y marcado como datos, no instrucciones.
    terceros = {"datos_de_terceros": True}

    # Ingesta asíncrona: si se especifica URL o si el contenido es un link, raspar en segundo plano
    target_url = url or postulacion.get("url")
    if target_url and (url or (postulacion.get("contenido_oferta") or "").strip().startswith(("http://", "https://"))):
        try:
            scraped = await scrape_job_url(target_url)
            scraped_content = scraped.get("contenido", "")
            scraped_title = scraped.get("titulo") or postulacion.get("titulo") or "Posición Importada"
            scraped_empresa = scraped.get("empresa") or postulacion.get("empresa") or "Empresa Externa"

            try:
                from core.database import get_db_connection
                _conn = get_db_connection()
                with _conn:
                    _conn.execute("ALTER TABLE postulaciones ADD COLUMN raw_scraped_data TEXT DEFAULT '{}';")
                _conn.close()
            except Exception:
                pass

            update_kwargs = {
                "contenido_oferta": scraped_content or target_url,
                "raw_scraped_data": json.dumps(scraped, ensure_ascii=False) if isinstance(scraped, (dict, list)) else str(scraped),
            }
            if scraped_title and scraped_title not in ("Posición por URL", "Posición Importada", "Posición Técnica", "Encolada..."):
                update_kwargs["titulo"] = scraped_title
            elif not postulacion.get("titulo") or postulacion.get("titulo") in ("Posición por URL", "Posición Importada", "Encolada..."):
                update_kwargs["titulo"] = scraped_title

            if scraped_empresa and scraped_empresa not in ("Detectando...", "Cargando URL...", "Empresa Externa", "Confidencial"):
                update_kwargs["empresa"] = scraped_empresa
            elif not postulacion.get("empresa") or postulacion.get("empresa") in ("Detectando...", "Cargando URL..."):
                update_kwargs["empresa"] = scraped_empresa

            update_postulacion(postulacion_id, **update_kwargs)
            postulacion = get_postulacion(postulacion_id)
        except Exception as e:
            logger.warning(f"Error raspando oferta en segundo plano para {postulacion_id}: {e}")

    pipeline_start_time = time.time()
    _ETAPAS_PIPELINE.pop(postulacion_id, None)
    update_postulacion(postulacion_id, status="PROCESSING")
    await broadcast_event("status", {
        "id": postulacion_id,
        "status": "PROCESSING",
        "phase": 1,
        "total_phases": 3,
        "percent": 15,
        "agent": "ESTRATEGA",
        "agent_title": "Subagente 1: Estratega de Brechas",
        "step": "Analizando oferta y deduciendo dolores corporativos...",
        "detail": "Extrayendo requisitos excluyentes y deseables, mapeando competencias y calculando score de afinidad...",
        "elapsed_seconds": 0,
        "phase_elapsed_seconds": 0,
        "model": AntigravityBridge.active_model,
        "effort": "low",
    })

    try:
        # 1. Subagente Estratega
        await progreso.reportar("analizar")
        phase1_start = time.time()
        async with track_phase(
            postulacion_id=postulacion_id,
            phase=1,
            agent="ESTRATEGA",
            agent_title="Subagente 1: Estratega de Brechas",
            step="Analizando oferta y deduciendo dolores corporativos...",
            base_detail="Extrayendo requisitos excluyentes y deseables, mapeando competencias...",
            base_percent=15,
            target_percent=50,
            start_time=pipeline_start_time,
        ):
            strategist = StrategistAgent(bridge=AntigravityBridge(effort="low"), effort="low")
            analisis = await strategist.analyze_offer(postulacion["contenido_oferta"], **terceros)
            reqs = analisis.get("requisitos", [])
            analisis["matriz_oferta"] = extract_matriz_oferta(reqs)
            analisis["idioma_cv"] = idioma_cv_de_oferta(postulacion.get("contenido_oferta") or "")
        
        # Preservar título y empresa ingresados por el usuario si no son los valores por defecto o placeholders
        empresa_actual = (postulacion.get("empresa") or "").strip()
        titulo_actual = (postulacion.get("titulo") or "").strip()
        generic_companies = {"confidencial", "empresa confidencial", "detectando...", "cargando url...", "portal web externo", ""}
        generic_titles = {"posición técnica", "posicion tecnica", "posición por url", "posición importada", "oferta importada por url", "encolada...", ""}

        if empresa_actual.lower() in generic_companies and analisis.get("empresa"):
            final_empresa = analisis["empresa"]
        else:
            final_empresa = empresa_actual or analisis.get("empresa") or "Empresa Confidencial"

        if (titulo_actual.lower() in generic_titles or " en " in titulo_actual or " at " in titulo_actual) and analisis.get("puesto"):
            final_titulo = analisis["puesto"]
        else:
            final_titulo = titulo_actual or analisis.get("puesto") or "Posición Técnica"

        update_postulacion(
            postulacion_id,
            empresa=final_empresa,
            titulo=final_titulo,
            dolores_empresa=analisis.get("dolores_empresa", []),
            requisitos=reqs,
            match_score=analisis.get("match_score", 0.0),
            analisis=analisis,
        )

        # Hito verídico de Fase 1
        total_reqs = len(reqs)
        match_dir = len([r for r in reqs if r.get("estado") == "MATCH_DIRECTO"])
        brechas_count = len([r for r in reqs if r.get("estado") == "BRECHA_REAL"])
        milestone1_detail = f"Estratega completado: {total_reqs} requisitos detectados ({match_dir} directos, {brechas_count} brechas). Score de afinidad: {analisis.get('match_score', 0):.1f}%."
        await emit_milestone(
            postulacion_id=postulacion_id,
            phase=1,
            agent="ESTRATEGA",
            agent_title="Subagente 1: Estratega de Brechas",
            step="Análisis de oferta completado",
            detail=milestone1_detail,
            percent=48,
            start_time=pipeline_start_time,
            phase_start=phase1_start,
            metrics={
                "total_requisitos": total_reqs,
                "match_directo": match_dir,
                "brechas": brechas_count,
                "match_score": analisis.get("match_score", 0.0),
            },
        )

        await broadcast_event("status", {
            "id": postulacion_id,
            "status": "PROCESSING",
            "phase": 2,
            "total_phases": 3,
            "percent": 50,
            "agent": "REDACTOR",
            "agent_title": "Subagente 2: Redactor de Alto Impacto",
            "step": "Formulando CV Google XYZ y Pitch de Postulación...",
            "detail": "Redactando viñetas con logros cuantificados (X-Y-Z), adaptando tono ejecutivo y carta de presentación...",
            "elapsed_seconds": int(time.time() - pipeline_start_time),
            "phase_elapsed_seconds": 0,
            "model": AntigravityBridge.active_model,
            "effort": AntigravityBridge.active_effort,
        })

        # 2. CV desde el maestro: reemplaza al Redactor y al Auditor; sin pitch
        phase3_start = time.time()
        await _generar_desde_maestro(postulacion_id, analisis)
        final = get_postulacion(postulacion_id) or {}
        reporte = final.get("reporte_auditoria") or {}
        telemetry = final.get("telemetry") or {}

        score_ats = reporte.get("ats_score", 0) if isinstance(reporte, dict) else 0
        milestone3_detail = f"Auditoría completada: Score ATS final {score_ats}%. Documentación validada."
        await emit_milestone(
            postulacion_id=postulacion_id,
            phase=3,
            agent="AUDITOR",
            agent_title="Subagente 3: Auditor Ácido",
            step="Auditoría completada",
            detail=milestone3_detail,
            percent=98,
            start_time=pipeline_start_time,
            phase_start=phase3_start,
            metrics={
                "score_ats": score_ats,
                "reporte": reporte,
            },
        )

        await broadcast_event("complete", {
            "id": postulacion_id,
            "status": "READY",
            "phase": 4,
            "percent": 100,
            "elapsed_seconds": int(time.time() - pipeline_start_time),
            "score": analisis.get("match_score", 0.0),
            "telemetry": telemetry,
            "message": "¡Documentación generada con éxito!",
        })

    except Exception as e:
        if not isinstance(e, cv_maestro.CVNoAprobado):  # ese ya abrió su ticket con las líneas y el motivo
            tickets.reportar_error("generacion", "generacion_fallida", f"Falló la generación de la postulación {postulacion_id}: {e}",
                                   excepcion=e, datos={"oferta_id": postulacion_id})
        postu = get_postulacion(postulacion_id) or {}
        tel_existente = postu.get("telemetry")
        if isinstance(tel_existente, str):
            try:
                tel_existente = json.loads(tel_existente)
            except Exception:
                tel_existente = None
        telemetry = _telemetria_con_etapas(postulacion_id, (
            (tel_existente if isinstance(tel_existente, dict) else None)
            or _ultima_telemetria(writer if 'writer' in locals() else None)
            or _ultima_telemetria(auditor if 'auditor' in locals() else None)
            or {"model": AntigravityBridge.active_model, "status": "ERROR"}
        ))
        telemetry["status"] = "ERROR"
        update_postulacion(postulacion_id, status="ERROR", telemetry=telemetry)
        await broadcast_event("error", {"id": postulacion_id, "error": str(e)})


ORIGEN_CV_MAESTRO = "cv_maestro"


def _es_cv_maestro(postulacion: Dict[str, Any]) -> bool:
    telemetria = postulacion.get("telemetry")
    if isinstance(telemetria, str):
        try:
            telemetria = json.loads(telemetria)
        except Exception:
            telemetria = {}
    return isinstance(telemetria, dict) and telemetria.get("origen") == ORIGEN_CV_MAESTRO


def _idioma_del_cv(postulacion: Dict[str, Any], analisis: Dict[str, Any]) -> str:
    """El idioma sale siempre del texto de la oferta: el guardado en el análisis puede ser de un detector viejo
    (oferta 8201, ticket 186: negrita de Unicode leída como castellano). Sin texto queda el guardado."""
    texto = postulacion.get("contenido_oferta") or ""
    if texto.strip():
        return idioma_cv_de_oferta(texto)
    return analisis.get("idioma_cv") or idioma_cv_de_oferta("")


async def _generar_desde_maestro(postulacion_id: int, analisis: Dict[str, Any]) -> None:
    """CV desde el maestro (`core/cv_maestro.py`): título una sola vez, selección por código, una
    llamada de ajuste, auditoría independiente, una sola corrección y ticket si falla. En una generación del Radar
    el mail se compone en paralelo (no depende del CV). Deja la postulación READY o levanta la excepción (con
    `CVNoAprobado` la postulación queda en ERROR con el motivo y el ticket ya abierto). No genera pitch."""
    postulacion = get_postulacion(postulacion_id) or {}
    idioma = _idioma_del_cv(postulacion, analisis)
    candidato = DATA_DIR / "candidato"
    maestro = (candidato / "experiencia.txt").read_text(encoding="utf-8")
    personales = (candidato / "personales.txt").read_text(encoding="utf-8")
    fuente = ""
    # El CV final anterior deja de valer: el nuevo queda solo si la auditoría lo aprueba
    update_postulacion(postulacion_id, pitch="", pitches_multicanal={}, **cv_final.campos_vacios())
    ejecucion = _finalizacion_radar.get()
    en_radar = (ejecucion is not None and ejecucion["pid"] == postulacion_id
                and ejecucion["post"].get("estado") == "GENERANDO")
    tarea_mail = None
    snapshot = None

    snapshot_cv = [""]

    def componer_mail(cv_ajustado: str) -> None:
        # El mail toma su evidencia de las viñetas del CV: arranca con el CV ajustado, mientras se audita
        nonlocal tarea_mail, snapshot
        snapshot_cv[0] = cv_ajustado
        if not en_radar or tarea_mail is not None:
            return
        snapshot = {**(get_postulacion(postulacion_id) or postulacion), "cv_markdown": cv_ajustado,
                    "pitch": "", "pitches_multicanal": {}}
        tarea_mail = asyncio.create_task(_componer_mensaje_validado(ejecucion["post"], snapshot), name="mail-paralelo")

    inicio_ajuste = time.time()

    async def avisar_auditoria() -> None:
        # Hito del Redactor (fase 2) con el CV ajustado, y arranque de la fase del Auditor (fase 3)
        await emit_milestone(postulacion_id=postulacion_id, phase=2, agent="REDACTOR",
                             agent_title="Subagente 2: Redactor de Alto Impacto", step="CV ajustado desde el maestro",
                             detail="CV de 1 página ajustado a la oferta", percent=80, start_time=inicio_ajuste,
                             phase_start=inicio_ajuste, metrics={"cv_chars": len(snapshot_cv[0])})
        await broadcast_event("status", {
            "id": postulacion_id, "status": "PROCESSING", "phase": 3, "total_phases": 3, "percent": 85,
            "agent": "AUDITOR", "agent_title": "Subagente 3: Auditor Ácido",
            "step": "Auditando el CV ajustado desde el maestro...", "detail": "Controles independientes del CV",
            "model": AntigravityBridge.active_model, "effort": "low"})

    try:
        await progreso.reportar("redactar")
        if idioma == EN:
            fuente = maestro
            flujo_traduccion = cv_maestro.FlujoCV(maestro="", personales="", analisis={}, oferta="", titulo="")
            maestro, traducido = await cv_maestro.asegurar_maestro_en(
                candidato, lambda pedido: flujo_traduccion._ia_agy(pedido, effort=cv_maestro.EFFORT_TRADUCCION,
                                                                   timeout_s=cv_maestro.TIMEOUT_TRADUCCION_S))
            if traducido:
                logger.info("[CV] El maestro en inglés se tradujo de nuevo: queda pendiente de aprobación")
        # Una llamada por perfil (caché por huella de experiencia y personales): el primer CV después de cambiar el
        # perfil la recalcula, como avisa Mi perfil al guardar; con el caché vigente no llama a la IA
        areas = await asegurar_anios_por_area()
        decision = await decidir_titulo({**postulacion, "analisis": analisis}, ingles=idioma == EN)
        titulo = decision.titulo
        if idioma == EN:
            titulo = cv_maestro.titulo_en_ingles(titulo, fuente, maestro) or titulo
        flujo = cv_maestro.FlujoCV(
            maestro=maestro, maestro_fuente=fuente, personales=personales, analisis=analisis,
            oferta=postulacion.get("contenido_oferta") or "", titulo=titulo.upper(), idioma=idioma,
            post_id=_radar_post_pipeline.get(), nombre_pdf=f"cv_maestro_{postulacion_id}.pdf", anios_por_area=areas,
            al_ajustar=componer_mail, al_auditar=avisar_auditoria, estructura=cv_maestro.estructura_cv.leer(candidato),
            control_final=lambda cv: _motivos_texto_final(cv, "CV"))  # lo mismo que frena el envío
        try:
            resultado = await flujo.generar()
        except cv_maestro.CVNoAprobado as exc:
            reporte = {"approved": False, "origen": ORIGEN_CV_MAESTRO, "segundos": flujo.segundos,
                       "hallazgos_por_auditoria": [[f"{h.linea or 'general'}: {h.motivo}" for h in hs]
                                                   for hs in getattr(flujo, "historial", [])],
                       "hallazgos_unificados": {"lista_observaciones": [str(exc)], "requiere_accion": True}}
            update_postulacion(postulacion_id, reporte_auditoria=reporte, status="ERROR",
                               telemetry={"origen": ORIGEN_CV_MAESTRO, "status": "ERROR", "segundos": flujo.segundos})
            raise
        analisis_final = {**analisis, "idioma_cv": idioma, "keywords_cv": resultado.keywords_cv}
        if not resultado.keywords_cv:
            analisis_final.pop("keywords_cv", None)
        reporte = {**resultado.reporte, "origen": ORIGEN_CV_MAESTRO, "correcciones": resultado.correcciones,
                   "a_la_primera": resultado.correcciones == 0, "segundos": resultado.segundos,
                   # Para medir qué hace fallar la primera auditoría (objetivo: 95 % a la primera)
                   "hallazgos_por_auditoria": [[f"{h.linea or 'general'}: {h.motivo}" for h in hs]
                                               for hs in getattr(flujo, "historial", [])]}
        telemetry = {"origen": ORIGEN_CV_MAESTRO, "model": AntigravityBridge.active_model, "status": "OK",
                     "segundos": resultado.segundos, "correcciones": resultado.correcciones}
        # El PDF auditado es el CV final: el mismo archivo se descarga, se adjunta y se envía (huella SHA-256)
        final = cv_final.guardar(postulacion_id, resultado.pdf, resultado.markdown,
                                 generate_cv_pdf_filename(target_role=puesto_del_cv(resultado.markdown)
                                                                  or getattr(flujo, "titulo", titulo)))
        # El CV aprobado queda guardado aunque falle el mail (el post queda en ERROR con el motivo del mail)
        update_postulacion(postulacion_id, cv_markdown=resultado.markdown, pitch="", pitches_multicanal={},
                           reporte_auditoria=reporte, telemetry=telemetry, analisis=analisis_final, **final)
        if tarea_mail is not None:
            mensaje = await tarea_mail
            ejecucion["borrador"] = (snapshot, mensaje)
        update_postulacion(postulacion_id, status="READY")
    except cv_maestro.CVNoAprobado:
        raise
    except Exception as exc:
        # Falla de la IA (503, cuota, timeout) o del entorno: el motivo llega al Radar con su texto, para que reconozca
        # la saturación de AGY y reintente (como el «El Redactor no generó el CV» del flujo anterior)
        error = str(exc) if isinstance(exc, MensajeRequiereRevision) else f"{type(exc).__name__}: {exc}"
        update_postulacion(postulacion_id, telemetry={"origen": ORIGEN_CV_MAESTRO, "status": "ERROR",
                                                      "error": error[:500],
                                                      "mail": isinstance(exc, MensajeRequiereRevision)})
        raise
    finally:
        if tarea_mail is not None and not tarea_mail.done():
            tarea_mail.cancel()
            await asyncio.gather(tarea_mail, return_exceptions=True)


async def recalculate_cv_task(postulacion_id: int):
    """Regenera CV y Pitch preservando los tags modificados por el usuario."""
    postulacion = get_postulacion(postulacion_id)
    if not postulacion:
        return

    pipeline_start_time = time.time()
    _ETAPAS_PIPELINE.pop(postulacion_id, None)
    update_postulacion(postulacion_id, status="PROCESSING")
    await broadcast_event("status", {
        "id": postulacion_id,
        "status": "PROCESSING",
        "phase": 2,
        "total_phases": 3,
        "percent": 50,
        "agent": "REDACTOR",
        "agent_title": "Subagente 2: Redactor de Alto Impacto",
        "step": "Regenerando CV y Pitch con tags y competencias actuales...",
        "detail": "Reescribiendo viñetas Google XYZ y adecuando carta según los requisitos activados...",
        "elapsed_seconds": 0,
        "phase_elapsed_seconds": 0,
        "model": AntigravityBridge.active_model,
        "effort": AntigravityBridge.active_effort,
    })

    try:
        analisis_db = postulacion.get("analisis") or {}
        if isinstance(analisis_db, str):
            try:
                analisis_db = json.loads(analisis_db)
            except Exception:
                analisis_db = {}

        analisis_db = aplicar_aprendizajes_a_analisis(analisis_db)

        analisis = {
            **analisis_db,
            "puesto": postulacion.get("titulo") or analisis_db.get("puesto") or candidato_datos.puesto(),
            "empresa": postulacion.get("empresa") or analisis_db.get("empresa") or "Empresa Líder",
            "dolores_empresa": postulacion.get("dolores_empresa") or analisis_db.get("dolores_empresa", []),
            "requisitos": postulacion.get("requisitos") or analisis_db.get("requisitos", []),
            "match_score": postulacion.get("match_score", 0.0),
            "coincidencias": analisis_db.get("coincidencias", []),
            "transferibles": analisis_db.get("transferibles", []),
            "brechas": analisis_db.get("brechas", []),
        }
        analisis["idioma_cv"] = _idioma_del_cv(postulacion, analisis)
        # Coincidencias/transferibles que el usuario confirmó (acá o en otra oferta) llegan al Redactor
        if isinstance(analisis.get("requisitos"), list) and aplicar_aprendizajes_a_requisitos(analisis["requisitos"]):
            update_postulacion(postulacion_id, requisitos=analisis["requisitos"])

        # CV desde el maestro: reemplaza al Redactor y al Auditor; sin pitch
        phase3_start = time.time()
        await _generar_desde_maestro(postulacion_id, analisis)
        final = get_postulacion(postulacion_id) or {}
        reporte = final.get("reporte_auditoria") or {}
        telemetry = final.get("telemetry") or {}

        score_ats = reporte.get("ats_score", 0) if isinstance(reporte, dict) else 0
        milestone3_detail = f"Auditoría completada: Score ATS {score_ats}%. Requisitos sincronizados."
        await emit_milestone(
            postulacion_id=postulacion_id,
            phase=3,
            agent="AUDITOR",
            agent_title="Subagente 3: Auditor Ácido",
            step="Auditoría completada",
            detail=milestone3_detail,
            percent=98,
            start_time=pipeline_start_time,
            phase_start=phase3_start,
            metrics={
                "score_ats": score_ats,
                "reporte": reporte,
            },
        )

        await broadcast_event("complete", {
            "id": postulacion_id,
            "status": "READY",
            "phase": 4,
            "percent": 100,
            "elapsed_seconds": int(time.time() - pipeline_start_time),
            "score": analisis.get("match_score", 0.0),
            "telemetry": telemetry,
            "message": "¡CV y Pitch actualizados con éxito!",
        })

    except Exception as e:
        if not isinstance(e, cv_maestro.CVNoAprobado):  # ese ya abrió su ticket con las líneas y el motivo
            tickets.reportar_error("generacion", "recalculo_fallido", f"Falló la generación de la postulación {postulacion_id}: {e}",
                                   excepcion=e, datos={"oferta_id": postulacion_id})
        postu = get_postulacion(postulacion_id) or {}
        tel_existente = postu.get("telemetry")
        if isinstance(tel_existente, str):
            try:
                tel_existente = json.loads(tel_existente)
            except Exception:
                tel_existente = None
        telemetry = _telemetria_con_etapas(postulacion_id, (
            (tel_existente if isinstance(tel_existente, dict) else None)
            or _ultima_telemetria(writer if 'writer' in locals() else None)
            or _ultima_telemetria(auditor if 'auditor' in locals() else None)
            or {"model": AntigravityBridge.active_model, "status": "ERROR"}
        ))
        telemetry["status"] = "ERROR"
        update_postulacion(postulacion_id, status="ERROR", telemetry=telemetry)
        await broadcast_event("error", {"id": postulacion_id, "error": str(e)})


async def remediate_cv_task(postulacion_id: int):
    """Regenera el CV invocando exclusivamente al LLM guiado por las directivas de auditoría."""
    postulacion = get_postulacion(postulacion_id)
    if not postulacion:
        return
    if _es_cv_maestro(postulacion):
        # Un CV del maestro se corrige con su propio flujo
        await recalculate_cv_task(postulacion_id)
        return

    pipeline_start_time = time.time()
    _ETAPAS_PIPELINE.pop(postulacion_id, None)
    update_postulacion(postulacion_id, status="PROCESSING")
    await broadcast_event("status", {
        "id": postulacion_id,
        "status": "PROCESSING",
        "phase": 3,
        "total_phases": 3,
        "percent": 85,
        "agent": "AUDITOR",
        "agent_title": "Subagente 3: Auditor Ácido",
        "step": "Regenerando CV con directivas específicas de auditoría vía LLM...",
        "detail": "Subsanando brechas, incorporando keywords aprobadas y puliendo el documento vía LLM...",
        "elapsed_seconds": 0,
        "phase_elapsed_seconds": 0,
        "model": AntigravityBridge.active_model,
        "effort": "low",
    })

    try:
        analisis_db = postulacion.get("analisis") or {}
        if isinstance(analisis_db, str):
            try:
                analisis_db = json.loads(analisis_db)
            except Exception:
                analisis_db = {}
        analisis_db = aplicar_aprendizajes_a_analisis(analisis_db)
        coincidencias = analisis_db.get("coincidencias", [])
        transferibles = analisis_db.get("transferibles", [])

        reqs = postulacion.get("requisitos") or []
        keywords_aprobadas = [
            r["nombre"] for r in reqs
            if r.get("approved", r.get("estado") == "MATCH_DIRECTO")
        ]
        for item in coincidencias + transferibles:
            name = item if isinstance(item, str) else item.get("nombre")
            if name and name not in keywords_aprobadas:
                keywords_aprobadas.append(name)

        if not keywords_aprobadas:
            keywords_aprobadas = [r["nombre"] for r in reqs if r.get("estado") != "BRECHA_REAL"] or [r["nombre"] for r in reqs]
        keywords_aprobadas = _keywords_del_cv(keywords_aprobadas, analisis_db)

        auditor = AuditorAgent(bridge=AntigravityBridge(effort="low"), effort="low")
        redaccion = _normalizar_redaccion(postulacion)
        cv_current = redaccion["cv_markdown"]
        pitch_current = redaccion["pitch"]
        pitches_current = redaccion["pitches_multicanal"]

        phase3_start = time.time()
        async with track_phase(
            postulacion_id=postulacion_id,
            phase=3,
            agent="AUDITOR",
            agent_title="Subagente 3: Auditor Ácido",
            step="Regenerando CV con directivas específicas de auditoría vía LLM...",
            base_detail="Subsanando brechas y remediando documento vía LLM...",
            base_percent=85,
            target_percent=98,
            start_time=pipeline_start_time,
        ):
            if hasattr(auditor, "audit_and_remediate_all"):
                try:
                    res = await auditor.audit_and_remediate_all(
                        cv_current,
                        pitch_current,
                        pitches_current,
                        keywords=keywords_aprobadas,
                        max_attempts=INTENTOS_REMEDIACION_CV,
                    )
                    if isinstance(res, (tuple, list)) and len(res) == 4:
                        cv_clean, pitch_clean, pitches_clean, reporte = res
                    else:
                        cv_clean, pitch_clean, reporte = await auditor.audit_and_remediate(
                            cv_current, pitch_current, keywords_aprobadas, max_attempts=INTENTOS_REMEDIACION_CV
                        )
                        pitches_clean = pitches_current
                except Exception:
                    cv_clean, pitch_clean, reporte = await auditor.audit_and_remediate(
                        cv_current, pitch_current, keywords_aprobadas, max_attempts=INTENTOS_REMEDIACION_CV
                    )
                    pitches_clean = pitches_current
            else:
                cv_clean, pitch_clean, reporte = await auditor.audit_and_remediate(
                    cv_current, pitch_current, keywords_aprobadas, max_attempts=INTENTOS_REMEDIACION_CV
                )
                pitches_clean = pitches_current

        cv_clean, pitch_clean, pitches_clean, incompleto, reporte = await _finalizar_cv(
            postulacion_id, cv_clean, pitch_clean, pitches_clean, reporte, keywords_aprobadas,
            hallazgos_por_etapa=getattr(auditor, "hallazgos_por_etapa", None))
        telemetry = _telemetria_con_etapas(postulacion_id, (
            auditor.bridge.last_telemetry
            or {"model": AntigravityBridge.active_model, "status": "REMEDIATED_LLM"}
        ))
        if incompleto:
            telemetry["incompleto"] = incompleto

        update_postulacion(
            postulacion_id,
            cv_markdown=cv_clean,
            pitch=pitch_clean,
            pitches_multicanal=pitches_clean,
            reporte_auditoria=reporte,
            telemetry=telemetry,
            status="READY",
        )

        score_ats = reporte.get("score_ats", 0) if isinstance(reporte, dict) else 0
        milestone3_detail = f"Auditoría completada: Score ATS final {score_ats}%. Documento corregido."
        await emit_milestone(
            postulacion_id=postulacion_id,
            phase=3,
            agent="AUDITOR",
            agent_title="Subagente 3: Auditor Ácido",
            step="Remediación completada",
            detail=milestone3_detail,
            percent=98,
            start_time=pipeline_start_time,
            phase_start=phase3_start,
            metrics={
                "score_ats": score_ats,
                "reporte": reporte,
            },
        )

        await broadcast_event("complete", {
            "id": postulacion_id,
            "status": "READY",
            "phase": 4,
            "percent": 100,
            "elapsed_seconds": int(time.time() - pipeline_start_time),
            "score": postulacion.get("match_score", 0.0),
            "telemetry": telemetry,
            "message": "¡CV corregido conforme a las directivas de auditoría!",
        })

    except Exception as e:
        tickets.reportar_error("generacion", "remediacion_fallida", f"Falló la generación de la postulación {postulacion_id}: {e}",
                               excepcion=e, datos={"oferta_id": postulacion_id})
        postu = get_postulacion(postulacion_id) or {}
        tel_existente = postu.get("telemetry")
        if isinstance(tel_existente, str):
            try:
                tel_existente = json.loads(tel_existente)
            except Exception:
                tel_existente = None
        telemetry = _telemetria_con_etapas(postulacion_id, (
            (tel_existente if isinstance(tel_existente, dict) else None)
            or _ultima_telemetria(writer if 'writer' in locals() else None)
            or _ultima_telemetria(auditor if 'auditor' in locals() else None)
            or {"model": AntigravityBridge.active_model, "status": "ERROR"}
        ))
        telemetry["status"] = "ERROR"
        update_postulacion(postulacion_id, status="ERROR", telemetry=telemetry)
        await broadcast_event("error", {"id": postulacion_id, "error": str(e)})


# Modelos Pydantic
class OfertaIn(BaseModel):
    titulo: Optional[str] = "Posición Técnica"
    empresa: Optional[str] = "Confidencial"
    contenido: str
    url: Optional[str] = ""


class URLIn(BaseModel):
    url: str


class BatchURLsIn(BaseModel):
    urls: List[str]


class ToggleTagIn(BaseModel):
    tag_index: int


class ModelConfigIn(BaseModel):
    model: str
    effort: Optional[str] = None


class LifecycleIn(BaseModel):
    estado_postulacion: str  # 'POR_POSTULAR' | 'POSTULADA' | 'EN_ENTREVISTA' | 'OFERTA' | 'DESESTIMADA'
    fecha_postulacion: Optional[str] = None
    link_postulacion: Optional[str] = ""
    notas_seguimiento: Optional[str] = ""
    contacto_recruiter: Optional[str] = ""


def _crear_post_manual(texto: str, url: str = "", titulo: str = "", empresa: str = "") -> dict:
    """Inserta o recupera una oferta manual, sin iniciar scraping ni generar CV en la request."""
    existente = radar_store.post_manual_existente(texto, url)
    post_id = existente or radar_store.insert_post_manual(texto, url, titulo, empresa)
    post = radar_store.get_post(post_id)
    titulo = post.get("cargo_autor") or "Oferta manual"
    empresa = post.get("autor") or "Oferta manual"
    return {
        "id": post_id, "post_id": post_id, "estado": "NUEVO", "status": "PROCESSING",
        "titulo": titulo, "empresa": empresa, "puesto": titulo,
        "url": post.get("post_url") or "", "duplicado": existente is not None,
    }


@app.post("/api/ofertas", dependencies=_GUARDA)
async def api_crear_oferta(oferta: OfertaIn):
    if not oferta.contenido.strip() and not (oferta.url or "").strip():
        raise HTTPException(status_code=400, detail="Contenido de la oferta requerido")
    respuesta = _crear_post_manual(
        oferta.contenido, (oferta.url or "").strip(),
        (oferta.titulo or "").strip(), (oferta.empresa or "").strip(),
    )
    await radar_loop.wake_processing()
    return respuesta


@app.post("/api/ofertas/url", dependencies=_GUARDA)
async def api_crear_oferta_url(payload: URLIn):
    url = payload.url.strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="URL inválida (debe iniciar con http:// o https://)")
    respuesta = _crear_post_manual(url, url, "Oferta por URL")
    await radar_loop.wake_processing()
    return respuesta


@app.post("/api/ofertas/batch", dependencies=_GUARDA)
async def api_crear_ofertas_batch(payload: BatchURLsIn):
    """Encola las URLs válidas en el mismo motor que las demás ofertas manuales."""
    urls = [u.strip() for u in payload.urls if u.strip().startswith(("http://", "https://"))]
    if not urls:
        raise HTTPException(status_code=400, detail="No se suministraron URLs válidas (deben iniciar con http:// o https://)")
    respuestas = [_crear_post_manual(url, url, "Oferta por URL") for url in urls]
    ids = [r["id"] for r in respuestas]
    await radar_loop.wake_processing()
    return {"created": sum(not r["duplicado"] for r in respuestas), "total": len(ids),
            "ids": ids, "status": "PROCESSING"}


@app.get("/api/ofertas", dependencies=_GUARDA)
def api_listar_ofertas(filtro_lifecycle: Optional[str] = None, origen: str = "manual"):
    """Lista las ofertas almacenadas ordenadas por match_score descendente con soporte para filtro de ciclo de vida.
    `origen=radar`: las postulaciones del Radar sin post (el historial de la vista Radar)."""
    if origen not in ("manual", "radar"):
        raise HTTPException(status_code=422, detail="Origen inválido. Válidos: ['manual', 'radar']")
    if origen == "radar":
        ofertas = list_postulaciones(filtro_lifecycle=filtro_lifecycle, origen="linkedin", sin_post=True)
    else:
        ofertas = list_postulaciones(filtro_lifecycle=filtro_lifecycle, origen="manual")
    for of in ofertas:
        raw_c = of.get("contenido_oferta") or ""
        of["contenido_limpio"] = filter_job_boilerplate(raw_c)
    return ofertas


@app.post("/api/ofertas/{postulacion_id}/lifecycle", dependencies=_GUARDA)
async def api_actualizar_lifecycle(postulacion_id: int, payload: LifecycleIn):
    """Actualiza el estado de ciclo de vida y seguimiento de la postulación."""
    postulacion = get_postulacion(postulacion_id)
    if not postulacion:
        raise HTTPException(status_code=404, detail="Oferta no encontrada")

    try:
        actualizado = update_postulacion_lifecycle(
            postulacion_id=postulacion_id,
            estado=payload.estado_postulacion,
            fecha=payload.fecha_postulacion,
            link=payload.link_postulacion or "",
            notas=payload.notas_seguimiento or "",
            contacto=payload.contacto_recruiter or "",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not actualizado:
        raise HTTPException(status_code=404, detail="Oferta no encontrada")

    await broadcast_event("lifecycle_updated", {
        "id": postulacion_id,
        "estado_postulacion": actualizado.get("estado_postulacion"),
        "fecha_postulacion": actualizado.get("fecha_postulacion"),
        "link_postulacion": actualizado.get("link_postulacion"),
        "notas_seguimiento": actualizado.get("notas_seguimiento"),
        "contacto_recruiter": actualizado.get("contacto_recruiter"),
        "oferta": actualizado,
    })

    return actualizado


@app.get("/api/ofertas/{postulacion_id}", dependencies=_GUARDA)
def api_detalle_oferta(postulacion_id: int):
    """Detalle completo de una oferta y sus activos generados."""
    postulacion = get_postulacion(postulacion_id)
    if not postulacion:
        raise HTTPException(status_code=404, detail="Oferta no encontrada")

    reqs = postulacion.get("requisitos", [])
    analisis = postulacion.get("analisis") or {}
    if not isinstance(analisis, dict):
        analisis = {}
    if "matriz_oferta" not in analisis or not analisis["matriz_oferta"]:
        analisis["matriz_oferta"] = extract_matriz_oferta(reqs)
    analisis["requisitos"] = reqs

    if "desglose_afinidad" not in analisis or not analisis["desglose_afinidad"]:
        from core.strategist import calculate_match_score_with_breakdown
        _, desglose = calculate_match_score_with_breakdown(reqs)
        analisis["desglose_afinidad"] = desglose
    postulacion["analisis"] = analisis
    postulacion["desglose_afinidad"] = analisis.get("desglose_afinidad")

    # no se generan pitch ni pitches multicanal (tampoco al responder el detalle)

    raw_content = postulacion.get("contenido_oferta") or ""
    postulacion["contenido_limpio"] = filter_job_boilerplate(raw_content)

    rep = postulacion.get("reporte_auditoria") or {}
    if isinstance(rep, str):
        try:
            rep = json.loads(rep)
        except Exception:
            rep = {}
    tel = postulacion.get("telemetry") or {}
    if isinstance(tel, str):
        try:
            tel = json.loads(tel)
        except Exception:
            tel = {}
    is_fb = bool(postulacion.get("is_fallback") or rep.get("is_fallback") or tel.get("is_fallback") or tel.get("status") == "DETERMINISTIC_FALLBACK")
    postulacion["is_fallback"] = is_fb
    postulacion["fallback_reason"] = rep.get("fallback_reason") or tel.get("fallback_reason")

    return postulacion


@app.delete("/api/ofertas/{postulacion_id}", dependencies=_GUARDA)
def api_delete_oferta(postulacion_id: int):
    """Elimina definitivamente una oferta de la base de datos sin dejar rastro."""
    deleted = delete_postulacion(postulacion_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Oferta no encontrada")
    return {"status": "DELETED", "id": postulacion_id}



@app.post("/api/ofertas/{postulacion_id}/tags/{tag_name}/cycle-state", dependencies=_GUARDA)
def api_cycle_tag_state(postulacion_id: int, tag_name: str):
    """Cicla el estado de un tag o requerimiento: BRECHA_REAL -> TRANSFERIBLE -> MATCH_DIRECTO -> BRECHA_REAL."""
    postulacion = get_postulacion(postulacion_id)
    if not postulacion:
        raise HTTPException(status_code=404, detail="Oferta no encontrada")

    analisis = postulacion.get("analisis") or {}
    if isinstance(analisis, str):
        try:
            analisis = json.loads(analisis)
        except Exception:
            analisis = {}

    coincidencias = list(analisis.get("coincidencias") or [])
    transferibles = list(analisis.get("transferibles") or [])
    brechas = list(analisis.get("brechas") or [])

    tag_clean = tag_name.strip()
    if not tag_clean:
        raise HTTPException(status_code=400, detail="Falta el nombre del requisito")
    tag_lower = tag_clean.lower()

    def find_in_list(lst):
        for idx, item in enumerate(lst):
            name = item if isinstance(item, str) else item.get("nombre", "")
            if name.strip().lower() == tag_lower:
                return idx, (item if isinstance(item, str) else name)
        return -1, None

    c_idx, c_orig = find_in_list(coincidencias)
    t_idx, t_orig = find_in_list(transferibles)
    b_idx, b_orig = find_in_list(brechas)

    display_name = c_orig or t_orig or b_orig or tag_clean

    if c_idx != -1:
        # Currently MATCH_DIRECTO -> cycle to BRECHA_REAL
        estado_anterior = "MATCH_DIRECTO"
        nuevo_estado = "BRECHA_REAL"
        coincidencias.pop(c_idx)
        brechas.append(display_name)
    elif t_idx != -1:
        # Currently TRANSFERIBLE -> cycle to MATCH_DIRECTO
        estado_anterior = "TRANSFERIBLE"
        nuevo_estado = "MATCH_DIRECTO"
        transferibles.pop(t_idx)
        coincidencias.append(display_name)
    else:
        # Currently BRECHA_REAL or new -> cycle to TRANSFERIBLE
        estado_anterior = "BRECHA_REAL"
        nuevo_estado = "TRANSFERIBLE"
        if b_idx != -1:
            brechas.pop(b_idx)
        transferibles.append(display_name)

    analisis["coincidencias"] = coincidencias
    analisis["transferibles"] = transferibles
    analisis["brechas"] = brechas

    requisitos = postulacion.get("requisitos") or analisis.get("requisitos")
    if isinstance(requisitos, str):
        try:
            requisitos = json.loads(requisitos)
        except Exception:
            requisitos = []
    if isinstance(requisitos, dict) and "requisitos" in requisitos:
        requisitos = requisitos["requisitos"]
    if isinstance(requisitos, list):
        for req in requisitos:
            if isinstance(req, dict) and req.get("nombre", "").strip().lower() == tag_lower:
                req["estado"] = nuevo_estado
                if nuevo_estado == "MATCH_DIRECTO":
                    req["approved"] = True
                elif nuevo_estado == "BRECHA_REAL":
                    req["approved"] = False
                elif nuevo_estado == "TRANSFERIBLE":
                    req["approved"] = True

    if "matriz_oferta" in analisis and isinstance(analisis["matriz_oferta"], dict):
        for cat in ("herramientas", "metodologias", "responsabilidades", "otros"):
            for item in analisis["matriz_oferta"].get(cat, []):
                if isinstance(item, dict) and item.get("nombre", "").strip().lower() == tag_lower:
                    item["estado"] = nuevo_estado
                    item["approved"] = (nuevo_estado != "BRECHA_REAL")

    if isinstance(requisitos, list) and len(requisitos) > 0:
        analisis["matriz_oferta"] = extract_matriz_oferta(requisitos)

    from core.strategist import calculate_match_score_with_breakdown
    if isinstance(requisitos, list) and len(requisitos) > 0:
        new_score, desglose = calculate_match_score_with_breakdown(requisitos)
    else:
        new_score = float(postulacion.get("match_score", 0.0) or 0.0)
        desglose = {
            "puntos_faltantes": round(max(0.0, 100.0 - new_score), 1),
            "brechas": [],
            "transferibles": [],
            "camino_al_100": "Perfil 100% alineado con los requerimientos." if new_score >= 100.0 else ""
        }
    analisis["match_score"] = new_score
    analisis["desglose_afinidad"] = desglose
    analisis["requisitos"] = requisitos

    update_postulacion(postulacion_id, analisis=analisis, requisitos=requisitos, match_score=new_score)

    registrar_aprendizaje(display_name, nuevo_estado)
    aprendizajes = obtener_aprendizajes()

    return {
        "ok": True,
        "tag": display_name,
        "anterior_estado": estado_anterior,
        "nuevo_estado": nuevo_estado,
        "aprendizajes_totales": len(aprendizajes),
        "aprendizajes": aprendizajes,
        "analisis": analisis,
        "match_score": new_score,
        "desglose_afinidad": desglose,
        "requisitos": requisitos,
    }


@app.get("/api/candidato/puntaje-bajo", dependencies=_GUARDA)
def api_get_puntaje_bajo():
    """Lo que tienen en común las ofertas con puntaje bajo y no tienen las demás (Mi perfil; solo informa)."""
    import json as _json
    from core import aprendizaje_puntaje, radar_store
    ruta = DATA_DIR / "candidato" / aprendizaje_puntaje.ARCHIVO
    try:
        config = _json.loads(ruta.read_text(encoding="utf-8")) if ruta.exists() else None
    except ValueError:
        config = {"error": "no es un JSON válido"}
    return aprendizaje_puntaje.para_perfil(config, radar_store.list_posts())


@app.get("/api/candidato/aprendizajes", dependencies=_GUARDA)
def api_get_aprendizajes():
    """Retorna las competencias y estados aprendidos del candidato."""
    return {"aprendizajes": obtener_aprendizajes()}


class PerfilIn(BaseModel):
    contenido: StrictStr
    version: StrictStr


@app.get("/api/candidato/perfil/{clave}", dependencies=_GUARDA)
def api_get_perfil(clave: str):
    try:
        return perfil_candidato.leer_perfil(clave)
    except perfil_candidato.PerfilError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from e


@app.put("/api/candidato/perfil/{clave}", dependencies=_GUARDA)
def api_put_perfil(clave: str, payload: PerfilIn):
    try:
        return perfil_candidato.guardar_perfil(clave, payload.contenido, payload.version)
    except perfil_candidato.PerfilError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from e


@app.get("/api/candidato/busquedas", dependencies=_GUARDA)
def api_get_busquedas():
    """Las búsquedas del Radar (busquedas.json) con el promedio por lectura de las que ya se leyeron."""
    from core import radar_resultados
    return radar_resultados.busquedas_para_perfil()


class BusquedasIn(BaseModel):
    busquedas: List[Dict[str, Any]]
    version: StrictStr


@app.put("/api/candidato/busquedas", dependencies=_GUARDA)
def api_put_busquedas(payload: BusquedasIn):
    """Rediseño E6: la lista entera de búsquedas (nombre, activa, bloques o consulta, orden, portales y palabras),
    con versión, respaldo y la validación del Radar."""
    try:
        return perfil_candidato.guardar_busquedas(payload.busquedas, payload.version)
    except perfil_candidato.PerfilError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from e


class PortalesBusquedasIn(BaseModel):
    cambios: List[Dict[str, Any]]
    version: StrictStr


@app.put("/api/candidato/busquedas/portales", dependencies=_GUARDA)
def api_put_portales_busquedas(payload: PortalesBusquedasIn):
    """Portales: casillas de portales y palabras simples de cada búsqueda. El resto del archivo no cambia."""
    try:
        return perfil_candidato.guardar_portales_de_busquedas(payload.cambios, payload.version)
    except perfil_candidato.PerfilError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from e


@app.get("/api/portales/estado", dependencies=_GUARDA)
def api_portales_estado():
    """Portales: por portal, si corre (o por qué no), sus consultas de hoy y sus búsquedas; y las últimas
    consultas. Solo lectura."""
    from datetime import datetime as _dt
    from core import portales_store
    from core.radar_consultas import busquedas_de_portal
    from core.reglas_portales import NOMBRES, PORTALES, leer_reglas_portales

    validas, invalidas = leer_reglas_portales()
    pausados = portales_store.pausados()
    medianoche = portales_store.ahora_iso(_dt.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0))
    salida = []
    for portal in PORTALES:
        regla = validas.get(portal)
        salida.append({
            "portal": portal, "nombre": NOMBRES[portal],
            "corre": bool(regla and regla.activo), "motivo": invalidas.get(portal) or ("" if regla is None or regla.activo
                                                                                       else "Apagado en las reglas"),
            "consultas_hoy": portales_store.consultas_desde(portal, medianoche),
            "consultas_por_dia": regla.consultas_por_dia if regla else None,
            "ultima": portales_store.ultima_consulta(portal),
            "busquedas": [b["nombre"] for b in busquedas_de_portal(portal)],
            "modo": regla.modo if regla else None,
            "pausado": portal in pausados, "motivo_pausa": (pausados.get(portal) or {}).get("motivo", ""),
        })
    return {"portales": salida, "lecturas": portales_store.lecturas(20)}


class PausaPortalIn(BaseModel):
    pausado: StrictBool


@app.post("/api/portales/{portal}/pausa", dependencies=_GUARDA)
def api_portal_pausa(portal: str, payload: PausaPortalIn):
    """Pausa o reanuda el radar de un portal (panel de procesos). Reanudar también levanta la pausa que puso
    el radar solo cuando el portal pidió una verificación."""
    from core import portales_store
    from core.reglas_portales import PORTALES
    if portal not in PORTALES:
        raise HTTPException(status_code=404, detail="Portal desconocido")
    if payload.pausado:
        portales_store.pausar(portal)
    else:
        portales_store.reanudar(portal)
    return api_portales_estado()


@app.get("/api/candidato/anios-por-area", dependencies=_GUARDA)
def api_get_anios_por_area():
    try:
        return perfil_candidato.leer_anios_por_area()
    except perfil_candidato.PerfilError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from e


@app.delete("/api/candidato/aprendizajes/{clave:path}", dependencies=_GUARDA)
def api_delete_aprendizaje(clave: str):
    try:
        return perfil_candidato.borrar_aprendizaje(clave)
    except perfil_candidato.PerfilError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from e


@app.get("/api/model-info", dependencies=_GUARDA)
def api_get_model_info():
    """Retorna información y evidencia real del modelo de Antigravity configurado y activo."""
    bridge = AntigravityBridge()
    return bridge.get_model_info()


@app.post("/api/model-config", dependencies=_GUARDA)
async def api_set_model_config(payload: ModelConfigIn):
    """Actualiza dinámicamente el modelo y nivel de razonamiento activo en el sistema."""
    bridge = AntigravityBridge()
    info = bridge.get_model_info()
    available = info.get("available_models", [])

    requested = payload.model.strip()
    valid_options = set(available + [
        "gemini-3.8-flash-high",
        "gemini-3.8-flash-medium",
        "gemini-3.8-flash-low",
        "claude-sonnet-4-6",
        "claude-opus-4-6-thinking",
        "gpt-oss-120b-medium",
    ])

    if requested not in valid_options:
        raise HTTPException(
            status_code=400,
            detail=f"Modelo no soportado: '{requested}'. Opciones válidas: {', '.join(sorted(valid_options))}"
        )

    bridge.set_active_model(requested, effort=payload.effort)
    new_info = bridge.get_model_info()

    await broadcast_event("model_changed", new_info)

    return {
        "status": "MODEL_UPDATED",
        "model": new_info["configured_model"],
        "display_name": new_info["display_name"],
        "effort": new_info["effort"],
    }


@app.post("/api/ofertas/{postulacion_id}/toggle-tag", dependencies=_GUARDA)
def api_toggle_tag(postulacion_id: int, payload: ToggleTagIn):
    """Activa o desactiva un tag recalculando el match score en tiempo real."""
    actualizado = toggle_tag(postulacion_id, payload.tag_index)
    if not actualizado:
        raise HTTPException(status_code=404, detail="Oferta o tag inválido")
    return actualizado


@app.post("/api/ofertas/{postulacion_id}/regenerate", dependencies=_GUARDA)
async def api_regenerar(postulacion_id: int, background_tasks: BackgroundTasks):
    """Re-analiza completamente la oferta laboral (pautas, requerimientos, puntaje, CV y pitch)."""
    postulacion = get_postulacion(postulacion_id)
    if not postulacion:
        raise HTTPException(status_code=404, detail="Oferta no encontrada")
    update_postulacion(postulacion_id, status="PROCESSING")
    background_tasks.add_task(run_pipeline_task, postulacion_id)
    return {"status": "REGENERATING", "id": postulacion_id}


class FormatoCvIn(BaseModel):
    formato: Optional[str] = None


@app.post("/api/ofertas/{postulacion_id}/recalculate-cv", dependencies=_GUARDA)
async def api_recalcular_cv(postulacion_id: int, background_tasks: BackgroundTasks,
                            payload: Optional[FormatoCvIn] = None):
    """Regenera CV y Pitch preservando los tags seleccionados por el usuario. Con `formato`
    («completo» o «una_pagina») cambia antes el formato del CV de la postulación."""
    postulacion = get_postulacion(postulacion_id)
    if not postulacion:
        raise HTTPException(status_code=404, detail="Oferta no encontrada")
    formato = payload.formato if payload else None
    if formato is not None and formato not in FORMATOS_CV:
        raise HTTPException(status_code=422, detail=f"Formato inválido: {formato}")
    if formato:
        update_postulacion(postulacion_id, formato_cv=formato)
    update_postulacion(postulacion_id, status="PROCESSING")
    background_tasks.add_task(recalculate_cv_task, postulacion_id)
    return {"status": "RECALCULATING_CV", "id": postulacion_id}


@app.post("/api/ofertas/{postulacion_id}/remediar", dependencies=_GUARDA)
async def api_remediar_oferta(postulacion_id: int, background_tasks: BackgroundTasks):
    """Dispara la regeneración del CV asistida por LLM con las directivas concretas de auditoría."""
    postulacion = get_postulacion(postulacion_id)
    if not postulacion:
        raise HTTPException(status_code=404, detail="Oferta no encontrada")
    update_postulacion(postulacion_id, status="PROCESSING")
    background_tasks.add_task(remediate_cv_task, postulacion_id)
    return {"status": "REMEDIATING", "id": postulacion_id, "message": "Remediación LLM iniciada"}


def _extract_postulacion_keywords(postulacion: dict) -> list[str]:
    """Extrae palabras clave de los requisitos aprobados o del análisis de la oferta para metadatos ATS."""
    raw_reqs = postulacion.get("requisitos")
    if not raw_reqs and isinstance(postulacion.get("analisis"), dict):
        raw_reqs = postulacion["analisis"].get("requisitos")
    keywords = []
    if isinstance(raw_reqs, list):
        for r in raw_reqs:
            if isinstance(r, dict):
                if r.get("approved") is False:
                    continue
                if r.get("approved") is None and r.get("estado") == "BRECHA_REAL":
                    continue
                name = r.get("nombre")
                if name and name.strip():
                    keywords.append(name.strip())
            elif isinstance(r, str) and r.strip():
                keywords.append(r.strip())
    return _keywords_del_cv(list(dict.fromkeys(keywords)), postulacion.get("analisis"))


def _puesto_export(postulacion: Dict[str, Any]) -> str:
    """Puesto del header y del nombre del archivo: el que escribió el CV (el rol con el que se
    presenta el candidato), no el título de la oferta ("Convocatoria abierta…")."""
    return puesto_del_cv(postulacion.get("cv_markdown") or "") or postulacion.get("titulo") or ""


def _cv_final_verificado(postulacion: Dict[str, Any], accion: str) -> Optional[Path]:
    """El CV final (el PDF auditado, `core/cv_final.py`) comprobado por su huella; None si el CV es del flujo
    anterior al maestro. Si no coincide, ticket y 409: nunca se sirve otro archivo en su lugar."""
    try:
        return cv_final.verificar(postulacion)
    except cv_final.CVFinalInvalido as exc:
        tickets.reportar_error(componente="cv", tipo="cv_final_invalido", mensaje=f"{accion}: {exc}",
                               datos={"postulacion_id": postulacion.get("id")})
        raise HTTPException(status_code=409, detail=f"El CV no es el que aprobó la auditoría ({exc}): regeneralo")


@app.get("/api/ofertas/{postulacion_id}/export/docx", dependencies=_GUARDA)
def api_exportar_docx(postulacion_id: int):
    """Compila el CV actual a formato DOCX ejecutivo y lo descarga (si tiene CV final, solo con el texto auditado)."""
    postulacion = get_postulacion(postulacion_id)
    if not postulacion or not postulacion.get("cv_markdown"):
        raise HTTPException(status_code=404, detail="CV no disponible para exportar")
    _cv_final_verificado(postulacion, "Descarga DOCX")

    keywords = _extract_postulacion_keywords(postulacion)
    filename = generate_cv_filename(target_role=_puesto_export(postulacion))
    file_path = compile_markdown_to_docx(
        postulacion["cv_markdown"],
        target_role=_puesto_export(postulacion),
        output_filename=filename,
        keywords=keywords
    )

    return FileResponse(
        path=str(file_path),
        filename=filename,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@app.get("/api/ofertas/{postulacion_id}/export/pdf", dependencies=_GUARDA)
def api_exportar_pdf(postulacion_id: int):
    """Descarga el CV final (el mismo PDF que aprobó la auditoría); un CV del flujo anterior se compila como antes."""
    postulacion = get_postulacion(postulacion_id)
    if not postulacion or not postulacion.get("cv_markdown"):
        raise HTTPException(status_code=404, detail="CV no disponible para exportar")
    final = _cv_final_verificado(postulacion, "Descarga PDF")
    if final is not None:
        return FileResponse(path=str(final), filename=final.name, media_type="application/pdf",
                            headers={"Content-Disposition": f'attachment; filename="{final.name}"'})

    keywords = _extract_postulacion_keywords(postulacion)
    filename = generate_cv_pdf_filename(target_role=_puesto_export(postulacion))
    file_path = compile_markdown_to_pdf(
        postulacion["cv_markdown"],
        target_role=_puesto_export(postulacion),
        output_filename=filename,
        keywords=keywords
    )

    return FileResponse(
        path=str(file_path),
        filename=filename,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@app.get("/api/stream", dependencies=_GUARDA)
async def api_stream(request: Request):
    """Canal Server-Sent Events (SSE) para progreso y notificaciones en tiempo real."""
    queue = asyncio.Queue()
    _LOOPS_SSE[queue] = asyncio.get_running_loop()
    subscribers.append(queue)

    async def event_generator():
        try:
            # Ping inicial de conexión
            yield "event: connected\ndata: {\"status\": \"connected\"}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                message = await queue.get()
                yield message
        except asyncio.CancelledError:
            pass
        finally:
            if queue in subscribers:
                subscribers.remove(queue)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# Modelos Pydantic para Módulo expand
class ExpandAskIn(BaseModel):
    target_role: Optional[str] = ""
    missing_keywords: List[str] = []


class ExpandConfirmIn(BaseModel):
    competencias: List[Dict[str, Any]] = []


@app.get("/api/candidato/expand", dependencies=_GUARDA)
def api_expand_get():
    """Retorna el listado de competencias expandidas validadas del candidato."""
    return {"competencias": expander_agent.get_expanded_competencies()}


@app.post("/api/candidato/expand/ask", dependencies=_GUARDA)
async def api_expand_ask(payload: ExpandAskIn):
    """Genera preguntas socráticas asistidas por IA para descubrir competencias latentes frente a brechas."""
    preguntas = await expander_agent.generate_discovery_questions(
        payload.target_role or "",
        payload.missing_keywords
    )
    return {"preguntas": preguntas}


@app.post("/api/candidato/expand/confirm", dependencies=_GUARDA)
def api_expand_confirm(payload: ExpandConfirmIn):
    """Guarda y valida competencias expandidas confirmadas en el perfil del candidato."""
    actualizadas = expander_agent.confirm_competencies(payload.competencias)
    return {"competencias": actualizadas, "status": "SAVED"}


@app.delete("/api/candidato/expand/{index}", dependencies=_GUARDA)
def api_expand_delete(index: int):
    """Elimina una competencia expandida por índice."""
    eliminado = expander_agent.remove_competency(index)
    if not eliminado:
        raise HTTPException(status_code=404, detail="Competencia no encontrada")
    return {"competencias": expander_agent.get_expanded_competencies(), "status": "DELETED"}


# ---------------------------------------------------------------------------
# Radar LinkedIn: wiring de RadarLoop con las funciones reales del portal
# ---------------------------------------------------------------------------

def _ref_post_radar(post: Dict[str, Any]) -> str:
    """Clave del post del Radar para reutilizar su postulación: el `urn` (único por post).

    No sirve `post["url"]`: en el DOM de 2026 es el perfil del autor, compartido por todos sus posts."""
    urn = str(post.get("urn") or "").strip()
    return urn or (f"radar-post:{post['id']}" if post.get("id") is not None else "")


def _es_permalink_de_post(url: str) -> bool:
    return "/feed/update/" in (url or "")


def _buscar_postulacion_radar(post: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Postulación `origen='linkedin'` de ESE post (prefiere una READY; si no, la más reciente).

    Se busca por `radar_post_ref` (el `urn` del post): nunca se reutiliza la de otro post del mismo
    autor. Filas anteriores a la columna (ref vacía) solo se aceptan si su URL es el permalink del
    post (`/feed/update/…`, DOM anterior), que sí identifica al post; no un perfil de autor."""
    ref = _ref_post_radar(post)
    if not ref:
        return None
    linkedin = list_postulaciones(origen="linkedin")
    candidatas = [p for p in linkedin if (p.get("radar_post_ref") or "") == ref]
    if not candidatas:
        url = post.get("url") or ""
        if _es_permalink_de_post(url):
            candidatas = [
                p for p in linkedin
                if not (p.get("radar_post_ref") or "") and (p.get("url") or "") == url
            ]
    if not candidatas:
        return None
    listas = [p for p in candidatas if p.get("status") == "READY"]
    return max(listas or candidatas, key=lambda p: p["id"])


# Links del post que se leen antes de analizar (muchos posts solo dicen "mirá el link"). Con más
# texto el análisis High se pasa del timeout: pocos links y recortados
MAX_LINKS_ANALISIS = 2
# Con razonamiento High el Estratega tarda 140-170 s por oferta: el timeout por defecto (180 s) la cortaba
TIMEOUT_ANALISIS_S = 420
EFFORT_ANALISIS = "medium"
MAX_CHARS_POR_LINK = 2500
_AVISO_LINK_EXTERNO = "not on LinkedIn"
_RE_URL_EXTERNA = re.compile(r"https://(?![\w.-]*linkedin\.com)(?!lnkd\.in)[^\s\"'<>]+")
_RE_OFERTA_CERRADA = re.compile(
    r"ya no se aceptan solicitudes|no longer accepting applications|postulaciones cerradas|b[uú]squeda cerrada",
    re.IGNORECASE,
)


async def _leer_link_oferta(url: str) -> Tuple[str, str]:
    """(url leída, contenido) de la página de la oferta. Si es el aviso de LinkedIn de link externo
    (lnkd.in), sigue al destino real. Nunca lanza: un link ilegible devuelve contenido vacío."""
    try:
        pagina = await scrape_job_url(url)
        contenido = (pagina or {}).get("contenido") or ""
        if _AVISO_LINK_EXTERNO in contenido:
            destino = _RE_URL_EXTERNA.search(contenido)
            if destino:
                url = destino.group(0).rstrip(".,)")
                pagina = await scrape_job_url(url)
                contenido = (pagina or {}).get("contenido") or ""
        if (pagina or {}).get("error"):
            return url, ""
        return url, contenido[:MAX_CHARS_POR_LINK]
    except Exception as e:
        logger.warning(f"[Radar] No se pudo leer el link {url}: {e}")
        return url, ""


_RE_URL_EN_TEXTO = re.compile(r"https://[^\s<>]+")


async def _radar_preparar_analisis(post: Dict[str, Any]) -> Dict[str, Any]:
    """Lee hasta MAX_LINKS_ANALISIS=2 links de la oferta, persiste texto_pagina y devuelve el post enriquecido sin LLM."""
    if "links_leidos" in post:
        return dict(post)  # Ya preparado en este intento: no agrega ni lee páginas otra vez.
    preparado = dict(post)
    preparado.setdefault("_texto_original", post.get("texto") or "")
    # También los links escritos en el texto (p. ej. un post que es solo «https://lnkd.in/…»): sin leerlos
    # la oferta quedaba sin texto y se analizaba igual
    en_texto = [u.rstrip(".,;:)»\"'") for u in _RE_URL_EN_TEXTO.findall(post.get("texto") or "")]
    links = list(dict.fromkeys(
        u for u in [*(post.get("links") or []), *en_texto] if isinstance(u, str) and u.startswith("https://")
    ))[:MAX_LINKS_ANALISIS]
    texto_pagina = post.get("texto_pagina") or ""
    leidos = []
    if links and not texto_pagina and int(post.get("intentos") or 0) == 0:
        leidos = await asyncio.gather(*(_leer_link_oferta(u) for u in links))
        unicos = {}
        for url, contenido in leidos:
            if contenido.strip():
                unicos.setdefault(" ".join(contenido.split()), (url, contenido))
        leidos = list(unicos.values())
        if leidos:
            texto_pagina = "\n\n".join(c for _, c in leidos)
            preparado["texto_pagina"] = texto_pagina
            if post.get("id"):
                actual = radar_store.get_post(post["id"])
                if actual and actual.get("estado") == post.get("estado"):
                    radar_store.update_post(post["id"], texto_pagina=texto_pagina,
                                            updated_at_esperado=post.get("updated_at"))
    elif texto_pagina:
        leidos = [(links[0] if links else "captura guardada", texto_pagina)]

    # Reconstruir desde las fuentes evita sumar otra vez las páginas persistidas
    # que Radar ya incluyó al preparar OCR, también en un reintento.
    texto = texto_para_analisis(dict(preparado, texto_pagina=""))
    if leidos:
        for url, contenido in leidos:
            texto += f"\n\n--- Página de la oferta ({url}) ---\n{contenido}"
        preparado["links_leidos"] = links if post.get("texto_pagina") else [u for u, _ in leidos]
        preparado["oferta_cerrada"] = any(_RE_OFERTA_CERRADA.search(c) for _, c in leidos)
    else:
        preparado.setdefault("links_leidos", [])
        preparado.setdefault("oferta_cerrada", False)
    preparado["texto"] = texto
    return preparado


async def _radar_analyze(post: Dict[str, Any]) -> Dict[str, Any]:
    """Análisis de la oferta del post: el mismo del Estratega de Postulaciones (requisitos con
    match/brecha, dolores de la empresa, score de afinidad), con el modelo y el nivel de
    razonamiento elegidos en el portal. Sin IA no hay análisis: una falla del modelo falla acá.

    Recibe el post preparado por _radar_preparar_analisis y no repite la lectura de páginas.
    Los consumidores directos anteriores pasan por esa misma preparación una sola vez."""
    if "links_leidos" not in post:
        post = await _radar_preparar_analisis(post)
    texto = post.get("texto") or ""
    # Match y score: razonamiento Medium (el High queda para el CV, que solo se escribe sobre el umbral)
    strategist = StrategistAgent(bridge=AntigravityBridge(effort=EFFORT_ANALISIS), effort=EFFORT_ANALISIS)
    with etapa("analisis") as medida:
        analisis = await strategist.analyze_offer(texto, datos_de_terceros=True, timeout_seconds=TIMEOUT_ANALISIS_S)
    if not isinstance(analisis, dict):
        raise RuntimeError("el modelo no respondió un análisis válido")
    analisis["matriz_oferta"] = extract_matriz_oferta(analisis.get("requisitos", []))
    analisis["idioma_cv"] = idioma_cv_de_oferta(texto)
    analisis["etapa_analisis"] = medida
    analisis["links_leidos"] = post.get("links_leidos") or []
    analisis["oferta_cerrada"] = bool(post.get("oferta_cerrada"))
    return analisis


def _rechazo_de_calidad(postulacion: Optional[Dict[str, Any]]) -> bool:
    """La postulación terminó en ERROR porque la revisión de calidad no aprobó el CV o el mensaje."""
    if not postulacion or postulacion.get("status") != "ERROR":
        return False
    reporte = postulacion.get("reporte_auditoria")
    if isinstance(reporte, str):
        try:
            reporte = json.loads(reporte)
        except Exception:
            return False
    return isinstance(reporte, dict) and reporte.get("approved") is False


async def _radar_pipeline(post: Dict[str, Any]) -> int:
    """CV del post vía el circuito de Postulaciones. Idempotente por post (su `urn`), nunca por autor.

    Si el post ya tiene el análisis del Radar, la postulación nace con él y solo corren el
    Redactor y el Auditor (`recalculate_cv_task`, sin repetir el Estratega); si no, el pipeline
    completo. Reutiliza la postulación READY de ese post; si existe pero no está READY, re-ejecuta
    sobre la misma fila. Las tareas capturan sus propios errores, así que el resultado se lee del
    `status` final: distinto de READY → `RuntimeError`.

    Ante cualquier corte (fallo, cancelación por `stop()`) se borra `postulacion_id` del post:
    así el próximo intento vuelve a correr el pipeline en vez de redactar sobre un CV inválido.
    """
    _descartar_borradores(post["id"])
    triage = post.get("triage") if isinstance(post.get("triage"), dict) else {}
    analisis = post.get("analisis") if isinstance(post.get("analisis"), dict) else {}
    url = post.get("url") or ""
    ref = _ref_post_radar(post)
    existente = _buscar_postulacion_radar(post)
    if existente and not (existente.get("radar_post_ref") or ""):
        # Fila previa a la migración (permalink del post): queda asociada a este post
        update_postulacion(existente["id"], radar_post_ref=ref)
    if existente and existente.get("status") == "READY":
        radar_store.update_post(post["id"], postulacion_id=existente["id"])
        return existente["id"]
    if existente:
        pid = existente["id"]
    else:
        pid = create_postulacion(
            titulo=(analisis.get("puesto") or triage.get("puesto") or "").strip() or "Búsqueda en LinkedIn",
            empresa=(analisis.get("empresa") or triage.get("empresa") or "").strip()
            or (post.get("autor") or "").strip() or "Empresa (LinkedIn)",
            contenido=post.get("texto") or "",
            url=url,
            origen="linkedin",
            radar_post_ref=ref,
        )
    _limpiar_fallback_radar(pid)
    radar_store.update_post(post["id"], postulacion_id=pid)
    actual = radar_store.get_post(post["id"])
    preparado = dict(actual, texto=texto_para_analisis(actual)) if actual else post
    ejecucion = {"pid": pid, "post": deepcopy(preparado)}
    token_finalizacion = _finalizacion_radar.set(ejecucion)
    token_post = _radar_post_pipeline.set(post["id"])
    try:
        # Un análisis válido puede no detectar requisitos; eso no habilita repetirlo.
        if analisis:
            update_postulacion(
                pid,
                dolores_empresa=analisis.get("dolores_empresa", []),
                requisitos=analisis.get("requisitos", []),
                match_score=analisis.get("match_score", 0.0),
                analisis=analisis,
            )
            token = _origen_pipeline.set("linkedin")
            try:
                await recalculate_cv_task(pid)
            finally:
                _origen_pipeline.reset(token)
        else:
            await run_pipeline_task(pid)
        # Corrección automática: un rechazo de la revisión de calidad tiene CORRECCIONES_DE_CALIDAD pasadas de
        # lo que hace «remediar» (el auditor reescribe con sus directivas) antes de ir a ERROR. Las reglas no cambian.
        for _ in range(CORRECCIONES_DE_CALIDAD):
            if not _rechazo_de_calidad(get_postulacion(pid)):
                break
            logger.info(f"[Radar] El CV del post {post['id']} no pasó la revisión: corrección automática")
            token = _origen_pipeline.set("linkedin")
            try:
                await remediate_cv_task(pid)
            finally:
                _origen_pipeline.reset(token)
            ejecucion["corregida"] = True
    except BaseException:
        # CancelledError (stop, plazo vencido, descarte) u otro corte: sin vínculo, el reintento
        # re-ejecuta el pipeline; la postulación no queda «procesando» para siempre
        _desvincular_postulacion(post["id"])
        _postulacion_cortada_a_error(pid)
        raise
    finally:
        _finalizacion_radar.reset(token_finalizacion)
        _radar_post_pipeline.reset(token_post)
        _ETAPAS_PIPELINE.pop(pid, None)
    final = get_postulacion(pid)
    telemetria = (final or {}).get("telemetry")
    if isinstance(telemetria, str):
        try:
            telemetria = json.loads(telemetria)
        except Exception:
            telemetria = {}
    if isinstance(telemetria, dict) and telemetria.get("is_fallback"):
        # El Redactor no respondió y quedó el CV determinístico (siempre en español, genérico): no
        # se ofrece como listo. La postulación sale de READY para que el reintento la rehaga
        update_postulacion(pid, status="ERROR")
        _desvincular_postulacion(post["id"])
        motivo = telemetria.get("fallback_reason") or "sin respuesta del modelo"
        raise RuntimeError(f"El Redactor no generó el CV ({motivo}): reintentá")
    reporte_final = (final or {}).get("reporte_auditoria")
    if isinstance(reporte_final, str):
        try:
            reporte_final = json.loads(reporte_final)
        except Exception:
            reporte_final = None
    if (final and final.get("status") == "ERROR" and isinstance(reporte_final, dict)
            and reporte_final.get("approved") is False):
        # Rechazo de calidad: queda para revisar con su motivo visible.
        _desvincular_postulacion(post["id"])
        motivos = ((reporte_final.get("hallazgos_unificados") or {}).get("lista_observaciones")
                   or reporte_final.get("deducciones") or ["la auditoría no aprobó el contenido"])
        tras = " (tras una corrección automática)" if ejecucion.get("corregida") else ""
        raise RuntimeError(f"Revisá el CV{tras}: " + "; ".join(str(m) for m in motivos))
    if not final or final.get("status") != "READY":
        # Sin vínculo: el reintento del radar vuelve a correr el pipeline (sobre esta misma fila)
        _desvincular_postulacion(post["id"])
        if isinstance(telemetria, dict) and telemetria.get("origen") == ORIGEN_CV_MAESTRO and telemetria.get("error"):
            if telemetria.get("mail"):  # el CV pasó; el mail no, tras su única corrección
                raise RuntimeError(telemetria["error"])
            raise RuntimeError(f"El Redactor no generó el CV ({telemetria['error']}): reintentá")
        estado = final.get("status") if final else "inexistente"
        raise RuntimeError(f"El pipeline del CV terminó en estado {estado} (postulación {pid})")
    if isinstance(telemetria, dict) and telemetria.get("incompleto"):
        update_postulacion(pid, status="ERROR")
        _desvincular_postulacion(post["id"])
        raise RuntimeError(f"El CV quedó con campos sin completar ({', '.join(telemetria['incompleto'])}): reintentá")
    borrador = ejecucion.pop("borrador", None)
    actual = radar_store.get_post(post["id"])
    if actual:
        actual = dict(actual, texto=texto_para_analisis(actual))
    if (borrador and actual and actual.get("estado") == "GENERANDO"
            and actual.get("postulacion_id") == pid
            and actual.get("updated_at") == ejecucion["post"].get("updated_at")
            and _entradas_mensaje(actual, final) == _entradas_mensaje(ejecucion["post"], borrador[0])):
        cache = _borradores_radar()
        clave = (post["id"], pid)
        entrada = {"entradas": _entradas_mensaje(actual, final),
                   "versiones": (actual.get("updated_at"), final.get("updated_at")),
                   "mensaje": borrador[1]}
        cache[clave] = entrada

        def al_terminar(tarea):
            # Un stop/corte entre READY y compose tampoco retiene el borrador.
            if cache.get(clave) is not entrada:
                return
            vigente = radar_store.get_post(post["id"])
            if (tarea.cancelled() or tarea.exception() is not None or not vigente
                    or vigente.get("estado") != "GENERANDO"
                    or vigente.get("postulacion_id") != pid):
                if cache.get(clave) is entrada:
                    cache.pop(clave, None)

        asyncio.current_task().add_done_callback(al_terminar)
    return pid


def _postulacion_cortada_a_error(postulacion_id: int) -> None:
    """Una postulación cuyo pipeline se cortó a mitad pasa de PROCESSING a ERROR (nunca tapa la
    excepción original)."""
    try:
        postulacion = get_postulacion(postulacion_id)
        if postulacion and postulacion.get("status") == "PROCESSING":
            update_postulacion(postulacion_id, status="ERROR")
    except Exception:
        logger.exception(f"[Radar] No se pudo marcar en ERROR la postulación {postulacion_id}")


def _desvincular_postulacion(post_id: int) -> None:
    """Borra `postulacion_id` del post; nunca tapa la excepción original."""
    try:
        _descartar_borradores(post_id)
        radar_store.update_post(post_id, postulacion_id=None)
    except Exception:
        logger.exception(f"[Radar] No se pudo desvincular la postulación del post {post_id}")


def _borradores_radar() -> Dict[Tuple[int, int], Dict[str, Any]]:
    """Transferencia efímera en el event loop del Radar, sin mezclar ejecuciones/servidores. Con el Radar en
    su propio hilo, el portal descarta en el caché de ese loop: un evento del portal invalida el borrador."""
    loop = getattr(radar_loop, "loop", None)
    if not isinstance(loop, asyncio.AbstractEventLoop):
        loop = asyncio.get_running_loop()
    if not hasattr(loop, "_linkedout_borradores"):
        loop._linkedout_borradores = {}
    return loop._linkedout_borradores


def _descartar_borradores(post_id: Optional[int] = None) -> None:
    cache = _borradores_radar()
    for clave in list(cache):
        if post_id is None or clave[0] == post_id:
            cache.pop(clave, None)


def _entradas_mensaje(post: Dict[str, Any], postulacion: Dict[str, Any]) -> str:
    """Comparación exacta de entradas del composer y de la completitud del mail.

    Progreso/telemetría no son contenido; sus escrituras no invalidan el draft.
    Las versiones se verifican por separado después de publicar READY.
    """
    return json.dumps([
        {k: post.get(k) for k in ("id", "urn", "texto", "autor", "emails", "triage", "analisis",
                                 "canal", "destino_canal", "whatsapp", "links")},
        {k: postulacion.get(k) for k in ("id", "titulo", "empresa", "contenido_oferta", "dolores_empresa",
                                        "pitch", "pitches_multicanal")},
    ], sort_keys=True, ensure_ascii=False)


async def _radar_compose(post: Dict[str, Any], postulacion_id: int) -> Dict[str, Any]:
    """Redacta el mensaje (email o canal). Solo sobre una postulación READY con CV: si no (p. ej. un pipeline
    cortado por un cierre abrupto, o pendiente de regenerar), desvincula y falla, para que el
    reintento vuelva a generar el CV en la misma fila en vez de adjuntar uno inválido o viejo."""
    # Consumo único incluso cuando el CV es inválido o se cancela su actualización.
    entrada = _borradores_radar().pop((post["id"], postulacion_id), None)
    postulacion = get_postulacion(postulacion_id)
    if not postulacion or postulacion.get("status") != "READY" or not (postulacion.get("cv_markdown") or "").strip():
        _desvincular_postulacion(post["id"])
        estado = postulacion.get("status") if postulacion else "inexistente"
        raise RuntimeError(
            f"La postulación {postulacion_id} no tiene un CV listo (estado {estado}); se regenerará"
        )
    postulacion = await _cv_al_dia(postulacion)
    actual = radar_store.get_post(post["id"])
    if actual:
        # La base conserva el texto original; composer y guardas comparan la
        # misma oferta enriquecida que recibió el pipeline, sin perder el OCR.
        actual = dict(actual, texto=texto_para_analisis(actual))
    if (entrada and actual and actual.get("estado") == "GENERANDO"
            and actual.get("postulacion_id") == postulacion_id
            and entrada["versiones"] == (actual.get("updated_at"), postulacion.get("updated_at"))
            and entrada["entradas"] == _entradas_mensaje(actual, postulacion)
            and entrada["entradas"] == _entradas_mensaje(post, postulacion)):
        await progreso.reportar("completar_mensaje")
        return entrada["mensaje"]
    if actual:
        # También sin borrador: el post pudo cambiar durante el pipeline del CV.
        # El worker conserva este dict para validar el canal después de componer;
        # actualizarlo evita exigir un asunto de email a un DM (o aceptar el inverso).
        post.clear()
        post.update(actual)
    return await _componer_mensaje_validado(post, postulacion)


async def _componer_mensaje_validado(post: dict, postulacion: dict) -> dict:
    try:
        return await _componer_y_completar_mensaje(post, postulacion)
    except MensajeRequiereRevision as exc:
        _registrar_senales_finales("mensaje", "", exc.motivos, postulacion.get("id"), post.get("id"))
        # lo que no pasa tras la única corrección abre un ticket con el motivo (no queda para revisar)
        tickets.reportar_error("mail", "mail_no_aprobado", str(exc), datos={
            "post_id": post.get("id"), "postulacion_id": postulacion.get("id"), "motivos": exc.motivos,
            "borrador": exc.borrador})
        raise


async def _componer_y_completar_mensaje(post: dict, postulacion: dict) -> dict:
    """Composición con validadores finales; una falla del modelo se reintenta una vez y después se propaga."""
    ejecucion = _finalizacion_radar.get()
    with etapa("mail") as medida:
        try:
            mensaje = await compose_message(post, postulacion)
        except MensajeRequiereRevision:
            raise
        except Exception as exc:
            logger.warning(f"[Radar] El mensaje del post {post.get('id')} falló ({exc}): se reintenta")
            mensaje = await compose_message(post, postulacion)
        # El worker aún debe informar «mensaje» al salir del pipeline. Diferir
        # este paso evita 97→92 cuando el mail termina antes que el CV.
        if ejecucion is None:
            await progreso.reportar("completar_mensaje")
        mensaje = await _completar_mensaje(post, postulacion, mensaje)
    if ejecucion is not None and ejecucion["pid"] == postulacion["id"]:
        _ETAPAS_PIPELINE.setdefault(postulacion["id"], []).append(medida)
    else:
        _sumar_etapa(postulacion["id"], medida)
    return mensaje


async def _cv_al_dia(postulacion: Dict[str, Any]) -> Dict[str, Any]:
    """Un CV generado antes de las reglas actuales (puesto objetivo, campos a completar, afirmaciones
    que inflan el perfil) se corrige antes de adjuntarlo. Sin hallazgos no llama al modelo."""
    if _es_cv_maestro(postulacion):
        return postulacion
    cv = postulacion.get("cv_markdown") or ""
    pitches = postulacion.get("pitches_multicanal") or {}
    if isinstance(pitches, str):
        try:
            pitches = json.loads(pitches)
        except Exception:
            pitches = {}
    pitches = pitches if isinstance(pitches, dict) else {}
    pitch = postulacion.get("pitch") or ""
    textos = [("CV", cv), ("pitch", pitch),
              *((f"mensaje ({c})", t) for c, t in pitches.items() if isinstance(t, str))]
    decision = await _decidir_titulo_cv(postulacion, cv, sin_ia=True)
    cv_objetivo, _ = aplicar_titulo(cv, decision)
    cv_objetivo = asegurar_mencion_ats(cv_objetivo, decision.puesto_oferta)
    if (cv_objetivo != cv or any(placeholders(t) for _, t in textos)
            or contradicciones_cv(cv, _perfil_o_vacio())):
        cv, pitch, pitches, _ = await _asegurar_completo(
            postulacion["id"], cv, pitch, pitches, informar_paso=False,
            _decision_titulo=decision)
    else:
        cv = _sanear_texto_final(cv, "CV")
        pitch = _sanear_texto_final(pitch, "pitch")
        pitches = {c: _sanear_texto_final(t, f"mensaje ({c})") if isinstance(t, str) else t
                   for c, t in pitches.items()}
    previo = postulacion.get("reporte_auditoria")
    reporte, motivos = _dictamen_final(postulacion, cv, pitch, pitches, previo,
                                       conservar_sin_dictamen=True)
    # Un CV heredado sin dictamen no se convierte retroactivamente en rechazado
    # por criterios absolutos. Sí se bloquean señales y pérdidas ATS nuevas.
    if not reporte and not motivos:
        reporte = previo
    cambios = {"cv_markdown": cv, "pitch": pitch, "pitches_multicanal": pitches,
               "reporte_auditoria": reporte}
    rechazo = isinstance(reporte, dict) and reporte.get("approved") is False and postulacion.get("status") in ("READY", "ERROR")
    if rechazo:
        cambios["status"] = "ERROR"
    update_postulacion(postulacion["id"], **cambios)
    if rechazo or pitch != (postulacion.get("pitch") or "") or pitches != (postulacion.get("pitches_multicanal") or {}):
        # El almacenamiento ya invalida el adjunto al cambiar el CV. Los otros
        # textos finales y un rechazo también requieren una nueva aprobación.
        for post in radar_store.list_posts():
            if post.get("postulacion_id") == postulacion["id"] and post.get("estado") in ("LISTO", "ENVIANDO"):
                radar_store.update_post(post["id"], aprobado_at=None,
                                        updated_at_esperado=post.get("updated_at"))
    if rechazo:
        raise RuntimeError("Revisá el CV: " + "; ".join(motivos))
    return get_postulacion(postulacion["id"]) or postulacion


async def _completar_mensaje(post: Dict[str, Any], postulacion: Dict[str, Any],
                             mensaje: Dict[str, Any]) -> Dict[str, Any]:
    """Asunto y cuerpo sin campos a rellenar ni afirmaciones que inflen el perfil; si no se puede,
    falla (el post queda en ERROR con el motivo, nunca LISTO a medio hacer)."""
    contexto = "\n".join(x for x in (_contexto_oferta(postulacion), str(post.get("texto") or "")) if x)
    perfil = _perfil_o_vacio()
    anterior = dict(mensaje)
    mensaje = dict(mensaje)
    cambio_completitud = False
    for campo, contradice in (("subject", False), ("body", True)):
        texto = str(mensaje.get(campo) or "")
        if not texto:
            continue
        nuevo, cambios = await completar(texto, tipo="asunto" if campo == "subject" else "mensaje",
                                         contexto=contexto,
                                         contradicciones=contradicciones_cv(texto, perfil) if contradice else None)
        cambio_completitud |= nuevo != texto
        _registrar_revision(cambios, postulacion.get("id"), post.get("id"),
                            "asunto" if campo == "subject" else "mensaje")
        mensaje[campo] = _sanear_texto_final(nuevo, campo, asunto_exigido(post) if campo == "subject" else "")
    restos = placeholders(str(mensaje.get("subject") or "")) + placeholders(str(mensaje.get("body") or ""))
    if restos:
        for campo in restos:
            revision_store.registrar("placeholder", campo, campo, "Quedó sin completar en el mensaje",
                                     postulacion_id=postulacion.get("id"), post_id=post.get("id"))
        raise RuntimeError(f"El mensaje quedó con campos sin completar ({', '.join(restos)}): reintentá")
    motivos = []
    for campo in ("subject", "body"):
        texto = str(mensaje.get(campo) or "")
        hallazgos = _motivos_texto_final(texto, campo, asunto_exigido(post) if campo == "subject" else "")
        _registrar_senales_finales(campo, texto, hallazgos, postulacion.get("id"), post.get("id"))
        motivos.extend(f"{campo}: {m}" for m in hallazgos)
    if motivos:
        raise MensajeRequiereRevision(motivos)
    if cambio_completitud:
        validar_mensaje_final(post, mensaje, perfil, anterior=anterior)
    return mensaje


def _sumar_etapa(postulacion_id: int, medida: Dict[str, Any]) -> None:
    """Agrega una etapa medida fuera del pipeline del CV (el mail) a la telemetría de la postulación."""
    try:
        postulacion = get_postulacion(postulacion_id) or {}
        telemetria = postulacion.get("telemetry")
        if isinstance(telemetria, str):
            telemetria = json.loads(telemetria or "{}")
        telemetria = telemetria if isinstance(telemetria, dict) else {}
        etapas = [e for e in telemetria.get("etapas") or [] if e.get("etapa") != medida["etapa"]] + [medida]
        update_postulacion(postulacion_id, telemetry={**telemetria, **resumen_etapas(etapas)})
    except Exception:
        logger.exception(f"[Radar] No se pudo guardar la etapa {medida.get('etapa')} de la postulación {postulacion_id}")


async def _radar_send(post: Dict[str, Any]) -> str:
    """Compila el CV en PDF y envía el email aprobado por Gmail. Devuelve el Message-ID.

    Revalida que la aprobación y el CV permanezcan vigentes tanto antes de conectar
    como después del login SMTP inmediatamente antes de entregar el mensaje.
    """
    config = load_gmail_config()
    if config is None:
        raise MailerAuthError("Gmail no configurado: completá GMAIL_ADDRESS y GMAIL_APP_PASSWORD en .env")
    motivos = _revisar_contenido_heredado(post)
    if motivos:
        raise AprobacionInvalidadaError("Revisá el contenido antes de enviar: " + "; ".join(motivos))
    pid = post.get("postulacion_id")
    postulacion = get_postulacion(pid) if pid else None
    if not postulacion or not postulacion.get("cv_markdown"):
        raise MailerError(f"CV no disponible para adjuntar (postulación {pid})")
    if postulacion.get("status") != "READY":
        raise AprobacionInvalidadaError("El CV cambió: revisalo y volvé a aprobar el envío")

    def final_verificado(p: Dict[str, Any]) -> Optional[Path]:
        # El adjunto es el CV final (el PDF que aprobó la auditoría), comprobado por su huella; si no coincide, no
        # se envía y queda un ticket
        try:
            return cv_final.verificar(p)
        except cv_final.CVFinalInvalido as exc:
            tickets.reportar_error(componente="cv", tipo="cv_final_invalido", mensaje=f"Envío: {exc}",
                                   datos={"post_id": post.get("id"), "postulacion_id": pid})
            raise AprobacionInvalidadaError(f"El CV no es el que aprobó la auditoría ({exc}): regeneralo")

    pdf_path = await asyncio.to_thread(final_verificado, postulacion)
    if pdf_path is None:
        # CV del flujo anterior al maestro (sin CV final): se compila como antes
        filename = generate_cv_pdf_filename(target_role=_puesto_export(postulacion))
        pdf_path = await asyncio.to_thread(
            compile_markdown_to_pdf,
            postulacion["cv_markdown"],
            target_role=_puesto_export(postulacion),
            output_filename=filename,
            keywords=_extract_postulacion_keywords(postulacion),
        )
    perfil = load_candidate_profile()
    from_name = (perfil.get("nombre") or "").strip()
    cuerpo = post.get("email_body") or ""

    def validar_aprobacion() -> None:
        """Verifica que el post siga ENVIANDO con la misma aprobación y versión, y que el CV siga READY e intacto."""
        actual = radar_store.get_post(post["id"])
        cv_actual = get_postulacion(pid)
        if actual and _motivos_contenido_heredado(actual, cv_actual):
            raise AprobacionInvalidadaError("Revisá el CV y el mensaje antes de enviar")
        if (not actual or actual["estado"] != "ENVIANDO" or not actual.get("aprobado_at")
                or actual.get("aprobado_at") != post.get("aprobado_at")
                or actual.get("updated_at") != post.get("updated_at")
                or not cv_actual or cv_actual.get("status") != "READY"
                or cv_actual.get("cv_markdown") != postulacion["cv_markdown"]
                or cv_actual.get("cv_pdf_sha256") != postulacion.get("cv_pdf_sha256")):
            raise AprobacionInvalidadaError("El CV o el mensaje cambió: revisalo y volvé a aprobar el envío")
        if cv_final.tiene_final(cv_actual):
            final_verificado(cv_actual)  # el archivo a adjuntar sigue siendo el auditado, justo antes de enviar

    def enviar_aprobado():
        # La compilación y la conexión SMTP ceden el control: el editor pudo cambiar el CV o
        # anular la aprobación mientras tanto. Se valida antes de conectar y se vuelve a
        # validar tras el login vía before_send, inmediatamente antes de send_message.
        validar_aprobacion()
        return send_application_email(
            to=(post.get("email_to") or "").strip(),
            subject=post.get("email_subject") or "",
            body=email_texto_plano(cuerpo, perfil),
            pdf_path=pdf_path,
            from_name=from_name,
            config=config,
            html_body=email_html(cuerpo, perfil),
            before_send=validar_aprobacion,
        )

    return await asyncio.to_thread(enviar_aprobado)


async def _radar_ingestar_manual(post: dict) -> dict:
    """Lee la página completa antes del triage de una oferta manual pegada como URL."""
    from core.linkedin_parser import extract_emails, extract_links, extract_whatsapp

    url = post.get("post_url") or post["texto"]
    pagina = await scrape_job_url(url)
    texto = (pagina or {}).get("contenido") or ""
    imagenes = (pagina or {}).get("imagenes") or []
    if re.search(r"HTTP Error (?:404|410)\b", str((pagina or {}).get("error") or "")):
        raise OfertaInexistente("La oferta ya no existe en el portal (404): el aviso se cerró o se borró")
    if (pagina or {}).get("error") or (not texto.strip() and not imagenes):
        raise RuntimeError("No se pudo leer la página de la oferta: pegá el texto")
    return {
        "texto": texto,
        "autor": pagina.get("empresa") or post.get("autor") or "Oferta manual",
        "cargo_autor": pagina.get("titulo") or post.get("cargo_autor") or "Oferta manual",
        "emails": extract_emails(texto),
        "links": extract_links(texto, [url]),
        "whatsapp": extract_whatsapp(texto, [url]),
        "imagenes": imagenes,
    }


def _build_radar_loop() -> RadarLoop:
    return RadarLoop(
        harvester_factory=LinkedInHarvester,
        triage_fn=triage_batch,
        ingest_fn=_radar_ingestar_manual,
        analyze_fn=_radar_analyze,
        pipeline_fn=_radar_pipeline,
        compose_fn=_radar_compose,
        send_fn=_radar_send,
        broadcast=broadcast_event,
        generation_workers=GENERACIONES_EN_PARALELO,
        prepare_fn=_radar_preparar_analisis,
    )


# El Radar corre en su propio hilo y event loop: su SQLite síncrono no frena al portal
radar_loop: RadarEnHilo = RadarEnHilo(_build_radar_loop())


def _asunto_literal_envio(post: dict) -> str:
    """Conserva el asunto exigido y la referencia al puesto del composer legado.

    La excepción sólo cubre el separador histórico y el puesto literal del
    triage; no habilita rayas en prosa arbitraria ni en mensajes nuevos.
    """
    exigido = asunto_exigido(post)
    if exigido:
        return exigido
    puesto = (post.get("triage") or {}).get("puesto")
    asunto = post.get("email_subject") or ""
    if puesto and asunto == f"Postulación – {puesto}":
        return asunto
    return ""


MOTIVO_AUDITORIA_NO_APROBADA = "la auditoría del CV no aprobó el contenido"


def _forzado_vigente(post: dict) -> bool:
    """«Enviar igual» de la aprobación actual: el permiso se registró con la misma hora que `aprobado_at`."""
    return bool(post.get("forzado_auditoria_at")) and post.get("forzado_auditoria_at") == post.get("aprobado_at")


def _motivos_contenido_heredado(post: dict, postulacion: Optional[dict],
                                forzar_auditoria: Optional[bool] = None) -> List[str]:
    """Guard sin IA ni reparación: también se ejecuta inmediatamente antes de SMTP. `forzar_auditoria` («Enviar
    igual») excusa solo el dictamen no aprobado de la auditoría; sin el dato, vale el permiso de la aprobación."""
    motivos = []
    if forzar_auditoria is None:
        forzar_auditoria = _forzado_vigente(post)
    reporte = (postulacion or {}).get("reporte_auditoria") or {}
    if isinstance(reporte, dict) and reporte.get("approved") is False and not forzar_auditoria:
        motivos.append(MOTIVO_AUDITORIA_NO_APROBADA)
    for campo, texto in (("CV", (postulacion or {}).get("cv_markdown") or ""),
                          ("subject", post.get("email_subject") or ""),
                          ("body", post.get("email_body") or "")):
        motivos.extend(f"{campo}: {m}" for m in _motivos_texto_final(
            texto, campo, _asunto_literal_envio(post) if campo == "subject" else ""))
    return motivos


def _revisar_contenido_heredado(post: dict, forzar_auditoria: Optional[bool] = None) -> List[str]:
    """Sanea sin IA, persiste e invalida la aprobación si cambió cualquier contenido. `forzar_auditoria`: ver
    `_motivos_contenido_heredado` (los demás motivos bloquean igual)."""
    actual = radar_store.get_post(post["id"])
    if not actual or actual.get("updated_at") != post.get("updated_at"):
        return ["el contenido cambió; revisá la versión actual"]
    pid = post.get("postulacion_id")
    postulacion = get_postulacion(pid) if pid else None
    cambios = {}
    if postulacion and not cv_final.tiene_final(postulacion):
        # Un CV final (el PDF auditado, con su huella) no se retoca: su texto es el del archivo que se adjunta
        cv = postulacion.get("cv_markdown") or ""
        nuevo = _sanear_texto_final(cv, "CV")
        if nuevo != cv:
            update_postulacion(pid, cv_markdown=nuevo)
            postulacion = {**postulacion, "cv_markdown": nuevo}
            cambios["aprobado_at"] = None
    for campo, clave in (("subject", "email_subject"), ("body", "email_body")):
        texto = post.get(clave) or ""
        nuevo = _sanear_texto_final(texto, campo, _asunto_literal_envio(post) if campo == "subject" else "")
        if nuevo != texto:
            cambios[clave] = nuevo
    motivos = _motivos_contenido_heredado({**post, **cambios}, postulacion, forzar_auditoria)
    if cambios:
        motivos.append("el contenido cambió al sanearlo; volvé a aprobar la versión actual")
    if motivos:
        detalle = "Revisá el contenido antes de enviar: " + "; ".join(motivos)
        cambios.update(aprobado_at=None, error=detalle)
        # El cambio del CV invalida el post en la misma transacción. Sólo usar
        # esa versión si el mensaje sigue intacto; no pisar una edición concurrente.
        actual = radar_store.get_post(post["id"])
        if actual and all(actual.get(k) == post.get(k) for k in
                          ("email_to", "email_subject", "email_body", "postulacion_id", "estado")):
            radar_store.update_post(post["id"], updated_at_esperado=actual["updated_at"], **cambios)
        _registrar_senales_finales("envío", "", motivos, pid, post["id"])
    return motivos


async def _validar_calidad_envio(request: Request, _local=Depends(_solo_operacion_local)) -> None:
    """Frontera de las tres entradas de envío, dentro del guard de operación local.

    Un lote con contenido pendiente se rechaza completo antes de encolar envíos.
    Las validaciones de estado y versión de la API conservan su precedencia.
    """
    if request.method != "POST":
        return
    path = request.url.path
    candidatos = []
    if path.endswith("/approve") or path == "/api/radar/approve-all":
        try:
            payload = await request.json()
        except (ValueError, UnicodeDecodeError):
            # La API conserva su diagnóstico de payload ausente o inválido.
            return
    forzar = None
    if re.fullmatch(r"/api/radar/posts/\d+/approve", path):
        post = radar_store.get_post(int(path.split("/")[-2]))
        if (post and post.get("estado") == "LISTO" and isinstance(payload, dict)
                and payload.get("updated_at") == post.get("updated_at")):
            candidatos = [post]
            # «Enviar igual» de este pedido; sin él, vale solo el de la aprobación vigente (re-encolar)
            forzar = True if payload.get("forzar_auditoria") is True else None
    elif path == "/api/radar/approve-all":
        if isinstance(payload, dict) and isinstance(payload.get("ids"), list) and isinstance(payload.get("updated_at"), dict):
            for post_id in payload["ids"]:
                if not isinstance(post_id, int):
                    continue
                post = radar_store.get_post(post_id)
                if (post and post.get("estado") == "LISTO" and not post.get("aprobado_at")
                        and payload["updated_at"].get(str(post_id)) == post.get("updated_at")):
                    candidatos.append(post)
    elif path == "/api/radar/resume-sends":
        candidatos = [p for p in radar_store.list_posts(estado="LISTO") if p.get("aprobado_at")]
    # Los otros canales se omiten/rechazan en la API antes de aprobar: no son
    # candidatos a envío SMTP y no corresponde modificar su contenido acá.
    candidatos = [p for p in candidatos if radar_store.es_canal_email(p)]
    if candidatos and (path.endswith("/approve") or path == "/api/radar/approve-all"):
        # Conservar el diagnóstico de configuración sin sanear una operación
        # que todavía no puede aprobar. El guard de calidad sigue antes de encolar.
        _require_gmail()
    motivos = []
    for post in candidatos:
        motivos.extend(_revisar_contenido_heredado(post, forzar))
    if motivos:
        raise HTTPException(status_code=409, detail="Revisá el contenido antes de enviar: " + "; ".join(dict.fromkeys(motivos)))


# Del reporte anterior pasan al nuevo: cómo se armó el CV, no su dictamen
_META_REPORTE = ("origen", "seleccion", "reemplazos", "tipo_cv", "motivo_tipo", "segundos", "correcciones",
                 "a_la_primera", "hallazgos_por_auditoria")
# Una corrección por post a la vez (la IA y la auditoría tardan; un segundo clic no lanza otra)
_CORRIGIENDO: set[int] = set()


def _auditor_correccion() -> AuditorAgent:
    return AuditorAgent(effort="low")


def _paginas_pdf(ruta: Path) -> int:
    from pypdf import PdfReader
    return len(PdfReader(str(ruta)).pages)


def _criterios_auditoria(postulacion: dict, reporte: dict) -> Tuple[List[str], List[str]]:
    """Keywords y empresas con que se juzgó el CV (las del reporte); sin ellas, las de la postulación y el perfil."""
    ats = reporte.get("ats") or {}
    claves = ([*ats["found_keywords"], *ats["missing_keywords"]]
              if "found_keywords" in ats and "missing_keywords" in ats else _extract_postulacion_keywords(postulacion))
    empresas = ([*ats["found_experiences"], *ats["missing_experiences"]]
                if "found_experiences" in ats and "missing_experiences" in ats
                else _perfil_o_vacio().get("empresas") or [])
    return claves, empresas


@app.post("/api/radar/posts/{post_id}/corregir-auditoria", dependencies=[Depends(_solo_operacion_local)])
async def api_corregir_auditoria(post_id: int):
    """Corrige solo lo que marcó la auditoría sobre el CV actual (`core/correccion_auditoria.py`), lo vuelve a
    auditar y deja el PDF final con su huella. No regenera de cero ni toca el mail. Vale también con la postulación
    en ERROR (una corrección anterior cortada) si están el CV y el reporte."""
    post = radar_store.get_post(post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post inexistente")
    pid = post.get("postulacion_id")
    postulacion = get_postulacion(pid) if pid else None
    reporte = (postulacion or {}).get("reporte_auditoria")
    if post.get("estado") != "LISTO" or post.get("aprobado_at"):
        raise HTTPException(status_code=409, detail="Solo se corrige una oferta lista para enviar y sin aprobar")
    if not postulacion or not (postulacion.get("cv_markdown") or "").strip() or not isinstance(reporte, dict):
        raise HTTPException(status_code=409, detail="No hay CV o dictamen de la auditoría para corregir")
    if reporte.get("approved") is not False:
        raise HTTPException(status_code=409, detail="La auditoría ya aprobó este CV")
    if post_id in _CORRIGIENDO:
        raise HTTPException(status_code=409, detail="Ya se está corrigiendo")
    _CORRIGIENDO.add(post_id)
    try:
        return await _corregir_auditoria(post_id, postulacion, reporte)
    finally:
        _CORRIGIENDO.discard(post_id)


def _lineas_ya_revisadas(postulacion: dict, reporte: dict, marcadas: set) -> bool:
    """Todas las líneas del CV actual ya pasaron por la revisión con IA, así que re-auditar sin IA alcanza:
    - lo aprobó el flujo del maestro (su CV final coincide por huella): las señales de su reporte eran de líneas que
      esa revisión ya había aprobado (antes el reporte las incluía igual);
    - lo dejó una corrección de acá (`correccion_auditoria`, misma huella) y su reporte no marca ninguna línea."""
    if "correccion_auditoria" in reporte and marcadas:
        return False
    try:
        return cv_final.verificar(postulacion) is not None
    except cv_final.CVFinalInvalido:
        return False


class _SinRevisionIA:
    """El auditor sin la revisión con IA: para re-auditar un CV cuyas líneas ya aprobó esa revisión (todas exentas)."""

    def __init__(self, auditor):
        self.auditor = auditor

    async def revisar_senales_ia(self, md):
        return []

    def audit(self, *a, **kw):
        return self.auditor.audit(*a, **kw)


async def _flujo_del_maestro(postulacion: dict, cv: str, auditor) -> "cv_maestro.FlujoCV":
    """El `FlujoCV` del CV (sin generar nada): su auditoría con los controles del maestro, `control_final` (lo mismo
    que frena el envío) y la exención de líneas aprobadas. Es el criterio con que el flujo aprueba un CV."""
    candidato = DATA_DIR / "candidato"
    maestro = (candidato / "experiencia.txt").read_text(encoding="utf-8")
    personales = (candidato / "personales.txt").read_text(encoding="utf-8")
    analisis = postulacion.get("analisis") if isinstance(postulacion.get("analisis"), dict) else {}
    idioma, fuente = idioma_de_cv(cv), ""
    if idioma == EN:
        # El maestro en inglés que ya existe: re-auditar no lo traduce de nuevo
        fuente, ruta = maestro, candidato / cv_maestro.MAESTRO_EN
        maestro = ruta.read_text(encoding="utf-8") if ruta.exists() else maestro
    flujo = cv_maestro.FlujoCV(
        maestro=maestro, maestro_fuente=fuente, personales=personales, analisis=analisis,
        oferta=postulacion.get("contenido_oferta") or "", titulo=(puesto_del_cv(cv) or "").upper(), idioma=idioma,
        auditor=auditor, anios_por_area=await asegurar_anios_por_area(),
        estructura=cv_maestro.estructura_cv.leer(candidato), control_final=lambda t: _motivos_texto_final(t, "CV"))
    flujo.keywords_cv = {str(k): str(v) for k, v in (analisis.get("keywords_cv") or {}).items()}
    return flujo


async def _auditar_como_el_maestro(flujo, cv: str, keywords: List[str], aprobadas: set,
                                   reporte: dict) -> Tuple[dict, list]:
    """(reporte, hallazgos) de `FlujoCV._auditar` sobre el CV; `aprobadas`: ids de las líneas que la revisión con IA
    ya aprobó (sus señales no cuentan mientras no cambien). Un CV simple (`tipo_cv` del reporte) se juzga como tal:
    con un cargo por empresa, sus encabezados son los que eligió el flujo."""
    b, _ = correccion_auditoria.borrador_de(cv)
    simple = flujo.estructura.cv_simple
    if reporte.get("tipo_cv") == cv_maestro.TIPO_SIMPLE and simple:
        b.simple = True
        if simple.un_cargo_por_empresa:
            b.cargos_simples = {bl.empresa: bl.encabezado.strip() for bl in b.experiencia if bl.empresa}
    flujo.aprobadas = {l.id: l.texto for l in b.editables() if l.id in aprobadas}
    return await flujo._auditar(b, keywords)


def _informe_correccion(reporte: dict, auditado: dict, hallazgos: list, cambiadas: Optional[set] = None) -> dict:
    """El dictamen nuevo con el criterio del flujo del maestro: aprobado si no quedan hallazgos (auditoría,
    controles del maestro y control final), con los datos de cómo se armó el CV del reporte anterior."""
    informe = {**{k: reporte[k] for k in _META_REPORTE if k in reporte}, **auditado, "approved": not hallazgos}
    if cambiadas is not None:
        informe["correccion_auditoria"] = sorted(cambiadas)
    if hallazgos:
        motivos = list(dict.fromkeys(h.motivo for h in hallazgos))
        informe.update(verdict="REQUIERE REMEDIACIÓN", hallazgos_unificados={
            **(informe.get("hallazgos_unificados") or {}), "requiere_accion": True, "lista_observaciones": motivos})
    return informe


def _no_cambio_el_cv(pid: int, cv: str) -> None:
    # La IA, la auditoría y el PDF ceden el control: si el CV cambió mientras tanto (el editor), no se pisa
    if (get_postulacion(pid) or {}).get("cv_markdown") != cv:
        raise HTTPException(status_code=409, detail="El CV cambió mientras se corregía: revisalo y volvé a corregir")


async def _cerrar_correccion(post_id: int, informe: dict, lineas: List[str]) -> dict:
    # El contenido o su dictamen cambió: el post cambia de versión y no queda aprobado
    radar_store.update_post(post_id, error=None, aprobado_at=None)
    aprobado = informe.get("approved") is not False
    logger.info(f"[Radar] Corrección de la auditoría del post {post_id}: líneas {lineas}, "
                f"{'aprobado' if aprobado else 'sigue sin aprobar'}")
    try:
        await radar_loop.notify_post(post_id)
    except Exception:
        logger.exception("[Radar] Falló la notificación de la corrección")
    return {"aprobado": aprobado, "lineas": lineas,
            "motivos": [] if aprobado else auditoria_envio.motivos_cortos(informe)}


async def _corregir_auditoria(post_id: int, postulacion: dict, reporte: dict) -> dict:
    pid, cv = postulacion["id"], postulacion["cv_markdown"]
    claves, _ = _criterios_auditoria(postulacion, reporte)
    b, ubicacion = correccion_auditoria.borrador_de(cv)
    lineas = {l.id for l in b.editables()}
    marcadas = {h.linea for h in correccion_auditoria.lineas_marcadas(b, ubicacion, reporte)}
    if _lineas_ya_revisadas(postulacion, reporte, marcadas):
        # Primero se re-audita con el criterio del flujo, con todas las líneas exentas de la revisión con IA (ya la
        # pasaron). Si pasa, queda aprobado sin IA y sin tocar el CV.
        flujo = await _flujo_del_maestro(postulacion, cv, _SinRevisionIA(_auditor_correccion()))
        auditado, hallazgos = await _auditar_como_el_maestro(flujo, cv, claves, lineas, reporte)
        if not hallazgos:
            informe = {**_informe_correccion(reporte, auditado, hallazgos), "reauditado_sin_ia": True}
            _no_cambio_el_cv(pid, cv)
            update_postulacion(pid, reporte_auditoria=informe, status="READY")
            return await _cerrar_correccion(post_id, informe, [])
    flujo = await _flujo_del_maestro(postulacion, cv, _auditor_correccion())
    try:
        nuevo, cambiadas = await correccion_auditoria.corregir(
            cv, reporte, claves, lambda pedido: flujo._ia_agy(pedido, effort=auditoria_cv.EFFORT_REMEDIACION,
                                                             timeout_s=auditoria_cv.TIMEOUT_REMEDIACION_S))
    except correccion_auditoria.SinCorreccion as exc:
        raise HTTPException(status_code=409, detail=f"{exc}: usá Regenerar o Enviar igual")
    # Las que la auditoría no marcó ya estaban aprobadas; las marcadas se vuelven a juzgar
    auditado, hallazgos = await _auditar_como_el_maestro(flujo, nuevo, claves, lineas - marcadas, reporte)
    informe = _informe_correccion(reporte, auditado, hallazgos, cambiadas)
    puesto = _puesto_export({**postulacion, "cv_markdown": nuevo})
    pdf = Path(await asyncio.to_thread(compile_markdown_to_pdf, nuevo, puesto, f"cv_correccion_{pid}.pdf"))
    if reporte.get("origen") == ORIGEN_CV_MAESTRO and await asyncio.to_thread(_paginas_pdf, pdf) > 1:
        observaciones = (informe.get("hallazgos_unificados") or {}).get("lista_observaciones") or []
        informe.update(approved=False, hallazgos_unificados={
            **(informe.get("hallazgos_unificados") or {}), "requiere_accion": True,
            "lista_observaciones": [*observaciones, "El CV corregido no entra en 1 página"]})
    _no_cambio_el_cv(pid, cv)
    campos = cv_final.guardar(pid, pdf, nuevo, generate_cv_pdf_filename(target_role=puesto))
    update_postulacion(pid, cv_markdown=nuevo, reporte_auditoria=informe, status="READY", **campos)
    return await _cerrar_correccion(post_id, informe, sorted(cambiadas))


# Getter (no la instancia): reemplazar `server.radar_loop` tiene efecto en las rutas
app.include_router(build_radar_router(lambda: radar_loop), dependencies=[Depends(_validar_calidad_envio)])


# Servir Frontend
class StaticSinCache(StaticFiles):
    """/static con «Cache-Control: no-cache»: el navegador revalida cada archivo con su ETag
    (304 sin cuerpo si no cambió), así un index.html nuevo nunca usa un app.js viejo guardado en la caché."""

    async def get_response(self, path, scope):
        respuesta = await super().get_response(path, scope)
        if respuesta.status_code in (200, 304):
            respuesta.headers["Cache-Control"] = "no-cache"
        return respuesta


if STATIC_DIR.exists():
    app.mount("/static", StaticSinCache(directory=str(STATIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
def serve_index():
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        # Sin caché: cada despliegue se ve al abrir el portal (veía el panel viejo)
        return HTMLResponse(content=index_file.read_text(encoding="utf-8"), headers={"Cache-Control": "no-cache"})
    return HTMLResponse("<h1>LinkedOut: backend en marcha</h1>")


@app.get("/sw.js")
def serve_service_worker():
    """Service worker de la app de escritorio (static/pwa/sw.js). Va en la raíz para controlar las navegaciones
    a "/"; sin caché, así cada deploy lo actualiza. Código público, sin datos (lista blanca de S1)."""
    return FileResponse(STATIC_DIR / "pwa" / "sw.js", media_type="text/javascript",
                        headers={"Cache-Control": "no-cache"})
