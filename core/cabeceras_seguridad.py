"""Cabeceras contra clickjacking en toda respuesta (M4 de docs/seguridad/auditoria-20261117.md).

Sin ellas, una página abierta en la misma PC podía poner el portal en un iframe invisible y hacer tocar «Aprobar»:
las requests del iframe son del mismo origen y pasan la guarda. Solo prohibir el
iframe; una CSP de scripts espera al rediseño (el portal usa muchos onclick inline).
"""
from starlette.datastructures import MutableHeaders

CABECERAS = {
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "frame-ancestors 'none'",
}


class CabecerasSeguridad:
    """Middleware ASGI puro: no toca el cuerpo, así que el SSE (`/api/stream`) sigue en streaming."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        async def enviar(mensaje):
            if mensaje["type"] == "http.response.start":
                cabeceras = MutableHeaders(scope=mensaje)
                for nombre, valor in CABECERAS.items():
                    if nombre not in cabeceras:
                        cabeceras[nombre] = valor
            await send(mensaje)

        await self.app(scope, receive, enviar)
