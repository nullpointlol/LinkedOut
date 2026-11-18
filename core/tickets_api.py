"""API de tickets (`/api/tickets`) y middleware que convierte en ticket los errores de cada petición.

Middleware (ASGI puro, no corta el streaming):
- excepción no manejada → ticket «excepcion» con traza y la petición (cuerpo sin secretos), y se relanza;
- respuesta 4xx/5xx de `/api/` → ticket «respuesta_4xx»/«respuesta_5xx» con el cuerpo de la respuesta;
- no registra `/api/tickets*` (evita bucles) ni el 404 de rutas que no existen (bots, escaneos);
- si durante la petición ya se creó un ticket (p. ej. un `logger.exception`), no se crea otro por la respuesta.
"""
import json
from typing import Any, Dict, Optional
from urllib.parse import parse_qsl

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field, field_validator

from core import tickets
from core.radar_api import _solo_pc_o_emparejado

MAX_CUERPO_PETICION = 64 * 1024
MAX_CUERPO_RESPUESTA = 4 * 1024
PREFIJO_PROPIO = "/api/tickets"


def _cuerpo_legible(crudo: bytes, tipo: str) -> Any:
    if not crudo:
        return None
    texto = crudo.decode("utf-8", errors="replace")
    if "json" in tipo:
        try:
            return json.loads(texto)
        except Exception:
            pass
    if "x-www-form-urlencoded" in tipo:
        return dict(parse_qsl(texto))
    if "multipart" in tipo:
        return f"(formulario con archivos, {len(crudo)} bytes)"
    return texto[:MAX_CUERPO_PETICION]


class MiddlewareTickets:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        ruta = scope.get("path", "")
        if ruta.startswith(PREFIJO_PROPIO):
            await self.app(scope, receive, send)
            return
        encabezados = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []}
        tipo_contenido = encabezados.get("content-type", "")
        info: Dict[str, Any] = {
            "metodo": scope.get("method", ""), "ruta": ruta,
            "consulta": (scope.get("query_string") or b"").decode("latin-1"), "cuerpo": None,
        }
        crudo = bytearray()
        respuesta = {"estado": None, "cuerpo": bytearray()}

        async def recibir():
            mensaje = await receive()
            if mensaje.get("type") == "http.request":
                if len(crudo) < MAX_CUERPO_PETICION:
                    crudo.extend(mensaje.get("body", b"")[:MAX_CUERPO_PETICION - len(crudo)])
                if not mensaje.get("more_body"):
                    info["cuerpo"] = _cuerpo_legible(bytes(crudo), tipo_contenido)
            return mensaje

        async def enviar(mensaje):
            if mensaje.get("type") == "http.response.start":
                respuesta["estado"] = int(mensaje.get("status", 0))
            elif mensaje.get("type") == "http.response.body" and (respuesta["estado"] or 0) >= 400:
                if len(respuesta["cuerpo"]) < MAX_CUERPO_RESPUESTA:
                    respuesta["cuerpo"].extend(mensaje.get("body", b"")[:MAX_CUERPO_RESPUESTA])
            await send(mensaje)

        contexto = {"info": info, "tickets": []}
        token = tickets.PETICION_EN_CURSO.set(contexto)
        try:
            try:
                await self.app(scope, recibir, enviar)
            except Exception as e:
                if crudo and info["cuerpo"] is None:
                    info["cuerpo"] = _cuerpo_legible(bytes(crudo), tipo_contenido)
                info["estado_http"] = 500
                if not contexto["tickets"]:
                    tickets.reportar_error("servidor", "excepcion", f"{type(e).__name__}: {e}", excepcion=e,
                                           peticion=info)
                raise
            estado = respuesta["estado"] or 0
            if estado < 400 or not ruta.startswith("/api/") or contexto["tickets"]:
                return
            if estado == 404 and "route" not in scope and "endpoint" not in scope:
                return  # ruta inexistente: bots y escaneos, no un error del portal
            info["estado_http"] = estado
            info["respuesta"] = _cuerpo_legible(bytes(respuesta["cuerpo"]), "json")
            detalle = info["respuesta"].get("detail") if isinstance(info["respuesta"], dict) else info["respuesta"]
            if not isinstance(detalle, str):
                detalle = json.dumps(detalle, ensure_ascii=False, default=str)[:300] if detalle else ""
            tipo = "respuesta_5xx" if estado >= 500 else "respuesta_4xx"
            tickets.reportar_error("api", tipo, f"{estado} en {info['metodo']} {ruta}: {detalle}".strip(": "),
                                   peticion=info)
        finally:
            tickets.PETICION_EN_CURSO.reset(token)


# ---------------------------------------------------------------- rutas

class TicketIn(BaseModel):
    mensaje: str = Field(max_length=20000)
    tipo: str = Field(default="reporte_usuario", max_length=60)
    componente: str = Field(default="navegador", max_length=60)
    traza: Optional[str] = Field(default=None, max_length=50000)
    datos: Optional[Dict[str, Any]] = None
    captura: Optional[str] = Field(default=None, max_length=12 * 1024 * 1024)
    html: Optional[str] = Field(default=None, max_length=6 * 1024 * 1024)

    @field_validator("mensaje")
    @classmethod
    def _no_vacio(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Contanos qué pasó.")
        return v.strip()


class EstadoIn(BaseModel):
    estado: str

    @field_validator("estado")
    @classmethod
    def _valido(cls, v: str) -> str:
        if v not in tickets.ESTADOS:
            raise ValueError(f"Estado inválido. Válidos: {', '.join(tickets.ESTADOS)}")
        return v


def _ticket_o_404(tid: int) -> Dict[str, Any]:
    t = tickets.obtener(tid)
    if not t:
        raise HTTPException(status_code=404, detail="Ese ticket no existe.")
    return t


def build_tickets_router() -> APIRouter:
    # Los tickets guardan trazas, capturas y HTML con datos del candidato: solo la PC o el celular emparejado
    router = APIRouter(prefix=PREFIJO_PROPIO, dependencies=[Depends(_solo_pc_o_emparejado)])

    @router.get("")
    def api_listar(estado: Optional[str] = None, componente: Optional[str] = None, tipo: Optional[str] = None):
        return tickets.listar(estado=estado or None, componente=componente or None, tipo=tipo or None)

    @router.get("/resumen")
    def api_resumen():
        return tickets.resumen()

    @router.post("")
    def api_crear(payload: TicketIn):
        tid = tickets.reportar_error(payload.componente, payload.tipo, payload.mensaje, traza=payload.traza,
                                     datos=payload.datos, captura=payload.captura or None,
                                     html=payload.html or None)
        if not tid:
            raise HTTPException(status_code=500, detail="No se pudo guardar el ticket.")
        return {"id": tid}

    @router.get("/{tid}")
    def api_detalle(tid: int):
        t = _ticket_o_404(tid)
        return {**t, "texto": tickets.texto_ticket(t)}

    @router.post("/{tid}/estado")
    def api_estado(tid: int, payload: EstadoIn):
        _ticket_o_404(tid)
        tickets.cambiar_estado(tid, payload.estado)
        return _ticket_o_404(tid)

    @router.get("/{tid}/captura")
    def api_captura(tid: int):
        ruta = tickets.ruta_captura(tid)
        if not ruta:
            raise HTTPException(status_code=404, detail="Este ticket no tiene captura.")
        return FileResponse(str(ruta), media_type="image/png", headers={"Cache-Control": "no-cache"})

    @router.get("/{tid}/html")
    def api_html(tid: int):
        ruta = tickets.ruta_html(tid)
        if not ruta:
            raise HTTPException(status_code=404, detail="Este ticket no tiene HTML.")
        # Texto plano: el HTML capturado (p. ej. de LinkedIn) nunca se ejecuta en el portal
        return Response(ruta.read_text(encoding="utf-8", errors="replace"), media_type="text/plain; charset=utf-8",
                        headers={"Content-Disposition": f'inline; filename="ticket-{tid}.html.txt"',
                                 "X-Content-Type-Options": "nosniff"})

    return router
