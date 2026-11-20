"""Plan de la misión: reproducir pérdidas ATS y escapes de envío; corregir la
frontera final, preservar literales y medir offline; validar sólo la selección.
Todos los datos son sintéticos; no se llama a proveedores ni al portal real.

Ronda 2: conservar precedencia de canal/configuración; validar el contrato
completo del mail sólo si cambió y no exigir reportes a contenido heredado.
Corrección de compuerta: no atribuir a completitud incumplimientos anteriores;
conservar referencias de asunto heredadas y ausencia de dictamen, sin inventar
aprobación. Mantener los rechazos explícitos y las señales nuevas.
"""
import asyncio
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from datetime import date
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import server
from core import database, email_composer as composer, profile_parser, quality_calibration
from core import radar_store as store
from core.auditor import AuditorAgent
from core.mailer import GmailConfig
from tests.test_cv_mail_final_contract import CV, PITCH, KEYWORDS, EXPECTED_COMPANIES, frontera, _crear
from tests.test_radar_envio_verificado import _crear_post_y_postulacion, _fake_smtp_class
from core.radar import AprobacionInvalidadaError
from tests.test_radar_workers import make_radar


@pytest.mark.asyncio
@pytest.mark.parametrize("antes,despues,campo", [
    ("## IDIOMAS", "## OTROS", "missing_sections"),
    ("SQL", "tecnología", "missing_keywords"),
    ("KORVA", "Empresa omitida", "missing_experiences"),
])
async def test_completitud_no_puede_perder_ats(frontera, monkeypatch, antes, despues, campo):
    pid = _crear(frontera)
    reporte = AuditorAgent().audit(CV, PITCH, KEYWORDS, expected_companies=EXPECTED_COMPANIES)
    assert reporte["approved"] is True
    nuevo = CV.replace(antes, despues)
    monkeypatch.setattr(server, "_asegurar_completo", AsyncMock(return_value=(nuevo, PITCH, {}, [])))
    with pytest.raises(RuntimeError, match="Revisá"):
        await server._finalizar_cv(pid, CV, PITCH, {}, reporte, KEYWORDS)
    actual = database.get_postulacion(pid)
    assert actual["status"] == "ERROR"
    assert actual["cv_markdown"] == nuevo
    assert actual["reporte_auditoria"]["approved"] is False
    assert actual["reporte_auditoria"]["ats"][campo]
    assert frontera.bridge_contado.llamadas == []


def test_sin_reporte_se_audita_y_no_se_inventa_aprobacion(frontera):
    pid = _crear(frontera)
    reporte, motivos = server._dictamen_final(database.get_postulacion(pid), "# ANA PRUEBA", "", {}, None)
    assert reporte["approved"] is False
    assert reporte["ats"]["missing_sections"] and motivos


def test_keywords_de_otro_reporte_no_producen_rechazo(frontera):
    pid = _crear(frontera, ["keyword heredada ausente"])
    reporte = AuditorAgent().audit(CV, PITCH, KEYWORDS, expected_companies=EXPECTED_COMPANIES)
    final, motivos = server._dictamen_final(database.get_postulacion(pid), CV, PITCH, {}, reporte)
    assert final == reporte and motivos == []


@pytest.mark.parametrize("entrada", ["individual", "todos", "reanudar"])
@pytest.mark.parametrize("defecto", ["reporte", "mail", "asunto", "cv"])
def test_heredado_rechazado_no_se_envia(monkeypatch, tmp_path, entrada, defecto):
    pid, post_id = _crear_post_y_postulacion(email_subject="Postulación QA")
    database.update_postulacion(pid, cv_markdown=CV, reporte_auditoria={"approved": defecto != "reporte"})
    residual = "Cambio de guardia. Pase de guardia. Cambio de guardia."
    if defecto == "mail":
        store.update_post(post_id, email_body=residual)
    if defecto == "asunto":
        store.update_post(post_id, email_subject=residual)
    if defecto == "cv":
        database.update_postulacion(pid, cv_markdown=CV + "\n" + residual)
    radar = make_radar(tmp_path, send=AsyncMock())
    radar.enqueue_send = AsyncMock()
    radar.resume_sends = AsyncMock()
    monkeypatch.setattr(server, "radar_loop", radar)
    monkeypatch.setattr("core.radar_api.load_gmail_config", lambda: GmailConfig("yo@example.com", "falsa"))
    if entrada == "reanudar":
        store.update_post(post_id, aprobado_at="2026-11-07 10:00:00")
    version = store.get_post(post_id)["updated_at"]
    client = TestClient(server.app)
    try:
        if entrada == "individual":
            response = client.post(f"/api/radar/posts/{post_id}/approve", json={"updated_at": version})
        elif entrada == "todos":
            response = client.post("/api/radar/approve-all", json={"ids": [post_id], "updated_at": {str(post_id): version}})
        else:
            response = client.post("/api/radar/resume-sends")
        assert "Revisá" in response.text
        assert response.status_code == 409
        assert radar.enqueue_send.await_count == radar.resume_sends.await_count == 0
        assert store.get_post(post_id)["aprobado_at"] is None
        assert store.get_radar_state()["contadores_dia"]["enviados"] == 0
    finally:
        client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("contacto", ["ana@example.com", "ana@example.com | https://linkedin.com/in/ana–prueba"])
async def test_firma_literal_pasa_del_composer_a_completitud(monkeypatch, contacto):
    perfil = {"nombre": "ANA–PRUEBA", "contacto": contacto}
    monkeypatch.setattr(server, "_perfil_o_vacio", lambda: perfil)
    monkeypatch.setattr(composer, "load_voz_email", lambda: {})
    async def completar(texto, **kw):
        return texto, []
    monkeypatch.setattr(server, "completar", completar)
    bridge = AsyncMock()
    bridge.run_prompt.return_value = {"subject": "Postulación QA", "body": "Hola, adjunto mi CV.\n\nANA–PRUEBA"}
    mensaje = await composer.compose_email({}, {}, perfil, bridge)
    assert "ANA–PRUEBA" in mensaje["body"]
    assert "ANA-PRUEBA" not in mensaje["body"]
    final = await server._completar_mensaje({}, {"id": 1}, mensaje)
    assert final == mensaje
    assert bridge.run_prompt.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("defecto", ["frase", "largo", "idioma"])
async def test_completitud_revalida_contrato_completo_sin_recomponer(monkeypatch, defecto):
    voz = {"disponibilidad": "Puedo conversar mañana.", "disponibilidad_en": "Available tomorrow."}
    monkeypatch.setattr(composer, "load_voz_email", lambda: voz)
    monkeypatch.setattr(server, "_perfil_o_vacio", lambda: {"nombre": "ANA PRUEBA"})
    post = {"texto": "We are hiring a QA engineer. Please send your CV in English."} if defecto == "idioma" else {}
    cuerpo = "Hola, adjunto mi CV. Puedo conversar mañana."
    async def completar(texto, *, tipo, **kw):
        if tipo == "mensaje":
            texto = (texto.replace("Puedo conversar mañana.", "") if defecto == "frase" else
                     texto + "\n" + "revisé " * 100 if defecto == "largo" else
                     "Hola, soy de Buenos Aires y tengo experiencia en las pruebas de los sistemas. Available tomorrow.")
        return texto, []
    monkeypatch.setattr(server, "completar", completar)
    compose = AsyncMock(return_value={"subject": "QA", "body": cuerpo, "is_fallback": False})
    monkeypatch.setattr(server, "compose_message", compose)
    with pytest.raises(composer.MensajeRequiereRevision):
        await server._componer_mensaje_validado(post, {"id": 1})
    assert compose.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("defecto", ["frase", "largo", "idioma"])
async def test_mail_intacto_conserva_validacion_previa_del_composer(monkeypatch, defecto):
    monkeypatch.setattr(server, "_perfil_o_vacio", lambda: {"nombre": "ANA PRUEBA"})
    monkeypatch.setattr(composer, "load_voz_email", lambda: {"disponibilidad": "Puedo conversar mañana."})
    cuerpo = "Hola, adjunto mi CV." if defecto != "largo" else "Hola, " + "revisé " * 100
    post = {"texto": "We are hiring. Please send your CV in English."} if defecto == "idioma" else {}
    async def completar(texto, **kw):
        return texto, []
    monkeypatch.setattr(server, "completar", completar)
    mensaje = {"subject": "QA", "body": cuerpo, "is_fallback": False}
    compose = AsyncMock(return_value=mensaje)
    monkeypatch.setattr(server, "compose_message", compose)
    assert await server._componer_mensaje_validado(post, {"id": 1}) == mensaje
    assert compose.await_count == 1


@pytest.mark.parametrize("entrada", ["individual", "todos", "reanudar"])
@pytest.mark.parametrize("reporte", [None, {}, {"score_ats": 100}])
def test_heredado_sin_dictamen_explicito_permite_aprobacion(monkeypatch, tmp_path, entrada, reporte):
    pid, post_id = _crear_post_y_postulacion(email_subject="Postulación QA")
    database.update_postulacion(pid, reporte_auditoria=reporte)
    if entrada == "reanudar":
        store.update_post(post_id, aprobado_at="2026-11-07 10:00:00")
    radar = make_radar(tmp_path, send=AsyncMock())
    radar.enqueue_send, radar.resume_sends = AsyncMock(), AsyncMock()
    monkeypatch.setattr(server, "radar_loop", radar)
    monkeypatch.setattr("core.radar_api.load_gmail_config", lambda: GmailConfig("yo@example.com", "falsa"))
    version = store.get_post(post_id)["updated_at"]
    client = TestClient(server.app)
    try:
        if entrada == "individual":
            respuesta = client.post(f"/api/radar/posts/{post_id}/approve", json={"updated_at": version})
        elif entrada == "todos":
            respuesta = client.post("/api/radar/approve-all", json={"ids": [post_id], "updated_at": {str(post_id): version}})
        else:
            respuesta = client.post("/api/radar/resume-sends")
        assert respuesta.status_code == 200, respuesta.text
        assert store.get_post(post_id)["aprobado_at"]
        assert radar.enqueue_send.await_count + radar.resume_sends.await_count == 1
    finally:
        client.close()


@pytest.mark.asyncio
async def test_asunto_exigido_literal_con_rayas(monkeypatch):
    monkeypatch.setattr(composer, "load_voz_email", lambda: {})
    monkeypatch.setattr(server, "_perfil_o_vacio", lambda: {"nombre": "ANA PRUEBA"})
    async def completar(texto, **kw):
        return texto, []
    monkeypatch.setattr(server, "completar", completar)
    post = {"texto": 'Enviar con asunto: "QA — turno tarde"', "triage": {"instrucciones_postulacion": 'Asunto: "QA — turno tarde"'}}
    bridge = AsyncMock()
    bridge.run_prompt.return_value = {"subject": "QA — turno tarde", "body": "Hola, adjunto mi CV."}
    mensaje = await composer.compose_email(post, {}, {"nombre": "ANA PRUEBA"}, bridge)
    assert mensaje["subject"] == "QA — turno tarde"
    final = await server._completar_mensaje(post, {"id": 1}, mensaje)
    assert final["subject"] == mensaje["subject"]
    assert bridge.run_prompt.await_count == 1


@pytest.mark.parametrize("texto,esperado", [
    ("Perfil: más de 7 años de experiencia.\n2011–2017\n2018–2026", 15),  # una frase suelta nunca da la cifra; mandan las fechas
    ("Tengo 9 años de experiencia.\n01/2018 - 01/2025", 7),  # una frase suelta nunca da la cifra; mandan las fechas
    ("2011–2017\n2018–2026", 15),
    ("2018–2025\n2020–2026", 8),
    ("2018–actualidad", 8),
    ("01/2018 - 01/2023\n01/2020 - 01/2025", 7),
    ("Sin datos laborales", None),
])
def test_q2_experiencia_declarada_y_fechas(texto, esperado):
    assert profile_parser.calcular_anios_experiencia(texto, date(2026, 11, 7)) == esperado


def test_q2_declaracion_en_personales_no_reemplaza_las_fechas():
    # la cifra sale de las fechas; los años por área los da el análisis del perfil.
    perfil = profile_parser.parse_candidate_profile("## Empresa\n2011–2017\n2018–2026", "# ANA PRUEBA\nMás de 7 años de experiencia.")
    assert perfil["anios_experiencia"] == 15


def test_medicion_por_patron_y_cli_sobre_copia(tmp_path):
    medir = getattr(quality_calibration, "medir_senales", None)
    assert callable(medir), "Falta el medidor puro por patrón"
    textos = ["SQL — API", "Ordené tickets para acelerar el despacho según SLA."]
    informe = medir(textos)
    assert informe["n"] == 2 and informe["por_senal"]["raya"] == 1
    assert informe["por_senal"]["impacto vago"] == 1
    copia = tmp_path / "copia.sqlite"
    with sqlite3.connect(copia) as conn:
        conn.execute("CREATE TABLE postulaciones (id INTEGER, status TEXT, updated_at TEXT, cv_markdown TEXT, pitch TEXT)")
        conn.executemany("INSERT INTO postulaciones VALUES (?, ?, ?, ?, ?)", [
            (i, "READY" if i < 12 else "ERROR", f"2026-11-{i:02} 12:00:00", "SQL — API" if i == 1 else "Consulté SQL.", "Ordené tickets para acelerar el despacho según SLA." if i == 11 else "Probé APIs.") for i in range(1, 13)])
        conn.execute("CREATE TABLE linkedin_posts (id INTEGER, postulacion_id INTEGER, estado TEXT, created_at TEXT, email_subject TEXT, email_body TEXT)")
        conn.executemany("INSERT INTO linkedin_posts VALUES (?, ?, ?, ?, ?, ?)", [
            (i, i, "LISTO" if i < 12 else "ERROR", f"2026-11-{i:02} 12:00:00",
             "QA — SQL" if i == 10 else "Postulación QA",
             "Adjunto mi CV — saludos." if i == 9 else "Adjunto mi CV.")
            for i in range(1, 13)])
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    resultado = subprocess.run([sys.executable, "-m", "core.quality_calibration", str(copia)], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=20)
    assert resultado.returncode == 0, resultado.stderr
    salida = (tmp_path / "_ref" / "calibracion.md").read_text(encoding="utf-8")
    assert "10" in salida and "| raya | 2 |" in salida and "| impacto vago | 1 |" in salida
    assert "LISTO" in salida and "JOIN postulaciones" in salida
    assert "ORDER BY lp.created_at DESC, lp.id DESC" in salida and "LIMIT 10" in salida
    assert "Consulté" not in salida and "Sin etiquetas humanas" in salida


@pytest.mark.parametrize("entrada", ["individual", "todos", "reanudar"])
@pytest.mark.parametrize("campo", ["CV", "email_subject", "email_body"])
def test_saneado_persistido_invalida_version(monkeypatch, tmp_path, entrada, campo):
    pid, post_id = _crear_post_y_postulacion(email_subject="Postulación QA")
    database.update_postulacion(pid, cv_markdown=CV, reporte_auditoria={"approved": True})
    if campo == "CV":
        database.update_postulacion(pid, cv_markdown=CV + "\nRelevé procesos — documenté acuerdos.")
    else:
        store.update_post(post_id, **{campo: "Relevé procesos — documenté acuerdos."})
    store.update_post(post_id, updated_at="2000-01-01 00:00:00",
                      aprobado_at="2026-11-07 10:00:00" if entrada == "reanudar" else None)
    radar = make_radar(tmp_path, send=AsyncMock())
    radar.enqueue_send, radar.resume_sends = AsyncMock(), AsyncMock()
    monkeypatch.setattr(server, "radar_loop", radar)
    monkeypatch.setattr("core.radar_api.load_gmail_config", lambda: GmailConfig("yo@example.com", "falsa"))
    version = store.get_post(post_id)["updated_at"]
    client = TestClient(server.app)
    try:
        if entrada == "individual":
            response = client.post(f"/api/radar/posts/{post_id}/approve", json={"updated_at": version})
        elif entrada == "todos":
            response = client.post("/api/radar/approve-all", json={"ids": [post_id], "updated_at": {str(post_id): version}})
        else:
            response = client.post("/api/radar/resume-sends")
        assert response.status_code == 409 and "Revisá" in response.text
        actual = store.get_post(post_id)
        assert actual["updated_at"] != version and actual["aprobado_at"] is None
        texto = database.get_postulacion(pid)["cv_markdown"] if campo == "CV" else actual[campo]
        assert "Relevé procesos - documenté acuerdos." in texto
        assert radar.enqueue_send.await_count == radar.resume_sends.await_count == 0
    finally:
        client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("cambio", ["cv", "aprobacion", "reporte", "mensaje"])
async def test_guard_smtp_sigue_vigente_con_contenido_saneado(monkeypatch, tmp_path, cambio):
    pid, post_id = _crear_post_y_postulacion(estado="ENVIANDO", email_subject="Postulación QA", aprobado_at="2026-11-07 10:00:00")
    database.update_postulacion(pid, cv_markdown=CV, reporte_auditoria={"approved": True})
    store.update_post(post_id, aprobado_at="2026-11-07 10:00:00")
    state = {"events": [], "messages": [], "closed": False}
    def login():
        if cambio == "cv":
            database.update_postulacion(pid, cv_markdown=CV + "\nEdición posterior.")
        elif cambio == "aprobacion":
            store.update_post(post_id, aprobado_at=None)
        elif cambio == "reporte":
            database.update_postulacion(pid, reporte_auditoria={"approved": False})
        else:
            store.update_post(post_id, email_body="Cambio de guardia. Pase de guardia. Cambio de guardia.")
    monkeypatch.setattr("core.mailer.smtplib.SMTP_SSL", _fake_smtp_class(state, login))
    monkeypatch.setattr(server, "load_gmail_config", lambda: GmailConfig("yo@example.com", "falsa"))
    def pdf(*a, **kw):
        path = tmp_path / "cv.pdf"
        path.write_bytes(b"%PDF-1.4 synthetic")
        return path
    monkeypatch.setattr(server, "compile_markdown_to_pdf", pdf)
    with pytest.raises(AprobacionInvalidadaError):
        await server._radar_send(store.get_post(post_id))
    assert "login" in state["events"] and "send_message" not in state["events"]
    assert state["messages"] == [] and state["closed"] is True


@pytest.mark.parametrize("entrada", ["individual", "todos", "reanudar"])
def test_guard_no_sanea_canales_que_no_envian_email(monkeypatch, tmp_path, entrada):
    _, post_id = _crear_post_y_postulacion(
        canal="whatsapp", email_subject="", email_body="Hola — adjunto mi CV.",
        aprobado_at="2026-11-07 10:00:00" if entrada == "reanudar" else None)
    anterior = store.get_post(post_id)
    radar = make_radar(tmp_path, send=AsyncMock())
    radar.enqueue_send, radar.resume_sends = AsyncMock(), AsyncMock()
    monkeypatch.setattr(server, "radar_loop", radar)
    monkeypatch.setattr("core.radar_api.load_gmail_config", lambda: GmailConfig("yo@example.com", "falsa"))
    client = TestClient(server.app)  # sin lifespan ni Radar real
    try:
        if entrada == "individual":
            response = client.post(f"/api/radar/posts/{post_id}/approve",
                                   json={"updated_at": anterior["updated_at"]})
            assert response.status_code == 409 and "usá «Ya me postulé»" in response.json()["detail"]
        elif entrada == "todos":
            response = client.post("/api/radar/approve-all", json={"ids": [post_id],
                "updated_at": {str(post_id): anterior["updated_at"]}})
            assert response.status_code == 200
            assert response.json()["encolados"] == []
            assert "usá «Ya me postulé»" in response.json()["omitidos"][0]["motivo"]
        else:
            response = client.post("/api/radar/resume-sends")
            assert response.status_code == 200 and radar.resume_sends.await_count == 1
        assert store.get_post(post_id) == anterior
        assert radar.enqueue_send.await_count == 0
    finally:
        client.close()


@pytest.mark.parametrize("entrada", ["individual", "todos"])
def test_gmail_sin_configurar_no_sanea_ni_aprueba(monkeypatch, tmp_path, entrada):
    _, post_id = _crear_post_y_postulacion()
    anterior = store.get_post(post_id)
    radar = make_radar(tmp_path, send=AsyncMock())
    radar.enqueue_send = AsyncMock()
    monkeypatch.setattr(server, "radar_loop", radar)
    monkeypatch.setattr("core.radar_api.load_gmail_config", lambda: None)
    client = TestClient(server.app)
    try:
        if entrada == "individual":
            response = client.post(f"/api/radar/posts/{post_id}/approve",
                                   json={"updated_at": anterior["updated_at"]})
        else:
            response = client.post("/api/radar/approve-all", json={"ids": [post_id],
                "updated_at": {str(post_id): anterior["updated_at"]}})
        assert response.status_code == 400 and "Gmail no configurado" in response.json()["detail"]
        assert store.get_post(post_id) == anterior
        assert radar.enqueue_send.await_count == 0
    finally:
        client.close()


@pytest.mark.asyncio
async def test_send_sin_gmail_no_sanea_ni_conecta(monkeypatch):
    from core.mailer import MailerAuthError

    _, post_id = _crear_post_y_postulacion(estado="ENVIANDO", aprobado_at="2026-11-07 10:00:00")
    anterior = store.get_post(post_id)
    monkeypatch.setattr(server, "load_gmail_config", lambda: None)
    smtp = AsyncMock(side_effect=AssertionError("No se debe conectar"))
    monkeypatch.setattr("core.mailer.smtplib.SMTP_SSL", smtp)
    with pytest.raises(MailerAuthError, match="Gmail no configurado"):
        await server._radar_send(anterior)
    assert store.get_post(post_id) == anterior
    smtp.assert_not_called()


@pytest.mark.asyncio
async def test_completitud_no_atribuye_frases_ya_ausentes(monkeypatch):
    monkeypatch.setattr(server, "_perfil_o_vacio", lambda: {})
    monkeypatch.setattr(composer, "load_voz_email", lambda: {"ubicacion": "Soy de Villa Test."})
    async def completar(texto, **kw):
        return texto.replace("[Nombre]", "equipo"), []
    monkeypatch.setattr(server, "completar", completar)
    mensaje = {"subject": "Postulación QA", "body": "Hola [Nombre], adjunto mi CV."}
    final = await server._completar_mensaje({}, {"id": 1}, mensaje)
    assert final["body"] == "Hola equipo, adjunto mi CV."


@pytest.mark.parametrize("reporte", [None, {}, {"score_ats": 100}])
@pytest.mark.asyncio
async def test_cv_heredado_sin_dictamen_no_recibe_rechazo_retroactivo(monkeypatch, reporte):
    pid, _ = _crear_post_y_postulacion(email_subject="Postulación QA")
    database.update_postulacion(pid, reporte_auditoria=reporte)
    monkeypatch.setattr(server, "_perfil_o_vacio", lambda: {})
    anterior = database.get_postulacion(pid)
    final = await server._cv_al_dia(anterior)
    assert final["status"] == "READY"
    assert final["cv_markdown"] == anterior["cv_markdown"]
    assert final["reporte_auditoria"] == anterior["reporte_auditoria"]


@pytest.mark.parametrize("entrada", ["individual", "todos", "reanudar"])
def test_asunto_heredado_referencia_al_puesto_no_invalida(monkeypatch, tmp_path, entrada):
    _, post_id = _crear_post_y_postulacion(
        aprobado_at="2026-11-07 10:00:00" if entrada == "reanudar" else None)
    anterior = store.get_post(post_id)
    radar = make_radar(tmp_path, send=AsyncMock())
    radar.enqueue_send, radar.resume_sends = AsyncMock(), AsyncMock()
    monkeypatch.setattr(server, "radar_loop", radar)
    monkeypatch.setattr("core.radar_api.load_gmail_config", lambda: GmailConfig("yo@example.com", "falsa"))
    client = TestClient(server.app)
    try:
        if entrada == "individual":
            r = client.post(f"/api/radar/posts/{post_id}/approve", json={"updated_at": anterior["updated_at"]})
        elif entrada == "todos":
            r = client.post("/api/radar/approve-all", json={"ids": [post_id], "updated_at": {str(post_id): anterior["updated_at"]}})
        else:
            r = client.post("/api/radar/resume-sends")
        assert r.status_code == 200, r.text
        assert store.get_post(post_id)["email_subject"] == anterior["email_subject"]
        assert radar.enqueue_send.await_count + radar.resume_sends.await_count == 1
    finally:
        client.close()


def test_auditoria_final_independiente_del_adaptador_llm(frontera, monkeypatch):
    pid = _crear(frontera)
    reporte = AuditorAgent().audit(CV, PITCH, KEYWORDS, expected_companies=EXPECTED_COMPANIES)
    class AdaptadorProveedor:
        async def audit_and_remediate_all(self, *args, **kwargs):
            raise AssertionError("No corresponde llamar al proveedor")
    monkeypatch.setattr(server.auditoria_cv, "AuditorAgent", AdaptadorProveedor)
    final, motivos = server._dictamen_final(
        database.get_postulacion(pid), CV.replace("SQL", "tecnología"), PITCH, {}, reporte, KEYWORDS, cv_antes=CV)
    assert final["approved"] is False
    assert final["ats"]["missing_keywords"] and motivos
    assert frontera.bridge_contado.llamadas == []
