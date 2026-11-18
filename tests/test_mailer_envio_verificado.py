from pathlib import Path
import smtplib
from typing import List, Optional

import pytest

from core.mailer import (
    GmailConfig,
    MailerAuthError,
    send_application_email,
)
from core.radar import AprobacionInvalidadaError


def _pdf(tmp_path: Path, name: str = "CV - Sintetico.pdf") -> Path:
    pdf = tmp_path / name
    pdf.write_bytes(b"%PDF-1.4 synthetic test pdf")
    return pdf


def _fake_smtp(state: dict, *, login_exc: Optional[Exception] = None, quit_exc: Optional[Exception] = None):
    class FakeSMTP:
        def __init__(self, host, port, timeout=None, context=None):
            state.setdefault("events", []).append("init")
            state["hp"] = (host, port)

        def login(self, u, p):
            state.setdefault("events", []).append("login")
            if login_exc:
                raise login_exc
            state["login"] = (u, p)

        def send_message(self, m):
            state.setdefault("events", []).append("send_message")
            state.setdefault("messages", []).append(m)

        def quit(self):
            state.setdefault("events", []).append("quit")
            state["quit"] = True
            if quit_exc:
                raise quit_exc
            self.close()
            return (221, b"bye")

        def close(self):
            state.setdefault("events", []).append("close")
            state["closed"] = True

    return FakeSMTP


def test_before_send_corre_entre_login_y_entrega(tmp_path, monkeypatch):
    state = {"events": [], "messages": []}
    monkeypatch.setattr("core.mailer.smtplib.SMTP_SSL", _fake_smtp(state))

    pdf = _pdf(tmp_path, name="CV_Postulacion.pdf")
    config = GmailConfig(address="remitente@correo-ficticio.test", app_password="password123")

    callback_calls = 0

    def callback():
        nonlocal callback_calls
        callback_calls += 1
        state["events"].append("validación")

    res = send_application_email(
        to="empresa@acme.test",
        subject="Postulación Desarrollador",
        body="Adjunto mi CV para la posición.",
        pdf_path=pdf,
        from_name="Candidato Ejemplo",
        config=config,
        before_send=callback,
    )

    # 1. Registrar login -> validación -> entrega -> cierre
    idx_login = state["events"].index("login")
    idx_val = state["events"].index("validación")
    idx_send = state["events"].index("send_message")
    idx_quit = state["events"].index("quit")
    assert idx_login < idx_val < idx_send < idx_quit
    assert state.get("closed") is True

    # 2. Callback una sola vez
    assert callback_calls == 1
    assert state["events"].count("validación") == 1

    # 3. Una entrega
    assert len(state["messages"]) == 1
    assert state["events"].count("send_message") == 1
    sent_msg = state["messages"][0]

    # 4. Retorno igual al Message-ID del MIME
    assert res == str(sent_msg["Message-ID"])
    assert res.startswith("<") and res.endswith(">")

    # 5. Contenido y adjunto conservados
    assert sent_msg["To"] == "empresa@acme.test"
    assert sent_msg["Subject"] == "Postulación Desarrollador"
    assert "Candidato Ejemplo" in sent_msg["From"]
    assert "remitente@correo-ficticio.test" in sent_msg["From"]
    assert sent_msg.get_body(("plain",)).get_content().strip() == "Adjunto mi CV para la posición."

    attachments = list(sent_msg.iter_attachments())
    assert len(attachments) == 1
    att = attachments[0]
    assert att.get_content_type() == "application/pdf"
    assert att.get_filename() == "CV_Postulacion.pdf"
    assert att.get_content() == pdf.read_bytes()


@pytest.mark.parametrize(
    "quit_exc",
    [
        None,
        OSError("Error de red en QUIT"),
    ],
    ids=["quit_normal", "quit_fallido"],
)
def test_before_send_propaga_invalidacion_y_cierra(tmp_path, monkeypatch, quit_exc):
    state = {"events": [], "messages": []}
    monkeypatch.setattr("core.mailer.smtplib.SMTP_SSL", _fake_smtp(state, quit_exc=quit_exc))

    pdf = _pdf(tmp_path)
    config = GmailConfig(address="me@correo-ficticio.test", app_password="pw")

    error_esperado = AprobacionInvalidadaError("El contenido cambió antes de enviar")

    def callback_invalida():
        state["events"].append("validación")
        raise error_esperado

    with pytest.raises(AprobacionInvalidadaError) as exc_info:
        send_application_email(
            to="empresa@acme.test",
            subject="Asunto",
            body="Cuerpo",
            pdf_path=pdf,
            from_name="Nombre",
            config=config,
            before_send=callback_invalida,
        )

    # Comprobar identidad de la excepción
    assert exc_info.value is error_esperado

    # Cero entregas
    assert len(state["messages"]) == 0
    assert "send_message" not in state["events"]

    # Conexión cerrada
    assert state.get("closed") is True
    assert "close" in state["events"]
    assert "quit" in state["events"]


def test_before_send_no_corre_si_login_falla(tmp_path, monkeypatch):
    state = {"events": [], "messages": []}
    login_error = smtplib.SMTPAuthenticationError(535, b"Credenciales invalidas")
    monkeypatch.setattr("core.mailer.smtplib.SMTP_SSL", _fake_smtp(state, login_exc=login_error))

    pdf = _pdf(tmp_path)
    config = GmailConfig(address="me@correo-ficticio.test", app_password="pw")

    callback_called = False

    def callback():
        nonlocal callback_called
        callback_called = True
        state["events"].append("validación")

    with pytest.raises(MailerAuthError):
        send_application_email(
            to="empresa@acme.test",
            subject="Asunto",
            body="Cuerpo",
            pdf_path=pdf,
            from_name="Nombre",
            config=config,
            before_send=callback,
        )

    # Callback sin llamadas
    assert not callback_called
    assert "validación" not in state["events"]

    # Cero entregas
    assert len(state["messages"]) == 0
    assert "send_message" not in state["events"]

    # Conexión cerrada
    assert state.get("closed") is True


def test_sin_before_send_conserva_interfaz(tmp_path, monkeypatch):
    state = {"events": [], "messages": []}
    monkeypatch.setattr("core.mailer.smtplib.SMTP_SSL", _fake_smtp(state))

    pdf = _pdf(tmp_path)
    config = GmailConfig(address="me@correo-ficticio.test", app_password="pw")

    html_content = "<p>Texto con formato y <a href='https://example.com'>link</a></p>"

    # Invocación sin callback, con html_body como 7° argumento posicional
    res = send_application_email(
        "destinatario@acme.test",
        "Asunto con HTML",
        "Cuerpo plano",
        pdf,
        "Remitente",
        config,
        html_content,
    )

    # Exactamente una entrega con parte HTML y retorno Message-ID
    assert len(state["messages"]) == 1
    sent_msg = state["messages"][0]

    assert res == str(sent_msg["Message-ID"])
    assert res.startswith("<") and res.endswith(">")

    html_part = sent_msg.get_body(("html",))
    assert html_part is not None
    assert html_content in html_part.get_content()

    plain_part = sent_msg.get_body(("plain",))
    assert plain_part is not None
    assert plain_part.get_content().strip() == "Cuerpo plano"
