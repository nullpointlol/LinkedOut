"""Acceso del celular al Radar: emparejamiento con una clave secreta guardada en la carpeta de datos.

La PC muestra un QR con `http://<IP de la PC>:<puerto>/api/radar/emparejar?clave=…`. Al abrirlo,
el celular recibe la cookie `radar_acceso` (HttpOnly, SameSite=Strict, un año) y desde ahí
la guarda del Radar le acepta los cambios como a la propia PC. Regenerar la clave borra el
archivo y desempareja todos los dispositivos.
"""
import secrets
import socket
from pathlib import Path
from typing import Optional

from core.paths import DATA_DIR

COOKIE = "radar_acceso"
DURACION_COOKIE = 365 * 24 * 3600
ARCHIVO_CLAVE = DATA_DIR / "acceso_celular.key"


def clave(archivo: Path = None, crear: bool = True) -> Optional[str]:
    """La clave de emparejamiento; la crea la primera vez (salvo `crear=False`)."""
    archivo = archivo or ARCHIVO_CLAVE
    try:
        guardada = archivo.read_text(encoding="utf-8").strip()
        if guardada:
            return guardada
    except FileNotFoundError:
        pass
    if not crear:
        return None
    nueva = secrets.token_urlsafe(24)
    archivo.parent.mkdir(parents=True, exist_ok=True)
    archivo.write_text(nueva, encoding="utf-8")
    return nueva


def regenerar(archivo: Path = None) -> str:
    """Nueva clave: los dispositivos emparejados con la anterior dejan de poder operar."""
    archivo = archivo or ARCHIVO_CLAVE
    archivo.unlink(missing_ok=True)
    return clave(archivo)


def es_valida(valor: Optional[str], archivo: Path = None) -> bool:
    esperada = clave(archivo, crear=False)
    return bool(valor and esperada) and secrets.compare_digest(valor.encode(), esperada.encode())


def ip_lan() -> Optional[str]:
    """IP de la PC en la red local (la de la interfaz con salida a internet). No envía paquetes."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))  # UDP no conecta de verdad: solo elige la interfaz
            ip = s.getsockname()[0]
    except OSError:
        return None
    return None if ip.startswith("127.") or ip == "0.0.0.0" else ip


def qr_svg(texto: str) -> str:
    import qrcode
    import qrcode.image.svg

    img = qrcode.make(texto, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2)
    return img.to_string(encoding="unicode")
