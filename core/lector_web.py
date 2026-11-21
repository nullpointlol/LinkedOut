"""Lector de páginas de terceros sin SSRF (M3 de docs/seguridad/auditoria-20261117.md).

Los links de las ofertas los escribe cualquiera que publica en LinkedIn o en un portal. Este lector:
- solo pide `http`/`https`;
- verifica la IP a la que realmente se conectó (no la que resolvió el DNS antes): si no es pública
  (loopback, red local, link-local, CGNAT, reservada) corta antes de mandar la request;
- hace lo mismo en cada redirección, con un tope (`MAX_REDIRECCIONES`);
- ignora los proxies del entorno (la IP verificada tiene que ser la del destino);
- lee como mucho `MAX_BYTES` (también descomprimido) y verifica el certificado TLS.
"""
import http.client
import ipaddress
import socket
import ssl
import urllib.request
import zlib
from dataclasses import dataclass
from email.message import Message
from typing import Dict, Optional

MAX_REDIRECCIONES = 5
# Una página de aviso pesa decenas de KiB; más que esto es basura o un intento de colgar la memoria
MAX_BYTES = 5 * 1024 * 1024
ESQUEMAS = ("http", "https")


class DestinoNoPermitido(ValueError):
    """El link apunta (o redirige) a algo que no es un sitio público de internet."""


@dataclass
class Respuesta:
    url: str
    cabeceras: Message
    cuerpo: bytes

    def texto(self) -> str:
        charset = self.cabeceras.get_content_charset() or "utf-8"
        try:
            return self.cuerpo.decode(charset, errors="replace")
        except LookupError:  # charset inventado por la página
            return self.cuerpo.decode("utf-8", errors="replace")


def destino_permitido(ip: str) -> bool:
    """True si la IP es de internet público."""
    try:
        direccion = ipaddress.ip_address(ip.split("%", 1)[0])
    except ValueError:
        return False
    mapeada = getattr(direccion, "ipv4_mapped", None)
    if mapeada is not None:
        direccion = mapeada
    return direccion.is_global and not direccion.is_multicast


def _conectar_verificado(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None, **kwargs):
    sock = socket.create_connection(address, timeout, source_address, **kwargs)
    ip = sock.getpeername()[0]
    if not destino_permitido(ip):
        sock.close()
        raise DestinoNoPermitido(f"destino no permitido: {address[0]} es una dirección no pública ({ip})")
    return sock


class _ConexionHTTP(http.client.HTTPConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = _conectar_verificado


class _ConexionHTTPS(http.client.HTTPSConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = _conectar_verificado  # antes del TLS: la IP se verifica sin handshake


class _ManejadorHTTP(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_ConexionHTTP, req)


class _ManejadorHTTPS(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_ConexionHTTPS, req, context=self._context)


class _Redirecciones(urllib.request.HTTPRedirectHandler):
    max_redirections = MAX_REDIRECCIONES

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _exigir_esquema(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def contexto_tls() -> ssl.SSLContext:
    return ssl.create_default_context()


def _exigir_esquema(url: str) -> None:
    esquema = urllib.request.urlparse(url).scheme.lower()
    if esquema not in ESQUEMAS:
        raise DestinoNoPermitido(f"destino no permitido: esquema «{esquema or 'vacío'}»")


def _abridor() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}), _ManejadorHTTP(), _ManejadorHTTPS(context=contexto_tls()), _Redirecciones(),
    )


# Content-Encoding → wbits de zlib (gzip con cabecera, deflate con o sin la de zlib)
_WBITS = {"gzip": 16 + zlib.MAX_WBITS, "x-gzip": 16 + zlib.MAX_WBITS, "deflate": zlib.MAX_WBITS}


def _descomprimido(cuerpo: bytes, codificacion: str) -> bytes:
    """El cuerpo según su Content-Encoding, que hay sitios que mandan sin pedirlo; uno desconocido no se lee como texto."""
    codificacion = (codificacion or "identity").strip().lower()
    if codificacion == "identity":
        return cuerpo
    if codificacion not in _WBITS:
        raise ValueError(f"Content-Encoding no soportado: {codificacion}")
    try:
        return zlib.decompressobj(_WBITS[codificacion]).decompress(cuerpo, MAX_BYTES)
    except zlib.error:
        if codificacion != "deflate":
            raise
        return zlib.decompressobj(-zlib.MAX_WBITS).decompress(cuerpo, MAX_BYTES)


def leer(url: str, cabeceras: Optional[Dict[str, str]] = None, timeout: float = 15) -> Respuesta:
    """GET a `url` con las reglas del módulo. Lanza DestinoNoPermitido, URLError/HTTPError u OSError."""
    _exigir_esquema(url)
    pedido = urllib.request.Request(url, headers=cabeceras or {})
    with _abridor().open(pedido, timeout=timeout) as r:
        cuerpo = _descomprimido(r.read(MAX_BYTES + 1)[:MAX_BYTES], r.headers.get("Content-Encoding"))
        return Respuesta(url=r.geturl(), cabeceras=r.headers, cuerpo=cuerpo)
