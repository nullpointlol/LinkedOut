"""Envío de postulaciones por Gmail (SMTP SSL) con errores tipados.

Módulo síncrono: quien lo use desde código async debe envolverlo en
``asyncio.to_thread``. Nunca imprime ni registra la contraseña de aplicación.
"""
import logging
import os
import re
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.policy import SMTP as SMTP_POLICY
from email.utils import formataddr, make_msgid
from pathlib import Path
from typing import Callable, Optional

from dotenv import load_dotenv

from core.paths import variable_entorno

logger = logging.getLogger(__name__)

# Raíz del proyecto (a nivel de módulo para poder sustituirla en tests).
BASE_DIR = Path(__file__).resolve().parent.parent

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465
SMTP_TIMEOUT = 30


# Modo de prueba: con esta variable definida (en `.env`, sin versionar), todo envío va a esa dirección y
# no al destinatario real, y el asunto lleva MARCA_PRUEBA. El código no tiene ninguna dirección escrita.
VARIABLE_MAIL_PRUEBA = "LINKEDOUT_MAIL_PRUEBA"
MARCA_PRUEBA = "[PRUEBA]"
_RE_DIRECCION = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True)
class GmailConfig:
    address: str
    app_password: str
    mail_prueba: Optional[str] = None


class MailerError(Exception):
    """Fallo permanente de envío (no tiene sentido reintentar)."""


class MailerAuthError(MailerError):
    """Credenciales de Gmail rechazadas: hay que pausar todos los envíos."""


class MailerTemporaryError(MailerError):
    """Fallo transitorio ANTES de entregar el mensaje (conexión/login caídos, respuesta 4xx
    explícita del servidor): el mensaje seguro no salió, se puede reintentar con backoff."""


class MailerAmbiguousError(MailerError):
    """La conexión se cortó (desconexión, timeout, error de red) mientras se transmitía el
    mensaje: no se sabe si Gmail lo aceptó. Nunca se reintenta solo (riesgo de email duplicado);
    el usuario verifica en Gmail (Enviados) antes de reintentar."""


def ruta_env() -> Path:
    """El `.env` del portal: `LINKEDOUT_ENV_FILE` (producción la fija en su arranque). Sin la variable,
    `<repo>/dev.env`: nunca `<repo>/.env`, que en el checkout principal es el de producción."""
    return Path(variable_entorno("ENV_FILE") or (BASE_DIR / "dev.env"))


def load_gmail_config() -> Optional[GmailConfig]:
    """Lee GMAIL_ADDRESS y GMAIL_APP_PASSWORD (de `.env` o del entorno).

    `LINKEDOUT_ENV_FILE` apunta a otro `.env` (igual que `LINKEDOUT_DATA_DIR` con los datos):
    así una copia de prueba del código usa la configuración real sin duplicar la clave.
    Devuelve None si falta cualquiera de las dos.
    """
    # override=True: "Reintentar envíos" tras corregir el .env debe tomar la clave nueva sin reiniciar
    load_dotenv(ruta_env(), override=True)
    address = (os.environ.get("GMAIL_ADDRESS") or "").strip()
    # Google muestra la contraseña de aplicación en grupos ("abcd efgh ijkl mnop"): sin espacios
    password = "".join((os.environ.get("GMAIL_APP_PASSWORD") or "").split())
    if not address or not password:
        return None
    prueba = (variable_entorno("MAIL_PRUEBA") or "").strip() or None
    return GmailConfig(address=address, app_password=password, mail_prueba=prueba)


def destino_y_asunto(to: str, subject: str, config: GmailConfig) -> tuple:
    """(destinatario, asunto) que se usan de verdad: en modo de prueba, la dirección de prueba y el asunto
    con MARCA_PRUEBA. Una dirección de prueba mal escrita es MailerError: nunca se cae al destinatario real."""
    prueba = config.mail_prueba
    if prueba is None:
        return to, subject
    if not _RE_DIRECCION.match(prueba):
        raise MailerError(f"{VARIABLE_MAIL_PRUEBA} no es una dirección de mail válida: no se envía nada")
    if not subject.startswith(MARCA_PRUEBA):
        subject = f"{MARCA_PRUEBA} {subject}".strip()
    return prueba, subject


def build_message(
    from_name: str,
    from_addr: str,
    to: str,
    subject: str,
    body: str,
    pdf_path: Path,
    html_body: Optional[str] = None,
) -> EmailMessage:
    """Arma el mensaje con el CV adjunto.

    La política SMTP codifica asunto y nombre de archivo con acentos
    (RFC 2047 / RFC 2231) para que lleguen legibles.
    """
    pdf_path = Path(pdf_path)
    msg = EmailMessage(policy=SMTP_POLICY)
    msg["From"] = formataddr((from_name, from_addr)) if from_name else from_addr
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain="gmail.com")
    msg.set_content(body)
    if html_body:
        # Texto plano + HTML (con los links de la firma ocultos tras el número y "LinkedIn")
        msg.add_alternative(html_body, subtype="html")
    msg.add_attachment(
        pdf_path.read_bytes(),
        maintype="application",
        subtype="pdf",
        filename=pdf_path.name,
    )
    return msg


def _error_pre_envio(e: BaseException) -> MailerError:
    """Mapea un fallo de conexión/login (el mensaje todavía no se transmitió)."""
    if isinstance(e, smtplib.SMTPAuthenticationError):
        return MailerAuthError(f"Gmail rechazó las credenciales: {e.smtp_code}")
    if isinstance(e, smtplib.SMTPResponseException):  # incluye SMTPConnectError / SMTPHeloError
        if 400 <= e.smtp_code < 500:
            return MailerTemporaryError(f"Error SMTP temporal {e.smtp_code}")
        return MailerError(f"Error SMTP {e.smtp_code}")
    if isinstance(e, smtplib.SMTPServerDisconnected):
        return MailerTemporaryError(f"Conexión SMTP caída: {e}")
    if isinstance(e, smtplib.SMTPException):
        # SMTPException hereda de OSError: debe evaluarse antes que OSError.
        return MailerError(f"Error SMTP: {e}")
    return MailerTemporaryError(f"Error de red: {e}")


def _error_en_envio(e: BaseException) -> MailerError:
    """Mapea un fallo dentro de `send_message` (MAIL/RCPT/DATA)."""
    if isinstance(e, smtplib.SMTPResponseException):  # respuesta explícita: el servidor no lo aceptó
        if 400 <= e.smtp_code < 500:
            return MailerTemporaryError(f"Error SMTP temporal {e.smtp_code}")
        return MailerError(f"Error SMTP {e.smtp_code}")
    if isinstance(e, smtplib.SMTPServerDisconnected):
        return MailerAmbiguousError(f"Conexión SMTP caída durante el envío: {e}")
    if isinstance(e, smtplib.SMTPException):  # SMTPRecipientsRefused, etc.: rechazo explícito
        return MailerError(f"Error SMTP: {e}")
    # OSError / TimeoutError: sin respuesta del servidor, no se sabe si lo aceptó
    return MailerAmbiguousError(f"Error de red durante el envío: {e}")


def _cerrar(smtp: smtplib.SMTP) -> None:
    """QUIT + close sin propagar nada: el resultado del envío ya está decidido."""
    try:
        smtp.quit()
    except Exception as e:
        logger.info(f"[Mailer] QUIT falló (se ignora): {e}")
        try:
            smtp.close()
        except Exception as e2:
            logger.info(f"[Mailer] close falló (se ignora): {e2}")


def send_application_email(
    to: str,
    subject: str,
    body: str,
    pdf_path: Path,
    from_name: str,
    config: GmailConfig,
    html_body: Optional[str] = None,
    *,
    before_send: Optional[Callable[[], None]] = None,
) -> str:
    """Envía la postulación y devuelve el Message-ID.

    En modo de prueba (`config.mail_prueba`) va a esa dirección con el asunto marcado (`destino_y_asunto`).

    Errores: MailerAuthError (credenciales), MailerTemporaryError (no se entregó; reintentable),
    MailerAmbiguousError (se cortó mientras se transmitía: puede haberse entregado; no reintentar
    solo), MailerError (resto). Una vez que `send_message` volvió sin error el mensaje está
    entregado: cualquier fallo posterior (QUIT distinto de 221, error al cerrar) se registra y se
    devuelve igual el Message-ID, así el radar nunca lo reenvía.

    Si se proporciona `before_send`, se invoca una vez tras el login exitoso e inmediatamente
    antes de `send_message`. Si lanza una excepción, no se entrega el mensaje, la conexión se
    cierra mediante `finally` y se propaga la misma excepción sin traducir.
    """
    to, subject = destino_y_asunto(to, subject, config)
    msg = build_message(from_name, config.address, to, subject, body, pdf_path, html_body=html_body)
    message_id = str(msg["Message-ID"])
    try:
        smtp = smtplib.SMTP_SSL(
            SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT, context=ssl.create_default_context()
        )
    except Exception as e:
        raise _error_pre_envio(e) from e

    try:
        try:
            smtp.login(config.address, config.app_password)
        except Exception as e:
            raise _error_pre_envio(e) from e
        if before_send is not None:
            before_send()
        try:
            smtp.send_message(msg)
        except Exception as e:
            raise _error_en_envio(e) from e
        # Entregado: desde acá nada puede convertir el envío en error (QUIT/close no propagan)
    finally:
        _cerrar(smtp)
    logger.info(f"[Mailer] Email entregado a Gmail ({message_id})")
    return message_id
