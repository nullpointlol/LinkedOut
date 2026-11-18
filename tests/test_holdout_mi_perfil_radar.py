"""Aceptación de regeneración sin proveedores, envíos ni lifespan."""
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import server
from core import radar_store, database
from core.radar import RadarLoop


@pytest.fixture
def entorno(monkeypatch):
    radar = AsyncMock(spec=RadarLoop)
    monkeypatch.setattr(server, "radar_loop", radar)
    cliente = TestClient(server.app)
    yield cliente, radar
    cliente.close()


def crear(n, estado="LISTO", fuente="busqueda", **extra):
    pid = radar_store.insert_post_if_new({"urn": f"urn:holdout:perfil:{n}",
        "url": f"https://example.test/{n}", "texto": "Oferta sintética", "fecha_rel": "1 h"}, query="test", fuente=fuente)
    radar_store.update_post(pid, estado=estado, triage={"es_busqueda_laboral": True},
                            email_subject="Asunto", email_body="Cuerpo", **extra)
    return pid


def test_regeneracion_selectiva_y_vacio(entorno):
    client, radar = entorno
    listo = crear(1)
    otros = [crear(2, aprobado_at="2026-11-09T10:00:00"), crear(3, "ERROR"),
             crear(4, "ENVIADO"), crear(5, "GENERANDO")]
    sin_triage = crear(6)
    radar_store.update_post(sin_triage, triage={})
    otros.append(sin_triage)
    antes = {p: radar_store.get_post(p) for p in otros}
    respuesta = client.post("/api/radar/regenerar-listos")
    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json() == {"regenerados": 1, "ids": [listo]}
    post = radar_store.get_post(listo)
    assert post["estado"] == "GENERANDO" and post["aprobado_at"] is None
    assert post["email_subject"] is None and post["email_body"] is None
    assert {p: radar_store.get_post(p) for p in otros} == antes
    radar.wake_generation.assert_awaited_once()
    assert client.post("/api/radar/regenerar-listos").json() == {"regenerados": 0, "ids": []}


def test_mismo_efecto_que_regenerate_con_cv_sin_cambiar_formato(entorno):
    client, _ = entorno
    individual, lote = crear(10), crear(11)
    postulaciones = []
    for post_id, formato in ((individual, "completo"), (lote, "una_pagina")):
        pid = database.create_postulacion("Analista", "Empresa", "Texto", status="READY", origen="linkedin")
        database.update_postulacion(pid, formato_cv=formato)
        radar_store.update_post(post_id, postulacion_id=pid)
        postulaciones.append((pid, formato))
    r = client.post(f"/api/radar/posts/{individual}/regenerate", json={"cv": True})
    assert r.status_code == 200
    r = client.post("/api/radar/regenerar-listos")
    assert r.status_code == 200 and r.json() == {"regenerados": 1, "ids": [lote]}
    a, b = radar_store.get_post(individual), radar_store.get_post(lote)
    for campo in ("estado", "gen_prioridad", "aprobado_at", "postulacion_id", "email_subject", "email_body", "intentos", "error"):
        assert a[campo] == b[campo], campo
    for pid, formato in postulaciones:
        postulacion = database.get_postulacion(pid)
        assert postulacion["status"] == "PENDING" and postulacion["formato_cv"] == formato


def test_lote_tambien_incluye_listos_de_fuente_manual(entorno):
    client, _ = entorno
    pid = crear(20, fuente="manual")
    r = client.post("/api/radar/regenerar-listos")
    assert r.status_code == 200
    assert r.json() == {"regenerados": 1, "ids": [pid]}
    assert radar_store.get_post(pid)["estado"] == "GENERANDO"
