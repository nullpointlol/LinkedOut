"""Modo de prueba del mail: con LINKEDOUT_MAIL_PRUEBA definida, todo envío va a esa dirección
(nunca al destinatario real) y el asunto lleva «[PRUEBA]». Sin la variable, nada cambia."""
import email
import email.policy
import re
from pathlib import Path

import pytest

from core import mailer
from core.mailer import GmailConfig, MailerError, load_gmail_config, send_application_email
from tests.test_mailer import _fake_smtp, _pdf

RAIZ = Path(__file__).resolve().parent.parent


def _env(tmp_path, monkeypatch, contenido: str) -> None:
    archivo = tmp_path / "prueba.env"
    archivo.write_text(contenido, encoding="utf-8")
    monkeypatch.setenv("LINKEDOUT_ENV_FILE", str(archivo))
    for clave in ("GMAIL_ADDRESS", "GMAIL_APP_PASSWORD", "LINKEDOUT_MAIL_PRUEBA"):
        monkeypatch.setenv(clave, "")


def _enviado(sent):
    return email.message_from_bytes(sent["msg"].as_bytes(), policy=email.policy.default)


def test_config_lee_mail_de_prueba_del_env(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch, "GMAIL_ADDRESS=yo@example.com\nGMAIL_APP_PASSWORD=abcd efgh\n"
                                "LINKEDOUT_MAIL_PRUEBA=  pruebas@example.org  \n")
    config = load_gmail_config()
    assert config == GmailConfig("yo@example.com", "abcdefgh", mail_prueba="pruebas@example.org")


@pytest.mark.parametrize("valor", ["", "   "])
def test_config_sin_mail_de_prueba_es_none(tmp_path, monkeypatch, valor):
    _env(tmp_path, monkeypatch, f"GMAIL_ADDRESS=yo@example.com\nGMAIL_APP_PASSWORD=x\nLINKEDOUT_MAIL_PRUEBA={valor}\n")
    assert load_gmail_config().mail_prueba is None


def test_modo_prueba_redirige_destinatario_y_marca_asunto(tmp_path, monkeypatch):
    sent = {}
    monkeypatch.setattr("core.mailer.smtplib.SMTP_SSL", _fake_smtp(sent))
    config = GmailConfig("yo@example.com", "pw", mail_prueba="pruebas@example.org")
    mid = send_application_email("rrhh@acme.test", "Postulación – Analista", "Hola", _pdf(tmp_path), "Ana", config)
    msg = _enviado(sent)
    assert msg["To"] == "pruebas@example.org"
    assert str(msg["Subject"]) == "[PRUEBA] Postulación – Analista"
    # El destinatario real no aparece en ningún encabezado de destino
    assert "rrhh@acme.test" not in " ".join(str(msg.get(h) or "") for h in ("To", "Cc", "Bcc"))
    assert mid == sent["msg"]["Message-ID"]


def test_modo_prueba_no_duplica_la_marca(tmp_path, monkeypatch):
    sent = {}
    monkeypatch.setattr("core.mailer.smtplib.SMTP_SSL", _fake_smtp(sent))
    config = GmailConfig("yo@example.com", "pw", mail_prueba="pruebas@example.org")
    send_application_email("rrhh@acme.test", "[PRUEBA] S", "B", _pdf(tmp_path), "Ana", config)
    assert str(_enviado(sent)["Subject"]) == "[PRUEBA] S"


def test_sin_modo_prueba_va_al_destinatario_real(tmp_path, monkeypatch):
    sent = {}
    monkeypatch.setattr("core.mailer.smtplib.SMTP_SSL", _fake_smtp(sent))
    send_application_email("rrhh@acme.test", "S", "B", _pdf(tmp_path), "Ana", GmailConfig("yo@example.com", "pw"))
    msg = _enviado(sent)
    assert msg["To"] == "rrhh@acme.test" and str(msg["Subject"]) == "S"


@pytest.mark.parametrize("invalida", ["sin-arroba", "dos@@example.org", "a@b"])
def test_mail_de_prueba_invalido_no_envia_a_nadie(tmp_path, monkeypatch, invalida):
    """Si la dirección de prueba está mal escrita, falla sin conectarse: nunca cae al destinatario real."""
    def _prohibido(*a, **kw):
        raise AssertionError("no debe conectarse")
    monkeypatch.setattr("core.mailer.smtplib.SMTP_SSL", _prohibido)
    config = GmailConfig("yo@example.com", "pw", mail_prueba=invalida)
    with pytest.raises(MailerError, match="LINKEDOUT_MAIL_PRUEBA"):
        send_application_email("rrhh@acme.test", "S", "B", _pdf(tmp_path), "Ana", config)


def test_env_example_documenta_la_variable_vacia():
    texto = (RAIZ / ".env.example").read_text(encoding="utf-8")
    assert re.search(r"^LINKEDOUT_MAIL_PRUEBA=$", texto, re.M)


def test_codigo_no_tiene_direcciones_de_mail_escritas():
    """Ninguna dirección real en el código del mailer: la de prueba sale solo del entorno."""
    fuente = Path(mailer.__file__).read_text(encoding="utf-8")
    direcciones = re.findall(r"[\w.+-]+@[\w-]+\.[\w.]+", fuente)
    assert direcciones == [], direcciones


@pytest.mark.parametrize("prueba,esperado", [("pruebas@example.org", True), (None, False)])
def test_estado_del_radar_avisa_modo_prueba_sin_mostrar_la_direccion(monkeypatch, prueba, esperado):
    from fastapi.testclient import TestClient
    import server
    monkeypatch.setattr("core.radar_api.load_gmail_config",
                        lambda: GmailConfig("yo@example.com", "pw", mail_prueba=prueba))
    r = TestClient(server.app).get("/api/radar/status")
    assert r.status_code == 200
    assert r.json()["mail_prueba"] is esperado
    assert "pruebas@example.org" not in r.text
