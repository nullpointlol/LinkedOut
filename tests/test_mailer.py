import email
import email.policy
import smtplib

import pytest

from core.mailer import (
    GmailConfig,
    MailerAmbiguousError,
    MailerAuthError,
    MailerError,
    MailerTemporaryError,
    build_message,
    load_gmail_config,
    send_application_email,
)


def _pdf(tmp_path, name="CV - Alex Rivera - ANALISTA.pdf"):
    pdf = tmp_path / name
    pdf.write_bytes(b"%PDF-1.4 test")
    return pdf


def test_build_message_headers_and_attachment(tmp_path):
    pdf = _pdf(tmp_path)
    msg = build_message("Alex Rivera", "me@correo-ficticio.test", "rrhh@acme.test", "Postulación – Analista Funcional", "Hola,\nCuerpo", pdf)
    raw = msg.as_bytes()
    parsed = email.message_from_bytes(raw, policy=email.policy.default)
    assert parsed["To"] == "rrhh@acme.test"
    assert str(parsed["Subject"]) == "Postulación – Analista Funcional"
    assert "Alex Rivera" in str(parsed["From"])
    att = next(parsed.iter_attachments())
    assert att.get_content_type() == "application/pdf"
    assert att.get_filename() == "CV - Alex Rivera - ANALISTA.pdf"
    assert att.get_content() == b"%PDF-1.4 test"
    assert parsed.get_body(("plain",)).get_content().startswith("Hola,")
    assert parsed["Message-ID"]


def test_build_message_with_html_part_keeps_plain_text_and_attachment(tmp_path):
    msg = build_message("Ana", "me@correo-ficticio.test", "rrhh@acme.test", "S", "Hola\nLinkedIn: https://x",
                        _pdf(tmp_path), html_body='Hola<br><a href="https://x">LinkedIn</a>')
    parsed = email.message_from_bytes(msg.as_bytes(), policy=email.policy.default)
    assert parsed.get_body(("plain",)).get_content().startswith("Hola")
    assert '<a href="https://x">LinkedIn</a>' in parsed.get_body(("html",)).get_content()
    assert next(parsed.iter_attachments()).get_content_type() == "application/pdf"


def _fake_smtp(sent, login_exc=None, init_exc=None, send_exc=None, quit_exc=None, close_exc=None):
    class FakeSMTP:
        def __init__(self, host, port, timeout=None, context=None):
            if init_exc:
                raise init_exc
            sent["hp"] = (host, port)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def login(self, u, p):
            if login_exc:
                raise login_exc
            sent["login"] = (u, p)

        def send_message(self, m):
            if send_exc:
                raise send_exc
            sent["msg"] = m

        def quit(self):
            sent["quit"] = True
            if quit_exc:
                raise quit_exc
            self.close()
            return (221, b"bye")

        def close(self):
            sent["closed"] = True
            if close_exc:
                raise close_exc

    return FakeSMTP


def test_send_uses_ssl_465_and_returns_message_id(tmp_path, monkeypatch):
    sent = {}
    monkeypatch.setattr("core.mailer.smtplib.SMTP_SSL", _fake_smtp(sent))
    mid = send_application_email("rrhh@acme.test", "S", "B", _pdf(tmp_path), "N", GmailConfig("me@correo-ficticio.test", "pw"))
    assert sent["hp"] == ("smtp.gmail.com", 465) and sent["login"] == ("me@correo-ficticio.test", "pw")
    assert mid == sent["msg"]["Message-ID"]
    assert mid.endswith("@gmail.com>")


def test_auth_error_is_typed(tmp_path, monkeypatch):
    fake = _fake_smtp({}, login_exc=smtplib.SMTPAuthenticationError(535, b"x"))
    monkeypatch.setattr("core.mailer.smtplib.SMTP_SSL", fake)
    with pytest.raises(MailerAuthError):
        send_application_email("rrhh@acme.test", "S", "B", _pdf(tmp_path), "N", GmailConfig("me@correo-ficticio.test", "pw"))


def test_temporary_error_is_typed(tmp_path, monkeypatch):
    fake = _fake_smtp({}, init_exc=smtplib.SMTPConnectError(421, b"x"))
    monkeypatch.setattr("core.mailer.smtplib.SMTP_SSL", fake)
    with pytest.raises(MailerTemporaryError):
        send_application_email("rrhh@acme.test", "S", "B", _pdf(tmp_path), "N", GmailConfig("me@correo-ficticio.test", "pw"))


@pytest.mark.parametrize(
    "exc, expected",
    [
        # Sin respuesta del servidor mientras se transmitía: puede haberse entregado
        (smtplib.SMTPServerDisconnected("x"), MailerAmbiguousError),
        (OSError("red caída"), MailerAmbiguousError),
        (TimeoutError("timeout"), MailerAmbiguousError),
        # Respuesta explícita del servidor: no se entregó
        (smtplib.SMTPResponseException(451, b"try later"), MailerTemporaryError),
        (smtplib.SMTPDataError(451, b"try later"), MailerTemporaryError),
        (smtplib.SMTPResponseException(550, b"no"), MailerError),
        (smtplib.SMTPRecipientsRefused({"a@b.c": (550, b"no")}), MailerError),
    ],
)
def test_send_error_mapping(tmp_path, monkeypatch, exc, expected):
    monkeypatch.setattr("core.mailer.smtplib.SMTP_SSL", _fake_smtp({}, send_exc=exc))
    with pytest.raises(MailerError) as info:
        send_application_email("rrhh@acme.test", "S", "B", _pdf(tmp_path), "N", GmailConfig("me@correo-ficticio.test", "pw"))
    assert type(info.value) is expected


def test_disconnect_during_send_message_is_ambiguous_not_temporary(tmp_path, monkeypatch):
    """Un corte durante send_message nunca debe mapearse a reintentable (riesgo de duplicado)."""
    sent = {}
    monkeypatch.setattr("core.mailer.smtplib.SMTP_SSL", _fake_smtp(sent, send_exc=smtplib.SMTPServerDisconnected("eof")))
    with pytest.raises(MailerAmbiguousError) as info:
        send_application_email("rrhh@acme.test", "S", "B", _pdf(tmp_path), "N", GmailConfig("me@correo-ficticio.test", "pw"))
    assert not isinstance(info.value, MailerTemporaryError)
    assert sent.get("quit") or sent.get("closed")


@pytest.mark.parametrize(
    "quit_exc, close_exc",
    [
        (smtplib.SMTPResponseException(451, b"quit raro"), None),  # QUIT != 221
        (smtplib.SMTPServerDisconnected("eof"), None),
        (OSError("reset"), OSError("close falló")),
    ],
)
def test_delivered_then_quit_or_close_fails_returns_message_id(tmp_path, monkeypatch, quit_exc, close_exc):
    sent = {}
    fake = _fake_smtp(sent, quit_exc=quit_exc, close_exc=close_exc)
    monkeypatch.setattr("core.mailer.smtplib.SMTP_SSL", fake)
    mid = send_application_email("rrhh@acme.test", "S", "B", _pdf(tmp_path), "N", GmailConfig("me@correo-ficticio.test", "pw"))
    assert mid == sent["msg"]["Message-ID"]


@pytest.mark.parametrize(
    "init_exc",
    [smtplib.SMTPConnectError(421, b"x"), OSError("sin red"), TimeoutError("timeout"), smtplib.SMTPServerDisconnected("x")],
)
def test_connect_failures_are_temporary(tmp_path, monkeypatch, init_exc):
    monkeypatch.setattr("core.mailer.smtplib.SMTP_SSL", _fake_smtp({}, init_exc=init_exc))
    with pytest.raises(MailerTemporaryError):
        send_application_email("rrhh@acme.test", "S", "B", _pdf(tmp_path), "N", GmailConfig("me@correo-ficticio.test", "pw"))


@pytest.mark.parametrize("login_exc", [smtplib.SMTPServerDisconnected("x"), OSError("reset")])
def test_login_phase_network_failures_are_temporary(tmp_path, monkeypatch, login_exc):
    monkeypatch.setattr("core.mailer.smtplib.SMTP_SSL", _fake_smtp({}, login_exc=login_exc))
    with pytest.raises(MailerTemporaryError):
        send_application_email("rrhh@acme.test", "S", "B", _pdf(tmp_path), "N", GmailConfig("me@correo-ficticio.test", "pw"))


def test_load_gmail_config_missing(monkeypatch, tmp_path):
    monkeypatch.delenv("GMAIL_ADDRESS", raising=False)
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)
    monkeypatch.setattr("core.mailer.BASE_DIR", tmp_path)
    assert load_gmail_config() is None


def test_load_gmail_config_from_env(monkeypatch, tmp_path):
    monkeypatch.setattr("core.mailer.BASE_DIR", tmp_path)
    monkeypatch.setenv("GMAIL_ADDRESS", "me@correo-ficticio.test")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "abcd efgh")
    # Los espacios de la contraseña de aplicación (como la muestra Google) se quitan
    assert load_gmail_config() == GmailConfig("me@correo-ficticio.test", "abcdefgh")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "  abcd efgh	ijkl mnop ")
    assert load_gmail_config() == GmailConfig("me@correo-ficticio.test", "abcdefghijklmnop")
    monkeypatch.delenv("GMAIL_APP_PASSWORD")
    assert load_gmail_config() is None


def test_load_gmail_config_rereads_env_file_after_edit(monkeypatch, tmp_path):
    """Corregir la clave en `.env` y reintentar debe usar la nueva sin reiniciar el proceso."""
    # setenv (no delenv) para que monkeypatch registre las variables y las deshaga al terminar:
    # load_dotenv escribe en os.environ directamente y no debe contaminar otros tests.
    monkeypatch.setenv("GMAIL_ADDRESS", "placeholder")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "placeholder")
    monkeypatch.delenv("LINKEDOUT_ENV_FILE", raising=False)
    monkeypatch.setattr("core.mailer.BASE_DIR", tmp_path)
    # Sin LINKEDOUT_ENV_FILE el .env por defecto es <repo>/dev.env: nunca el .env de la raíz, que en el
    # checkout principal es el de producción (13/11)
    (tmp_path / ".env").write_text("GMAIL_ADDRESS=real@gmail.test\nGMAIL_APP_PASSWORD=real\n", encoding="utf-8")
    env_file = tmp_path / "dev.env"
    env_file.write_text("GMAIL_ADDRESS=me@correo-ficticio.test\nGMAIL_APP_PASSWORD=vieja\n", encoding="utf-8")
    assert load_gmail_config() == GmailConfig("me@correo-ficticio.test", "vieja")
    env_file.write_text("GMAIL_ADDRESS=me@correo-ficticio.test\nGMAIL_APP_PASSWORD=nueva\n", encoding="utf-8")
    assert load_gmail_config() == GmailConfig("me@correo-ficticio.test", "nueva")


def test_load_gmail_config_reads_the_env_file_named_by_linkedout_env_file(monkeypatch, tmp_path):
    """Una copia de prueba del código (worktree) usa el `.env` real sin duplicar la clave."""
    monkeypatch.setenv("GMAIL_ADDRESS", "placeholder")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "placeholder")
    monkeypatch.setattr("core.mailer.BASE_DIR", tmp_path / "copia")
    real = tmp_path / "real.env"
    real.write_text("GMAIL_ADDRESS=me@correo-ficticio.test\nGMAIL_APP_PASSWORD=abcd efgh ijkl mnop\n", encoding="utf-8")
    monkeypatch.setenv("LINKEDOUT_ENV_FILE", str(real))
    assert load_gmail_config() == GmailConfig("me@correo-ficticio.test", "abcdefghijklmnop")
