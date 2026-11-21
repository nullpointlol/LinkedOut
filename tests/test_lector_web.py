"""`core/lector_web.py` (M3 de docs/seguridad/auditoria-20261117.md): lector de páginas de terceros sin SSRF.

Los links de las ofertas los escribe cualquiera. El lector se niega a conectarse a la red local o a la propia PC,
también tras una redirección, corta respuestas enormes y verifica TLS. Los servidores de prueba son reales,
en 127.0.0.1 y 127.0.0.2 (loopback); los tests habilitan solo los que necesitan.
"""
import gzip
import ssl
import threading
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from core import lector_web, scraper


class _Servidor:
    """Servidor HTTP real en `host`; `rutas` = {path: (código, cabeceras, cuerpo)}."""

    def __init__(self, host, rutas):
        self.pedidos = []
        servidor = self

        class Manejador(BaseHTTPRequestHandler):
            def do_GET(self):
                servidor.pedidos.append((self.path, self.headers.get("User-Agent")))
                codigo, cabeceras, cuerpo = rutas.get(self.path, (404, {}, b""))
                self.send_response(codigo)
                for k, v in cabeceras.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(cuerpo)))
                self.end_headers()
                self.wfile.write(cuerpo)

            def log_message(self, *a):
                pass

        self.http = ThreadingHTTPServer((host, 0), Manejador)
        self.url = f"http://{host}:{self.http.server_address[1]}"
        self.hilo = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.hilo.start()

    def cerrar(self):
        self.http.shutdown()
        self.http.server_close()
        self.hilo.join(timeout=10)


@pytest.fixture
def servidores():
    abiertos = []

    def crear(host, rutas):
        s = _Servidor(host, rutas)
        abiertos.append(s)
        return s

    yield crear
    for s in abiertos:
        s.cerrar()


def _permitir(monkeypatch, *ips):
    """Trata como públicas solo estas IPs de loopback (el resto sigue la regla real)."""
    real = lector_web.destino_permitido
    monkeypatch.setattr(lector_web, "destino_permitido", lambda ip: ip in ips or real(ip))


@pytest.mark.parametrize("ip", [
    "127.0.0.1", "::1", "10.0.0.5", "172.16.3.4", "192.168.1.1", "169.254.169.254", "100.64.0.1",
    "0.0.0.0", "::ffff:127.0.0.1", "fe80::1", "fc00::1", "224.0.0.1",
])
def test_rechaza_ips_no_publicas(ip):
    assert lector_web.destino_permitido(ip) is False


def test_rechaza_lo_que_no_es_una_ip():
    assert lector_web.destino_permitido("no-es-ip") is False


def test_charset_inventado_cae_a_utf8():
    from email.message import Message
    cabeceras = Message()
    cabeceras["Content-Type"] = "text/html; charset=no-existe"
    assert lector_web.Respuesta("u", cabeceras, "ñ".encode("utf-8")).texto() == "ñ"


@pytest.mark.parametrize("ip", ["8.8.8.8", "151.101.1.69", "2606:4700::1111"])
def test_acepta_ips_publicas(ip):
    assert lector_web.destino_permitido(ip) is True


def test_no_se_conecta_a_la_propia_pc(servidores):
    s = servidores("127.0.0.1", {"/api/candidato/perfil/x": (200, {}, b"datos del candidato")})
    with pytest.raises(lector_web.DestinoNoPermitido):
        lector_web.leer(s.url + "/api/candidato/perfil/x", {}, timeout=5)
    assert s.pedidos == []  # ni siquiera llegó la request


def test_no_sigue_una_redireccion_a_la_red_local(servidores, monkeypatch):
    interno = servidores("127.0.0.2", {"/secreto": (200, {}, b"interno")})
    externo = servidores("127.0.0.1", {"/aviso": (302, {"Location": interno.url + "/secreto"}, b"")})
    _permitir(monkeypatch, "127.0.0.1")
    with pytest.raises(lector_web.DestinoNoPermitido):
        lector_web.leer(externo.url + "/aviso", {}, timeout=5)
    assert [p for p, _ in externo.pedidos] == ["/aviso"]
    assert interno.pedidos == []


def test_sigue_redirecciones_permitidas_con_tope(servidores, monkeypatch):
    rutas = {f"/r{i}": (302, {"Location": f"/r{(i + 1) % 20}"}, b"") for i in range(20)}
    rutas["/r2"] = (200, {"Content-Type": "text/html"}, b"<p>aviso</p>")
    s = servidores("127.0.0.1", rutas)
    _permitir(monkeypatch, "127.0.0.1")
    assert lector_web.leer(s.url + "/r0", {}, timeout=5).cuerpo == b"<p>aviso</p>"

    rutas["/r2"] = (302, {"Location": "/r3"}, b"")
    with pytest.raises(Exception):
        lector_web.leer(s.url + "/r0", {}, timeout=5)
    assert len(s.pedidos) <= 3 + lector_web.MAX_REDIRECCIONES + 1


def test_no_sigue_redirecciones_a_otros_esquemas(servidores, monkeypatch):
    s = servidores("127.0.0.1", {"/aviso": (302, {"Location": "file:///C:/Windows/win.ini"}, b"")})
    _permitir(monkeypatch, "127.0.0.1")
    with pytest.raises(Exception):
        lector_web.leer(s.url + "/aviso", {}, timeout=5)


@pytest.mark.parametrize("url", ["file:///C:/Windows/win.ini", "ftp://acme.example/a", "gopher://x/"])
def test_solo_http_y_https(url):
    with pytest.raises(lector_web.DestinoNoPermitido):
        lector_web.leer(url, {}, timeout=5)


def test_corta_una_respuesta_enorme(servidores, monkeypatch):
    s = servidores("127.0.0.1", {"/grande": (200, {}, b"x" * 5000)})
    _permitir(monkeypatch, "127.0.0.1")
    monkeypatch.setattr(lector_web, "MAX_BYTES", 1000)
    assert lector_web.leer(s.url + "/grande", {}, timeout=5).cuerpo == b"x" * 1000


@pytest.mark.parametrize("codificacion, cuerpo", [
    ("gzip", gzip.compress("<p>Aviso</p>".encode())),
    ("deflate", zlib.compress("<p>Aviso</p>".encode())),
    ("deflate", zlib.compress("<p>Aviso</p>".encode())[2:-4]),
], ids=["gzip", "deflate", "deflate-crudo"])
def test_descomprime_aunque_no_lo_pida(servidores, monkeypatch, codificacion, cuerpo):
    s = servidores("127.0.0.1", {"/aviso": (200, {"Content-Encoding": codificacion}, cuerpo)})
    _permitir(monkeypatch, "127.0.0.1")
    assert lector_web.leer(s.url + "/aviso", {}, timeout=5).texto() == "<p>Aviso</p>"


@pytest.mark.parametrize("codificacion", ["br", "gzip"])
def test_codificacion_ilegible_no_devuelve_binario(servidores, monkeypatch, codificacion):
    s = servidores("127.0.0.1", {"/aviso": (200, {"Content-Encoding": codificacion}, b"\x8b\x00")})
    _permitir(monkeypatch, "127.0.0.1")
    with pytest.raises((ValueError, zlib.error)):
        lector_web.leer(s.url + "/aviso", {}, timeout=5)


def test_corta_una_respuesta_comprimida_enorme(servidores, monkeypatch):
    s = servidores("127.0.0.1", {"/grande": (200, {"Content-Encoding": "gzip"}, gzip.compress(b"x" * 5000))})
    _permitir(monkeypatch, "127.0.0.1")
    monkeypatch.setattr(lector_web, "MAX_BYTES", 1000)
    assert lector_web.leer(s.url + "/grande", {}, timeout=5).cuerpo == b"x" * 1000


def test_tls_verificado():
    ctx = lector_web.contexto_tls()
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


def test_ignora_los_proxies_del_entorno(servidores, monkeypatch):
    """Un proxy haría que la IP verificada sea la del proxy y no la del destino."""
    s = servidores("127.0.0.1", {"/aviso": (200, {}, b"ok")})
    monkeypatch.setenv("HTTP_PROXY", "http://203.0.113.1:9")
    monkeypatch.setenv("HTTPS_PROXY", "http://203.0.113.1:9")
    _permitir(monkeypatch, "127.0.0.1")
    assert lector_web.leer(s.url + "/aviso", {}, timeout=5).cuerpo == b"ok"


@pytest.mark.parametrize("charset, cuerpo, esperado", [
    ("latin-1", "Análisis".encode("latin-1"), "Análisis"),
    (None, "Análisis".encode("utf-8"), "Análisis"),
])
def test_el_scraper_usa_el_lector_con_charset_y_cabeceras(servidores, monkeypatch, charset, cuerpo, esperado):
    tipo = f"text/html; charset={charset}" if charset else "text/html"
    s = servidores("127.0.0.1", {"/aviso": (200, {"Content-Type": tipo}, cuerpo)})
    _permitir(monkeypatch, "127.0.0.1")
    assert scraper._fetch_url_sync(s.url + "/aviso", timeout=3) == esperado
    assert s.pedidos == [("/aviso", scraper.HEADERS["User-Agent"])]


@pytest.mark.asyncio
async def test_scrape_de_un_link_a_la_pc_no_trae_su_contenido(servidores):
    s = servidores("127.0.0.1", {"/api/candidato/perfil/x": (200, {}, b"<p>datos del candidato</p>")})
    res = await scraper.scrape_job_url(s.url + "/api/candidato/perfil/x")
    assert "datos del candidato" not in res["contenido"]
    assert res.get("error")
    assert s.pedidos == []
