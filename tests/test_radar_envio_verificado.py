"""Tests de envío verificado en las tres entradas del Radar (individual, aprobar todos y reanudación).

Verifica que el mailer real no entregue si la aprobación o el CV cambian durante el login SMTP,
y que el worker recupere el estado a LISTO sin sumar enviados.
"""
import asyncio
import itertools
from pathlib import Path
from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient

import server
from core import database, radar_store
from core.mailer import GmailConfig
from core.radar import AprobacionInvalidadaError
from test_radar_workers import make_radar

_URN = itertools.count(2000)


def _crear_post_y_postulacion(estado: str = "LISTO", **post_campos) -> tuple[int, int]:
    pid = database.create_postulacion(
        "Analista Funcional", "Acme", "Buscamos analista funcional en CABA", origen="linkedin", status="READY"
    )
    database.update_postulacion(pid, cv_markdown="# CV Original")

    n = next(_URN)
    post_id = radar_store.insert_post_if_new(
        {
            "urn": f"urn:li:activity:{n}",
            "url": f"https://www.linkedin.com/feed/update/urn:li:activity:{n}/",
            "autor": f"Autor {n}",
            "cargo_autor": "HR",
            "texto": "Buscamos analista funcional. CV a rrhh@empresa.test",
            "fecha_rel": "1 h",
            "emails": ["rrhh@empresa.test"],
        },
        query="analista",
    )
    campos = dict(
        estado=estado,
        postulacion_id=pid,
        triage={
            "es_busqueda_laboral": True,
            "puesto": "Analista Funcional",
            "empresa": "Acme",
            "match_rapido": 80,
            "motivo": "encaja",
            "alcance": "ARGENTINA",
        },
        email_to="rrhh@empresa.test",
        email_subject="Postulación – Analista Funcional",
        email_body="Hola, adjunto mi CV.",
    )
    campos.update(post_campos)
    radar_store.update_post(post_id, **campos)
    return pid, post_id


def _fake_smtp_class(smtp_state: Dict[str, Any], on_login_fn=None):
    class FakeSMTP:
        def __init__(self, host, port, timeout=None, context=None):
            smtp_state.setdefault("events", []).append("init")
            smtp_state["hp"] = (host, port)

        def login(self, user, password):
            smtp_state.setdefault("events", []).append("login")
            smtp_state["login"] = (user, password)
            if on_login_fn:
                on_login_fn()

        def send_message(self, msg):
            smtp_state.setdefault("events", []).append("send_message")
            smtp_state.setdefault("messages", []).append(msg)

        def quit(self):
            smtp_state.setdefault("events", []).append("quit")
            self.close()
            return (221, b"bye")

        def close(self):
            smtp_state.setdefault("events", []).append("close")
            smtp_state["closed"] = True

    return FakeSMTP


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cambio",
    ["aprobado_at_none", "cv_markdown"],
    ids=["aprobado_at_none", "cv_markdown"],
)
async def test_radar_send_invalida_durante_login(tmp_path, monkeypatch, cambio):
    smtp_state: Dict[str, Any] = {"events": [], "messages": [], "closed": False}

    pid, post_id = _crear_post_y_postulacion(
        estado="ENVIANDO",
        aprobado_at="2026-11-05 10:00:00",
    )
    post_snapshot = radar_store.get_post(post_id)

    def on_login():
        if cambio == "aprobado_at_none":
            radar_store.update_post(post_id, aprobado_at=None)
        elif cambio == "cv_markdown":
            database.update_postulacion(pid, cv_markdown="# CV nuevo")

    monkeypatch.setattr("core.mailer.smtplib.SMTP_SSL", _fake_smtp_class(smtp_state, on_login))

    config = GmailConfig("yo@correo-ficticio.test", "app-pass")
    monkeypatch.setattr(server, "load_gmail_config", lambda: config)
    monkeypatch.setattr("core.radar_api.load_gmail_config", lambda: config)
    monkeypatch.setattr(server, "load_candidate_profile", lambda: {"nombre": "Candidato Test"})

    def fake_compile(markdown, target_role=None, output_filename=None, keywords=None, candidate_name=None):
        path = tmp_path / (output_filename or "cv_sintetico.pdf")
        path.write_bytes(b"%PDF-1.4 synthetic test pdf")
        return path

    monkeypatch.setattr(server, "compile_markdown_to_pdf", fake_compile)

    with pytest.raises(AprobacionInvalidadaError) as exc_info:
        await server._radar_send(post_snapshot)

    assert str(exc_info.value) == "El CV o el mensaje cambió: revisalo y volvé a aprobar el envío"
    assert len(smtp_state["messages"]) == 0
    assert smtp_state["closed"] is True

    post_actual = radar_store.get_post(post_id)
    assert post_actual["estado"] != "ENVIADO"
    assert post_actual.get("sent_at") is None
    assert post_actual.get("message_id") is None
    assert radar_store.get_radar_state()["contadores_dia"]["enviados"] == 0


@pytest.mark.parametrize(
    "entrada",
    ["individual", "todos", "reanudacion"],
    ids=["individual", "todos", "reanudacion"],
)
@pytest.mark.parametrize(
    "cambio",
    ["ninguno", "aprobacion_anulada", "cv_recalculado"],
    ids=["ninguno", "aprobacion_anulada", "cv_recalculado"],
)
def test_entradas_validan_despues_del_login(tmp_path, monkeypatch, entrada, cambio):
    smtp_state: Dict[str, Any] = {"events": [], "messages": [], "closed": False}

    pid, post_id = _crear_post_y_postulacion(
        estado="LISTO",
        aprobado_at="2026-11-05 10:00:00" if entrada == "reanudacion" else None,
    )

    if entrada == "reanudacion":
        radar_store.update_radar_state(envios_pausados=True)

    def on_login():
        if cambio == "aprobacion_anulada":
            radar_store.update_post(post_id, aprobado_at=None)
        elif cambio == "cv_recalculado":
            database.update_postulacion(pid, cv_markdown="# CV nuevo")

    monkeypatch.setattr("core.mailer.smtplib.SMTP_SSL", _fake_smtp_class(smtp_state, on_login))

    config = GmailConfig("yo@correo-ficticio.test", "app-pass")
    monkeypatch.setattr(server, "load_gmail_config", lambda: config)
    monkeypatch.setattr("core.radar_api.load_gmail_config", lambda: config)
    monkeypatch.setattr(server, "load_candidate_profile", lambda: {"nombre": "Candidato Test"})

    def fake_compile(markdown, target_role=None, output_filename=None, keywords=None, candidate_name=None):
        path = tmp_path / (output_filename or "cv_sintetico.pdf")
        path.write_bytes(b"%PDF-1.4 synthetic test pdf")
        return path

    monkeypatch.setattr(server, "compile_markdown_to_pdf", fake_compile)

    radar = make_radar(tmp_path, send=server._radar_send)
    monkeypatch.setattr(server, "radar_loop", radar)

    real_enqueue = radar.enqueue_send
    real_resume = radar.resume_sends

    async def wrapped_enqueue(p_id):
        res = await real_enqueue(p_id)
        if radar._send_task is not None and not radar._send_task.done():
            await asyncio.wait_for(asyncio.shield(radar._send_task), timeout=30)
        return res

    async def wrapped_resume():
        res = await real_resume()
        if radar._send_task is not None and not radar._send_task.done():
            await asyncio.wait_for(asyncio.shield(radar._send_task), timeout=30)
        return res

    radar.enqueue_send = wrapped_enqueue
    radar.resume_sends = wrapped_resume

    client = TestClient(server.app)

    try:
        if entrada == "individual":
            version = radar_store.get_post(post_id)["updated_at"]
            r = client.post(f"/api/radar/posts/{post_id}/approve", json={"updated_at": version})
            assert r.status_code == 200
        elif entrada == "todos":
            version = radar_store.get_post(post_id)["updated_at"]
            r = client.post(
                "/api/radar/approve-all",
                json={"ids": [post_id], "updated_at": {str(post_id): version}},
            )
            assert r.status_code == 200
            data = r.json()
            assert data["encolados"] == [post_id]
            assert data["omitidos"] == []
        elif entrada == "reanudacion":
            r = client.post("/api/radar/resume-sends")
            assert r.status_code == 200

        assert smtp_state.get("closed") is True
        post_final = radar_store.get_post(post_id)
        contadores = radar_store.get_radar_state()["contadores_dia"]

        if cambio == "ninguno":
            assert len(smtp_state["messages"]) == 1
            assert post_final["estado"] == "ENVIADO"
            assert post_final["message_id"]
            assert post_final["message_id"].startswith("<") and post_final["message_id"].endswith(">")
            assert post_final["sent_at"] is not None
            assert post_final["error"] is None
            assert contadores["enviados"] == 1
        else:
            assert len(smtp_state["messages"]) == 0
            assert post_final["estado"] == "LISTO"
            assert post_final["aprobado_at"] is None
            assert post_final["error"] == "El CV o el mensaje cambió: revisalo y volvé a aprobar el envío"
            assert post_final.get("sent_at") is None
            assert post_final.get("message_id") is None
            assert contadores["enviados"] == 0

    finally:
        if radar._send_task is not None and not radar._send_task.done():
            radar._send_task.cancel()
        if radar._gen_task is not None and not radar._gen_task.done():
            radar._gen_task.cancel()
        radar._apagado = True
        radar._cerrando = True
        radar._send_stop_event.set()
        radar._stop_event.set()
