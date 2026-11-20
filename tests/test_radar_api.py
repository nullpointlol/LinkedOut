"""Tests de la API del Radar (Task 9) y del wiring en `server.py`.

`server.radar_loop` se reemplaza por un stub (`AsyncMock(spec=RadarLoop)`): el router hace
las transiciones de estado que le pertenecen y delega en el radar el resto, así que acá se
verifica qué método del radar se llamó, nunca un `RadarLoop` real. `TestClient(app)` sin `with`
para que no corra el lifespan (no se reanuda ningún radar ni se abre Edge).
"""
import asyncio
import itertools
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock

import server
from core import database, radar_store
from core.mailer import GmailConfig, MailerAuthError, MailerTemporaryError
from core.radar import RadarLoop

_URN = itertools.count(1)

GMAIL_FALTA = "Gmail no configurado: completá GMAIL_ADDRESS y GMAIL_APP_PASSWORD en .env"


# ----------------------------------------------------------------- fixtures y helpers

@pytest.fixture
def radar(monkeypatch):
    stub = AsyncMock(spec=RadarLoop)
    monkeypatch.setattr(server, "radar_loop", stub)
    return stub


@pytest.fixture
def gmail(monkeypatch):
    """Gmail configurado (sin leer ningún .env real)."""
    monkeypatch.setattr("core.radar_api.load_gmail_config", lambda: GmailConfig("yo@correo-ficticio.test", "app-pass"))


@pytest.fixture
def no_gmail(monkeypatch):
    monkeypatch.setattr("core.radar_api.load_gmail_config", lambda: None)


@pytest.fixture
def client(radar):
    return TestClient(server.app)


def make_post(estado="LISTO", email_to="rrhh@empresa.test", body="Hola, adjunto mi CV.", **extra) -> int:
    n = next(_URN)
    pid = radar_store.insert_post_if_new(
        {
            "urn": f"urn:li:activity:{n}",
            "url": f"https://www.linkedin.com/feed/update/urn:li:activity:{n}/",
            "autor": f"Autor {n}",
            "cargo_autor": "HR",
            "texto": "Buscamos analista funcional. Enviá tu CV a rrhh@empresa.test",
            "fecha_rel": "3 h",
            "emails": [email_to] if email_to else [],
        },
        query='"enviar cv"',
    )
    campos = dict(
        estado=estado,
        triage={"es_busqueda_laboral": True, "puesto": "Analista Funcional", "empresa": "Acme",
                "match_rapido": 80, "motivo": "encaja"},
        email_to=email_to,
        email_subject="Postulación – Analista Funcional",
        email_body=body,
    )
    campos.update(extra)
    radar_store.update_post(pid, **campos)
    return pid


def mark_sent(email_to: str, days_ago: int = 1) -> int:
    sent_at = (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")
    return make_post(estado="ENVIADO", email_to=email_to, sent_at=sent_at, message_id="<x@correo-ficticio.test>")


# ----------------------------------------------------------------- start / stop / status

def test_start_stop_status(client, radar, gmail):
    r = client.post("/api/radar/start")
    assert r.status_code == 200
    assert r.json()["desired_running"] is True
    radar.start.assert_awaited_once()

    r = client.post("/api/radar/stop")
    assert r.status_code == 200
    assert r.json()["desired_running"] is False
    # Se agenda en segundo plano (TestClient espera las background tasks)
    radar.stop.assert_awaited_once()

    r = client.get("/api/radar/status")
    assert r.status_code == 200
    data = r.json()
    assert data["gmail_configurado"] is True
    assert data["desired_running"] is False
    for clave in ("estado", "estado_detalle", "next_cycle_at", "contadores_dia", "umbral_match",
                  "tope_posts_dia", "tope_envios_dia", "envios_pausados"):
        assert clave in data


def test_status_reports_gmail_missing(client, no_gmail):
    assert client.get("/api/radar/status").json()["gmail_configurado"] is False


def test_stop_skipped_if_user_restarted_before_it_ran(radar, gmail):
    """Si al correr el stop en segundo plano el usuario ya volvió a iniciar, no se detiene."""
    from core import radar_api

    radar_store.update_radar_state(desired_running=True)
    asyncio.run(radar_api._detener_si_corresponde(radar))
    radar.stop.assert_not_awaited()

    radar_store.update_radar_state(desired_running=False)
    asyncio.run(radar_api._detener_si_corresponde(radar))
    radar.stop.assert_awaited_once()


def test_show_browser_and_resume_sends(client, radar):
    r = client.post("/api/radar/show-browser")
    assert r.status_code == 200
    radar.show_browser.assert_awaited_once()

    radar_store.update_radar_state(envios_pausados=True)
    r = client.post("/api/radar/resume-sends")
    assert r.status_code == 200
    radar.resume_sends.assert_awaited_once()


def test_show_browser_failure_returns_503(client, radar):
    radar.show_browser.side_effect = RuntimeError("Edge no instalado")
    r = client.post("/api/radar/show-browser")
    assert r.status_code == 503
    assert "Edge no instalado" in r.json()["detail"]


def test_router_uses_current_server_radar_loop(monkeypatch):
    """El router obtiene el radar en cada request: reemplazar `server.radar_loop` tiene efecto."""
    otro = AsyncMock(spec=RadarLoop)
    monkeypatch.setattr(server, "radar_loop", otro)
    TestClient(server.app).post("/api/radar/show-browser")
    otro.show_browser.assert_awaited_once()


# ----------------------------------------------------------------- listado y detalle

def test_list_and_detail_posts(client):
    listo = make_post("LISTO")
    make_post("GENERANDO")
    make_post("DESCARTADO")

    r = client.get("/api/radar/posts", params={"estado": "LISTO"})
    assert r.status_code == 200
    posts = r.json()
    assert [p["id"] for p in posts] == [listo]
    resumen = posts[0]
    assert resumen["puesto"] == "Analista Funcional"
    assert resumen["empresa"] == "Acme"
    assert resumen["match_score"] is None and resumen["analizado"] is False
    radar_store.update_post(listo, analisis={"puesto": "Analista Sr", "empresa": "Acme SA", "match_score": 71.5})
    resumen = client.get("/api/radar/posts", params={"estado": "LISTO"}).json()[0]
    assert (resumen["puesto"], resumen["empresa"], resumen["match_score"]) == ("Analista Sr", "Acme SA", 71.5)
    assert resumen["requisitos_estado"] == []
    radar_store.update_post(listo, analisis={"match_score": 71.5, "requisitos": [
        {"nombre": "SQL", "estado": "MATCH_DIRECTO", "aprendido": True, "justificacion": "texto largo"},
        {"nombre": "BPMN", "estado": "BRECHA_REAL"}, "basura"]})
    resumen = client.get("/api/radar/posts", params={"estado": "LISTO"}).json()[0]
    assert resumen["requisitos_estado"] == [{"estado": "MATCH_DIRECTO", "aprendido": True},
                                            {"estado": "BRECHA_REAL"}]
    radar_store.update_post(listo, analisis={"puesto": "Analista Sr", "empresa": "Acme SA", "match_score": 71.5})
    assert resumen["analizado"] is True
    assert client.get(f"/api/radar/posts/{listo}").json()["analisis"]["match_score"] == 71.5
    assert resumen["email_to"] == "rrhh@empresa.test"
    assert "email_body" not in resumen  # lista resumida

    assert len(client.get("/api/radar/posts").json()) == 3
    assert client.get("/api/radar/posts", params={"estado": "INVENTADO"}).status_code == 422

    viejo_desc = make_post("DESCARTADO")
    # Los no descartados van todos, sin tope; de los descartados, solo los N más recientes
    todos = client.get("/api/radar/posts", params={"limite_descartados": 1}).json()
    assert sorted(p["estado"] for p in todos) == ["DESCARTADO", "GENERANDO", "LISTO"]
    assert [p["id"] for p in todos if p["estado"] == "DESCARTADO"] == [viejo_desc]
    make_post("IGNORADA")
    ign = make_post("IGNORADA")
    todos = client.get("/api/radar/posts", params={"limite_descartados": 1}).json()
    assert [p["id"] for p in todos if p["estado"] == "IGNORADA"] == [ign]
    assert sum(p["estado"] == "LISTO" for p in todos) == 1
    assert [p["id"] for p in todos] == sorted((p["id"] for p in todos), reverse=True)
    assert client.get("/api/radar/posts", params={"limite_descartados": 1, "estado": "LISTO"}).status_code == 422

    # Detalle sin postulación asociada
    d = client.get(f"/api/radar/posts/{listo}").json()
    assert d["email_body"] == "Hola, adjunto mi CV."
    assert d["postulacion"] is None

    # Detalle con postulación asociada
    pid = database.create_postulacion("Analista Funcional", "Acme", "texto", origen="linkedin", status="READY")
    radar_store.update_post(listo, postulacion_id=pid)
    d = client.get(f"/api/radar/posts/{listo}").json()
    assert d["postulacion"]["id"] == pid
    assert d["postulacion"]["status"] == "READY"

def test_lista_liviana_sin_justificacion(client):
    """La lista no manda la justificación de cada requisito (el 76 % del peso); el detalle sí."""
    pid = make_post("LISTO")
    largo = "porque " * 200
    reqs = [
        {"nombre": "SQL", "estado": "MATCH_DIRECTO", "aprendido": True, "justificacion": largo,
         "tipo": "REQUISITO_EXCLUYENTE"},
        {"nombre": "Inglés", "estado": "BRECHA_REAL", "dura": True, "justificacion": largo,
         "eliminatorio": True, "categoria_descarte": "idioma"},
        {"nombre": "Presencial", "estado": "TRANSFERIBLE", "logistico": True, "justificacion": largo},
    ]
    radar_store.update_post(pid, analisis={"match_score": 50, "requisitos": reqs})
    lista = client.get("/api/radar/posts", params={"estado": "LISTO"}).json()[0]["requisitos_estado"]
    # Las marcas en falso no viajan: ausente vale lo mismo para la tarjeta
    assert lista == [
        {"estado": "MATCH_DIRECTO", "aprendido": True, "tipo": "REQUISITO_EXCLUYENTE"},
        {"estado": "BRECHA_REAL", "eliminatorio": True, "dura": True},
        {"estado": "TRANSFERIBLE", "logistico": True},
    ]
    # Con «no apto», cada requisito lleva su nombre (el sello lo cruza con los motivos)
    radar_store.update_post(pid, analisis={"match_score": 50, "requisitos": reqs,
                                           "no_apto": {"motivos": [{"requisito": "Inglés"}]}})
    lista = client.get("/api/radar/posts", params={"estado": "LISTO"}).json()[0]["requisitos_estado"]
    assert [r["nombre"] for r in lista] == ["SQL", "Inglés", "Presencial"]
    assert all("justificacion" not in r and "categoria_descarte" not in r for r in lista)
    detalle = client.get(f"/api/radar/posts/{pid}").json()["analisis"]["requisitos"]
    assert [r["justificacion"] for r in detalle] == [largo] * 3


def test_lista_reusa_las_colas_mientras_no_cambian(client, monkeypatch):
    """Las posiciones de las colas se recalculan solo si cambió alguna candidata (o pasó la vigencia)."""
    from core import colas, radar_api
    llamadas = []
    original = colas.posiciones
    monkeypatch.setattr(colas, "posiciones", lambda posts, *a: llamadas.append(len(posts)) or original(posts, *a))
    monkeypatch.setattr(radar_api, "_LUGARES_COLA", None)
    pid = make_post("ANALIZADA", body="", analisis={"match_score": 90, "puesto": "Analista"})
    otra = make_post("ANALIZADA", body="", analisis={"match_score": 40, "puesto": "Analista"})
    assert client.get("/api/radar/posts").status_code == 200
    assert client.get("/api/radar/posts", params={"fuente": "manual"}).status_code == 200
    assert llamadas == [2]
    antes = {p["id"]: p["cola"] for p in client.get("/api/radar/posts").json()}
    # Un cambio en una candidata (aunque no toque `updated_at`) se ve en el próximo GET
    radar_store.update_post(otra, analisis={"match_score": 99, "puesto": "Analista"})
    despues = {p["id"]: p["cola"] for p in client.get("/api/radar/posts").json()}
    assert len(llamadas) == 2
    assert antes[pid]["posicion"] != despues[pid]["posicion"]
    # Vencida la vigencia, se recalcula aunque nada haya cambiado (la edad de cada oferta corre sola)
    lugares = radar_api._LUGARES_COLA
    monkeypatch.setattr(radar_api, "_LUGARES_COLA",
                        (lugares[0], lugares[1] - radar_api.VIGENCIA_LUGARES_COLA_S - 1, lugares[2]))
    client.get("/api/radar/posts")
    assert len(llamadas) == 3


def test_lista_comprimida_si_el_navegador_acepta_gzip(client):
    for _ in range(3):  # por encima de MINIMO_GZIP
        make_post("LISTO")
    r = client.get("/api/radar/posts", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200 and r.headers["content-encoding"] == "gzip"
    assert "Accept-Encoding" in r.headers.get("vary", "")
    assert r.json()[0]["estado"] == "LISTO"
    r = client.get("/api/radar/posts", headers={"Accept-Encoding": "identity"})
    assert "content-encoding" not in r.headers and r.json()[0]["estado"] == "LISTO"



# ----------------------------------------------------------------- edición

def test_patch_post_only_when_listo_and_not_approved(client, radar, gmail):
    pid = make_post("LISTO")
    r = client.patch(f"/api/radar/posts/{pid}", json={"email_subject": "  Nuevo asunto  ", "email_body": "Cuerpo nuevo"})
    assert r.status_code == 200
    post = radar_store.get_post(pid)
    assert post["email_subject"] == "Nuevo asunto"
    assert post["email_body"] == "Cuerpo nuevo"
    assert post["email_to"] == "rrhh@empresa.test"  # no se tocó lo que no vino

    assert client.post(f"/api/radar/posts/{pid}/approve", json={"updated_at": radar_store.get_post(pid)["updated_at"]}).status_code == 200
    assert client.patch(f"/api/radar/posts/{pid}", json={"email_body": "otro"}).status_code == 409

    generando = make_post("GENERANDO")
    assert client.patch(f"/api/radar/posts/{generando}", json={"email_body": "x"}).status_code == 409


def test_patch_rejects_invalid_recipient(client):
    pid = make_post("LISTO")
    assert client.patch(f"/api/radar/posts/{pid}", json={"email_to": "no es un mail"}).status_code == 422
    assert client.patch(f"/api/radar/posts/{pid}", json={"email_to": "otro@empresa.test"}).status_code == 200
    assert radar_store.get_post(pid)["email_to"] == "otro@empresa.test"


# ----------------------------------------------------------------- aprobación

def test_approve_sets_aprobado_at_and_enqueues(client, radar, gmail):
    pid = make_post("LISTO")
    r = client.post(f"/api/radar/posts/{pid}/approve", json={"updated_at": radar_store.get_post(pid)["updated_at"]})
    assert r.status_code == 200
    assert radar_store.get_post(pid)["aprobado_at"]
    radar.enqueue_send.assert_awaited_once_with(pid)


def test_approve_without_gmail_config_returns_400(client, radar, no_gmail):
    pid = make_post("LISTO")
    r = client.post(f"/api/radar/posts/{pid}/approve", json={"updated_at": radar_store.get_post(pid)["updated_at"]})
    assert r.status_code == 400
    assert r.json()["detail"] == GMAIL_FALTA
    assert radar_store.get_post(pid)["aprobado_at"] is None
    radar.enqueue_send.assert_not_awaited()


def test_approve_requires_listo_and_complete_email(client, radar, gmail):
    generando = make_post("GENERANDO")
    assert client.post(f"/api/radar/posts/{generando}/approve", json={"updated_at": radar_store.get_post(generando)["updated_at"]}).status_code == 409

    sin_dest = make_post("LISTO", email_to="")
    assert client.post(f"/api/radar/posts/{sin_dest}/approve", json={"updated_at": radar_store.get_post(sin_dest)["updated_at"]}).status_code == 409

    sin_cuerpo = make_post("LISTO", body="   ")
    assert client.post(f"/api/radar/posts/{sin_cuerpo}/approve", json={"updated_at": radar_store.get_post(sin_cuerpo)["updated_at"]}).status_code == 409

    for pid in (generando, sin_dest, sin_cuerpo):
        assert radar_store.get_post(pid)["aprobado_at"] is None
    radar.enqueue_send.assert_not_awaited()


def test_approve_duplicate_requires_confirm(client, radar, gmail):
    mark_sent("rrhh@empresa.test", days_ago=5)
    pid = make_post("LISTO", email_to="RRHH@empresa.test")

    r = client.post(f"/api/radar/posts/{pid}/approve", json={"updated_at": radar_store.get_post(pid)["updated_at"]})
    assert r.status_code == 409
    assert r.json()["detail"] == {"duplicate": True, "email": "RRHH@empresa.test"}
    assert radar_store.get_post(pid)["aprobado_at"] is None

    r = client.post(f"/api/radar/posts/{pid}/approve", json={"confirm_duplicate": True, "updated_at": radar_store.get_post(pid)["updated_at"]})
    assert r.status_code == 200
    radar.enqueue_send.assert_awaited_once_with(pid)


def test_approve_duplicate_also_counts_pending_approved(client, radar, gmail):
    """Otro post al mismo destinatario ya aprobado (aún sin enviar) también es duplicado."""
    primero = make_post("LISTO", email_to="talento@acme.test")
    assert client.post(f"/api/radar/posts/{primero}/approve", json={"updated_at": radar_store.get_post(primero)["updated_at"]}).status_code == 200
    segundo = make_post("LISTO", email_to="talento@acme.test")
    r = client.post(f"/api/radar/posts/{segundo}/approve", json={"updated_at": radar_store.get_post(segundo)["updated_at"]})
    assert r.status_code == 409
    assert r.json()["detail"]["duplicate"] is True


def test_approve_maps_enqueue_value_error_to_409(client, radar, gmail):
    radar.enqueue_send.side_effect = ValueError("no se puede enviar")
    pid = make_post("LISTO")
    r = client.post(f"/api/radar/posts/{pid}/approve", json={"updated_at": radar_store.get_post(pid)["updated_at"]})
    assert r.status_code == 409
    assert radar_store.get_post(pid)["aprobado_at"] is None


def _lo_que_ve_el_usuario(client):
    """Payload de "Aprobar todos" tal como lo arma radar.js: los LISTO sin aprobar de la lista."""
    listos = [p for p in client.get("/api/radar/posts").json() if p["estado"] == "LISTO" and not p["aprobado_at"]]
    return {"ids": [p["id"] for p in listos], "updated_at": {str(p["id"]): p["updated_at"] for p in listos}}


def test_approve_all_skips_duplicates(client, radar, gmail):
    mark_sent("viejo@empresa.test", days_ago=3)
    ok1 = make_post("LISTO", email_to="uno@empresa.test")
    dup = make_post("LISTO", email_to="viejo@empresa.test")
    ok2 = make_post("LISTO", email_to="dos@empresa.test")
    repetido = make_post("LISTO", email_to="dos@empresa.test")  # mismo destinatario que ok2 en el lote
    sin_cuerpo = make_post("LISTO", email_to="tres@empresa.test", body="")
    make_post("GENERANDO", email_to="cuatro@empresa.test")

    r = client.post("/api/radar/approve-all", json=_lo_que_ve_el_usuario(client))
    assert r.status_code == 200
    data = r.json()
    assert sorted(data["encolados"]) == sorted([ok1, ok2])
    omitidos = {o["id"]: o["motivo"] for o in data["omitidos"]}
    assert set(omitidos) == {dup, repetido, sin_cuerpo}
    assert "viejo@empresa.test" in omitidos[dup]
    for pid in (ok1, ok2):
        assert radar_store.get_post(pid)["aprobado_at"]
    for pid in (dup, repetido, sin_cuerpo):
        assert radar_store.get_post(pid)["aprobado_at"] is None
    assert sorted(c.args[0] for c in radar.enqueue_send.await_args_list) == sorted([ok1, ok2])


def test_approve_all_without_gmail_returns_400(client, radar, no_gmail):
    pid = make_post("LISTO")
    r = client.post("/api/radar/approve-all", json={"ids": [pid], "updated_at": {}})
    assert r.status_code == 400
    assert r.json()["detail"] == GMAIL_FALTA
    radar.enqueue_send.assert_not_awaited()


def test_approve_all_only_approves_confirmed_ids(client, radar, gmail):
    """Un post que llegó a LISTO después de que el usuario confirmó no se aprueba."""
    visto = make_post("LISTO", email_to="uno@empresa.test")
    payload = _lo_que_ve_el_usuario(client)
    nuevo = make_post("LISTO", email_to="nuevo@empresa.test")  # llegó por SSE después de confirmar
    r = client.post("/api/radar/approve-all", json=payload)
    assert r.status_code == 200
    assert r.json()["encolados"] == [visto]
    assert radar_store.get_post(nuevo)["aprobado_at"] is None
    radar.enqueue_send.assert_awaited_once_with(visto)


def test_approve_all_skips_posts_changed_since_confirmation(client, radar, gmail):
    cambiado = make_post("LISTO", email_to="uno@empresa.test")
    regenerado = make_post("LISTO", email_to="dos@empresa.test")
    descartado = make_post("LISTO", email_to="tres@empresa.test")
    ok = make_post("LISTO", email_to="cuatro@empresa.test")
    payload = _lo_que_ve_el_usuario(client)
    # Cambios en el servidor entre el conteo y el POST
    radar_store.update_post(cambiado, email_to="otro@empresa.test")
    radar_store.update_post(regenerado, estado="GENERANDO")
    radar_store.update_post(descartado, estado="DESCARTADO")
    payload["ids"].append(999999)  # ya no existe
    r = client.post("/api/radar/approve-all", json=payload)
    assert r.status_code == 200
    data = r.json()
    assert data["encolados"] == [ok]
    omitidos = {o["id"]: o["motivo"] for o in data["omitidos"]}
    assert set(omitidos) == {cambiado, regenerado, descartado, 999999}
    assert "cambió" in omitidos[cambiado]
    assert "cambió" in omitidos[regenerado] and "cambió" in omitidos[descartado]
    for pid in (cambiado, regenerado, descartado):
        assert radar_store.get_post(pid)["aprobado_at"] is None


def test_approve_all_requires_body(client, radar, gmail):
    make_post("LISTO")
    assert client.post("/api/radar/approve-all").status_code == 422
    radar.enqueue_send.assert_not_awaited()


# ----------------------------------------------------------------- transiciones

def test_discard_force_retry_regenerate_transitions(client, radar, gmail):
    # discard: LISTO aprobado → DESCARTADO, limpia aprobado_at, motivo manual
    pid = make_post("LISTO")
    client.post(f"/api/radar/posts/{pid}/approve", json={"updated_at": radar_store.get_post(pid)["updated_at"]})
    r = client.post(f"/api/radar/posts/{pid}/discard", headers={"X-LinkedOut-Origen": "portal"})
    assert r.status_code == 200
    post = radar_store.get_post(pid)
    assert post["estado"] == "DESCARTADO"
    assert post["aprobado_at"] is None
    assert post["triage"]["motivo_descarte"] == "manual"
    assert post["triage"]["motivo"] == "encaja"  # se conserva el motivo del triage

    # force (= generar-cv): delega en el radar; ValueError → 409
    r = client.post(f"/api/radar/posts/{pid}/force")
    assert r.status_code == 200
    radar.generate_cv.assert_awaited_once_with(pid)
    radar.generate_cv.side_effect = ValueError("ya tiene CV")
    assert client.post(f"/api/radar/posts/{pid}/force").status_code == 409
    radar.generate_cv.side_effect = None

    # retry: delega en el radar; ValueError → 409; fuera de ERROR → 409
    err = make_post("ERROR", error="falló el pipeline")
    assert client.post(f"/api/radar/posts/{err}/retry").status_code == 200
    radar.retry.assert_awaited_once_with(err)
    assert client.post(f"/api/radar/posts/{pid}/retry").status_code == 409
    radar.retry.side_effect = ValueError("no está en ERROR")
    assert client.post(f"/api/radar/posts/{err}/retry").status_code == 409

    # regenerate solo email (cv=False, solo por API): conserva postulacion_id, limpia aprobación y email
    radar.wake_generation.reset_mock()
    listo = make_post("LISTO", postulacion_id=None)
    p_id = database.create_postulacion("Analista", "Acme", "texto", origen="linkedin", status="READY")
    radar_store.update_post(listo, postulacion_id=p_id)
    client.post(f"/api/radar/posts/{listo}/approve", json={"updated_at": radar_store.get_post(listo)["updated_at"]})
    r = client.post(f"/api/radar/posts/{listo}/regenerate", json={"cv": False})
    assert r.status_code == 200
    post = radar_store.get_post(listo)
    assert post["estado"] == "GENERANDO"
    assert post["aprobado_at"] is None
    assert post["postulacion_id"] == p_id
    assert not post["email_subject"] and not post["email_body"]
    radar.wake_generation.assert_awaited_once()
    assert database.get_postulacion(p_id)["status"] == "READY"

    # regenerate por defecto (el botón «Regenerar») rehace el CV: borra postulacion_id y marca la
    # postulación para regenerarla
    radar_store.update_post(listo, estado="LISTO", email_subject="A", email_body="B")
    r = client.post(f"/api/radar/posts/{listo}/regenerate", json={})
    assert r.status_code == 200
    post = radar_store.get_post(listo)
    assert post["estado"] == "GENERANDO"
    assert post["postulacion_id"] is None
    assert database.get_postulacion(p_id)["status"] != "READY"

    # regenerate no aplica a ENVIADO / GENERANDO
    enviado = mark_sent("x@empresa.test")
    assert client.post(f"/api/radar/posts/{enviado}/regenerate", json={}).status_code == 409
    assert client.post(f"/api/radar/posts/{listo}/regenerate", json={}).status_code == 409


def test_restaurar_devuelve_el_post_a_su_estado_previo(client, radar):
    pid = make_post("LISTO")
    client.post(f"/api/radar/posts/{pid}/approve", json={"updated_at": radar_store.get_post(pid)["updated_at"]})
    client.post(f"/api/radar/posts/{pid}/discard", headers={"X-LinkedOut-Origen": "portal"})
    assert radar_store.get_post(pid)["triage"]["estado_previo"] == "LISTO"

    r = client.post(f"/api/radar/posts/{pid}/restaurar")
    assert r.status_code == 200
    assert r.json()["estado"] == "LISTO"
    post = radar_store.get_post(pid)
    assert post["estado"] == "LISTO"
    assert post["aprobado_at"] is None  # hay que volver a aprobar
    assert "motivo_descarte" not in post["triage"] and "estado_previo" not in post["triage"]
    assert post["triage"]["motivo"] == "encaja"  # el resto del triage queda

    # Ya restaurado: no hay nada que deshacer
    assert client.post(f"/api/radar/posts/{pid}/restaurar").status_code == 409


def test_descartar_un_post_ya_descartado_no_guarda_un_estado_previo_nuevo(client, radar):
    auto = make_post("DESCARTADO", triage={"motivo": "no aplica", "motivo_descarte": "triage"})
    assert client.post(f"/api/radar/posts/{auto}/discard", headers={"X-LinkedOut-Origen": "portal"}).status_code == 200
    assert "estado_previo" not in radar_store.get_post(auto)["triage"]
    assert client.post(f"/api/radar/posts/{auto}/restaurar").status_code == 409
    # Descartado a mano dos veces: el estado previo sigue siendo el original (no pasa a DESCARTADO)
    pid = make_post("LISTO")
    client.post(f"/api/radar/posts/{pid}/discard", headers={"X-LinkedOut-Origen": "portal"})
    client.post(f"/api/radar/posts/{pid}/discard", headers={"X-LinkedOut-Origen": "portal"})
    assert radar_store.get_post(pid)["triage"]["estado_previo"] == "LISTO"


@pytest.mark.parametrize("salida", ["OFERTA", "ANALIZANDO", "GENERANDO", "LISTO", "ERROR"])
def test_un_post_que_sale_de_descartado_por_otra_via_pierde_el_estado_previo(client, radar, salida):
    pid = make_post("LISTO")
    client.post(f"/api/radar/posts/{pid}/discard", headers={"X-LinkedOut-Origen": "portal"})
    assert radar_store.get_post(pid)["triage"]["estado_previo"] == "LISTO"
    radar_store.update_post(pid, estado=salida)
    triage = radar_store.get_post(pid)["triage"]
    assert "estado_previo" not in triage and triage["motivo"] == "encaja"
    assert client.post(f"/api/radar/posts/{pid}/restaurar").status_code == 409


def test_restaurar_generando_despierta_la_generacion_y_analizando_vuelve_a_oferta(client, radar):
    gen = make_post("GENERANDO")
    client.post(f"/api/radar/posts/{gen}/discard", headers={"X-LinkedOut-Origen": "portal"})
    radar.wake_generation.reset_mock()
    assert client.post(f"/api/radar/posts/{gen}/restaurar").json()["estado"] == "GENERANDO"
    radar.wake_generation.assert_awaited_once()

    ana = make_post("ANALIZANDO")
    client.post(f"/api/radar/posts/{ana}/discard", headers={"X-LinkedOut-Origen": "portal"})
    assert client.post(f"/api/radar/posts/{ana}/restaurar").json()["estado"] == "OFERTA"


@pytest.mark.parametrize("estado", ["NUEVO", "OFERTA"])
def test_restaurar_nuevo_u_oferta_despierta_el_procesamiento(client, radar, estado):
    pid = make_post(estado)
    client.post(f"/api/radar/posts/{pid}/discard", headers={"X-LinkedOut-Origen": "portal"})
    radar.wake_processing.reset_mock()
    radar.wake_generation.reset_mock()
    assert client.post(f"/api/radar/posts/{pid}/restaurar").json()["estado"] == estado
    radar.wake_processing.assert_awaited_once()
    radar.wake_generation.assert_not_awaited()


def test_restaurar_rechaza_lo_que_no_fue_un_descarte_manual(client):
    # descartado por el triage: sin estado_previo
    auto = make_post("DESCARTADO")
    radar_store.update_post(auto, triage={"es_busqueda_laboral": False, "motivo": "no es oferta"})
    assert client.post(f"/api/radar/posts/{auto}/restaurar").status_code == 409
    # descarte manual de un post que ya estaba descartado: no hay a dónde volver
    client.post(f"/api/radar/posts/{auto}/discard", headers={"X-LinkedOut-Origen": "portal"})
    assert client.post(f"/api/radar/posts/{auto}/restaurar").status_code == 409
    # post activo
    assert client.post(f"/api/radar/posts/{make_post('LISTO')}/restaurar").status_code == 409
    # inexistente
    assert client.post("/api/radar/posts/999999/restaurar").status_code == 404


def test_discard_not_allowed_once_sending_or_sent(client):
    for estado in ("ENVIANDO", "ENVIADO"):
        pid = make_post(estado)
        assert client.post(f"/api/radar/posts/{pid}/discard", headers={"X-LinkedOut-Origen": "portal"}).status_code == 409
        assert radar_store.get_post(pid)["estado"] == estado


# ----------------------------------------------------------------- config

def test_config_threshold_validation(client):
    assert client.patch("/api/radar/config", json={"umbral_match": 101}).status_code == 422
    assert client.patch("/api/radar/config", json={"umbral_match": -1}).status_code == 422
    r = client.patch("/api/radar/config", json={"umbral_match": 70})
    assert r.status_code == 200
    assert r.json()["umbral_match"] == 70
    assert radar_store.get_radar_state()["umbral_match"] == 70


# ----------------------------------------------------------------- 404

def test_unknown_post_404(client, gmail):
    rutas = [
        ("get", "/api/radar/posts/9999", None),
        ("patch", "/api/radar/posts/9999", {"email_body": "x"}),
        ("post", "/api/radar/posts/9999/approve", {}),
        ("post", "/api/radar/posts/9999/discard", None),
        ("post", "/api/radar/posts/9999/force", None),
        ("post", "/api/radar/posts/9999/retry", None),
        ("post", "/api/radar/posts/9999/regenerate", {}),
    ]
    for metodo, ruta, body in rutas:
        kwargs = {"json": body} if body is not None else {}
        r = getattr(client, metodo)(ruta, **kwargs)
        assert r.status_code == 404, (metodo, ruta, r.status_code)


# ----------------------------------------------------------------- /api/ofertas

def test_api_ofertas_solo_historial_manual(client):
    # Las postulaciones nuevas del motor se consultan desde el post asociado.
    manual = database.create_postulacion("Manual", "Acme", "texto")
    radar_cv = database.create_postulacion("Radar", "Acme", "texto", origen="linkedin")
    ids = [o["id"] for o in client.get("/api/ofertas").json()]
    assert ids == [manual] and radar_cv not in ids
    # El filtro de ciclo de vida sigue funcionando
    assert client.get("/api/ofertas", params={"filtro_lifecycle": "TODAS"}).status_code == 200


# ----------------------------------------------------------------- wiring en server.py

def test_analizar_and_generar_cv_delegate_to_the_radar(client, radar):
    pid = make_post("ANALIZADA")
    assert client.post(f"/api/radar/posts/{pid}/analizar").status_code == 200
    radar.prioritize.assert_awaited_once_with(pid)
    assert client.post(f"/api/radar/posts/{pid}/generar-cv").status_code == 200
    radar.generate_cv.assert_awaited_once_with(pid)
    radar.prioritize.side_effect = ValueError("ya tiene CV")
    assert client.post(f"/api/radar/posts/{pid}/analizar").status_code == 409
    assert client.post("/api/radar/posts/9999/analizar").status_code == 404
    assert client.post("/api/radar/posts/9999/generar-cv").status_code == 404


def test_server_radar_loop_wired_with_real_functions():
    from core.triage import triage_batch

    real = server._build_radar_loop()
    assert isinstance(real, RadarLoop)
    # Por nombre: test_linkedin_harvester re-importa el módulo, la identidad de la clase cambia
    factory = real._harvester_factory
    assert (factory.__module__, factory.__qualname__) == ("core.linkedin_harvester", "LinkedInHarvester")
    assert real._triage_fn is triage_batch
    assert real._analyze_fn is server._radar_analyze
    assert real._pipeline_fn is server._radar_pipeline
    assert real._compose_fn is server._radar_compose
    assert real._send_fn is server._radar_send
    assert real._broadcast is server.broadcast_event


def _linkedin_post(url="https://www.linkedin.com/feed/update/urn:li:activity:77/") -> dict:
    pid = make_post("GENERANDO")
    radar_store.update_post(pid, url=url)
    return radar_store.get_post(pid)


@pytest.mark.asyncio
async def test_radar_pipeline_creates_linkedin_postulacion(monkeypatch):
    llamadas = []

    async def fake_pipeline(pid, url=None):
        llamadas.append(pid)
        database.update_postulacion(pid, status="READY")

    monkeypatch.setattr(server, "run_pipeline_task", fake_pipeline)
    post = _linkedin_post()
    pid = await server._radar_pipeline(post)

    p = database.get_postulacion(pid)
    assert p["origen"] == "linkedin"
    assert p["titulo"] == "Analista Funcional"
    assert p["empresa"] == "Acme"
    assert p["url"] == post["url"]
    assert p["contenido_oferta"] == post["texto"]
    assert llamadas == [pid]
    assert radar_store.get_post(post["id"])["postulacion_id"] == pid

    # Idempotente: con la postulación READY se reutiliza sin re-ejecutar el pipeline
    assert await server._radar_pipeline(post) == pid
    assert llamadas == [pid]
    assert len(database.list_postulaciones(origen="linkedin")) == 1


@pytest.mark.asyncio
async def test_radar_pipeline_reuses_the_radar_analysis_and_skips_the_strategist(monkeypatch):
    """Generar CV sobre una oferta ya analizada: la postulación nace con ese análisis y solo
    corren Redactor y Auditor (recalculate_cv_task), sin repetir el Estratega."""
    llamadas = []

    async def fake_recalc(pid):
        llamadas.append(("recalc", pid))
        database.update_postulacion(pid, status="READY")

    async def fake_pipeline(pid, url=None):
        llamadas.append(("pipeline", pid))

    monkeypatch.setattr(server, "recalculate_cv_task", fake_recalc)
    monkeypatch.setattr(server, "run_pipeline_task", fake_pipeline)
    post = _linkedin_post()
    reqs = [{"nombre": "SQL", "tipo": "REQUISITO_EXCLUYENTE", "estado": "MATCH_DIRECTO", "approved": True}]
    radar_store.update_post(post["id"], analisis={
        "puesto": "Data Analyst", "empresa": "TRANSENER", "match_score": 65.0,
        "dolores_empresa": ["Datos dispersos"], "requisitos": reqs,
    })
    pid = await server._radar_pipeline(radar_store.get_post(post["id"]))
    p = database.get_postulacion(pid)
    assert llamadas == [("recalc", pid)]
    assert (p["titulo"], p["empresa"], p["match_score"]) == ("Data Analyst", "TRANSENER", 65.0)
    assert p["requisitos"] == reqs and p["origen"] == "linkedin"


@pytest.mark.asyncio
async def test_radar_analyze_uses_the_selected_model_with_medium_effort_and_rejects_fallback(monkeypatch):
    from core.bridge import AntigravityBridge
    vistos = {}

    async def fake_analyze(self, offer_text, effort=None, datos_de_terceros=False, timeout_seconds=None):
        vistos.update(effort=self.effort, bridge_effort=self.bridge.effort, model=self.bridge.model,
                      terceros=datos_de_terceros, texto=offer_text, timeout=timeout_seconds)
        return dict(respuesta)

    respuesta = {"puesto": "QA", "requisitos": [{"nombre": "Selenium", "tipo": "REQUISITO_EXCLUYENTE",
                                                 "estado": "BRECHA_REAL"}], "match_score": 40.0}
    monkeypatch.setattr("core.strategist.StrategistAgent.analyze_offer", fake_analyze)
    monkeypatch.setattr(AntigravityBridge, "active_model", "gemini-3.8-flash-high")
    monkeypatch.setattr(AntigravityBridge, "active_effort", "high")
    analisis = await server._radar_analyze({"id": 1, "texto": "Buscamos QA en CABA"})
    # Match y score van siempre con razonamiento Medium, sea cual sea el elegido en el portal
    assert vistos == {"effort": "medium", "bridge_effort": "medium", "model": "gemini-3.8-flash-high",
                      "terceros": True, "texto": "Buscamos QA en CABA", "timeout": 420}
    assert analisis["match_score"] == 40.0 and "matriz_oferta" in analisis

    respuesta["analisis_fallback"] = True
    with pytest.raises(RuntimeError):
        await server._radar_analyze({"id": 1, "texto": "Buscamos QA"})


@pytest.mark.asyncio
async def test_radar_analyze_reads_the_job_links_like_a_person_would(monkeypatch):
    """Post corto con link: se lee la página de la oferta (y se sigue el aviso de link externo de
    lnkd.in) antes de analizar; una oferta que ya no acepta postulaciones queda marcada."""
    visitados, textos = [], []

    async def fake_scrape(url, timeout=15):
        visitados.append(url)
        paginas = {
            "https://www.linkedin.com/jobs/view/123/": {
                "titulo": "Soporte L1", "contenido": "Soporte L1 Power Platform. Ya no se aceptan solicitudes. Requisitos: SQL"},
            "https://lnkd.in/abc": {"titulo": "LinkedIn", "contenido":
                "This link will take you to a page that’s not on LinkedIn https://empresa.com/jobs/42 Continue"},
            "https://empresa.com/jobs/42": {"titulo": "Analista", "contenido": "Analista de datos. Requisitos: Python"},
        }
        return paginas.get(url, {"contenido": "", "error": "404"})

    async def fake_analyze(self, offer_text, effort=None, datos_de_terceros=False, timeout_seconds=None):
        textos.append(offer_text)
        return {"puesto": "X", "requisitos": [{"nombre": "SQL", "estado": "MATCH_DIRECTO"}], "match_score": 50.0}

    monkeypatch.setattr(server, "scrape_job_url", fake_scrape)
    monkeypatch.setattr("core.strategist.StrategistAgent.analyze_offer", fake_analyze)
    post = {"id": 1, "texto": "Conocé nuestra búsqueda en el botón",
            "links": ["https://www.linkedin.com/jobs/view/123/", "https://lnkd.in/abc",
                      "https://x.com/1", "https://x.com/2", "https://x.com/3"]}
    analisis = await server._radar_analyze(post)
    assert visitados[:2] == ["https://www.linkedin.com/jobs/view/123/", "https://lnkd.in/abc"]
    assert "https://empresa.com/jobs/42" in visitados  # siguió el aviso de link externo
    assert not [u for u in visitados if u.startswith("https://x.com")]  # máx. 2 links del post
    assert "Power Platform" in textos[0] and "Analista de datos" in textos[0]
    assert textos[0].startswith("Conocé nuestra búsqueda")
    assert analisis["oferta_cerrada"] is True
    assert "https://www.linkedin.com/jobs/view/123/" in analisis["links_leidos"]


@pytest.mark.asyncio
async def test_radar_analyze_retry_skips_the_links(monkeypatch):
    """Si el análisis ya falló (p. ej. timeout por el texto extra de los links), el reintento va
    solo con el texto del post, que responde rápido."""
    visitados = []

    async def scrape(url, timeout=15):
        visitados.append(url)
        return {"contenido": "Requisitos: SQL"}

    async def fake_analyze(self, offer_text, effort=None, datos_de_terceros=False, timeout_seconds=None):
        return {"requisitos": [{"nombre": "SQL"}], "match_score": 10.0}

    monkeypatch.setattr(server, "scrape_job_url", scrape)
    monkeypatch.setattr("core.strategist.StrategistAgent.analyze_offer", fake_analyze)
    post = {"id": 1, "texto": "Buscamos QA", "links": ["https://www.linkedin.com/jobs/view/1/"]}
    assert (await server._radar_analyze(dict(post, intentos=0)))["links_leidos"] != []
    visitados.clear()
    analisis = await server._radar_analyze(dict(post, intentos=1))
    assert visitados == [] and analisis["links_leidos"] == []


@pytest.mark.asyncio
async def test_radar_analyze_without_links_does_not_scrape(monkeypatch):
    async def no_scrape(url, timeout=15):
        raise AssertionError("no debería scrapear")

    async def fake_analyze(self, offer_text, effort=None, datos_de_terceros=False, timeout_seconds=None):
        return {"requisitos": [{"nombre": "SQL"}], "match_score": 10.0}

    monkeypatch.setattr(server, "scrape_job_url", no_scrape)
    monkeypatch.setattr("core.strategist.StrategistAgent.analyze_offer", fake_analyze)
    analisis = await server._radar_analyze({"id": 1, "texto": "Buscamos QA", "links": []})
    assert analisis["oferta_cerrada"] is False and analisis["links_leidos"] == []


@pytest.mark.asyncio
async def test_radar_pipeline_never_reuses_postulacion_of_another_post_by_same_author(monkeypatch):
    """En el DOM de 2026 `url` es el perfil del autor: dos posts suyos no comparten CV."""
    llamadas = []

    async def fake_pipeline(pid, url=None):
        llamadas.append(pid)
        database.update_postulacion(pid, status="READY")

    monkeypatch.setattr(server, "run_pipeline_task", fake_pipeline)
    perfil = "https://www.linkedin.com/in/reclutadora-ficticia/"
    primero = _linkedin_post(url=perfil)
    segundo = _linkedin_post(url=perfil)
    radar_store.update_post(segundo["id"], texto="Buscamos QA Automation. CV a rrhh@empresa.test")
    segundo = radar_store.get_post(segundo["id"])
    assert primero["urn"] != segundo["urn"]

    pid1 = await server._radar_pipeline(primero)
    pid2 = await server._radar_pipeline(segundo)
    assert pid1 != pid2
    assert llamadas == [pid1, pid2]
    assert database.get_postulacion(pid2)["contenido_oferta"] == segundo["texto"]
    assert database.get_postulacion(pid1)["radar_post_ref"] == primero["urn"]
    assert database.get_postulacion(pid2)["radar_post_ref"] == segundo["urn"]

    # El mismo post reintentado reutiliza su propia postulación
    assert await server._radar_pipeline(segundo) == pid2
    assert await server._radar_pipeline(primero) == pid1
    assert llamadas == [pid1, pid2]


@pytest.mark.asyncio
async def test_radar_pipeline_legacy_row_reused_only_by_post_permalink(monkeypatch):
    """Filas previas a `radar_post_ref`: se reutilizan solo si su URL es el permalink del post."""
    async def fake_pipeline(pid, url=None):
        database.update_postulacion(pid, status="READY")

    monkeypatch.setattr(server, "run_pipeline_task", fake_pipeline)
    permalink = "https://www.linkedin.com/feed/update/urn:li:activity:4242/"
    vieja = database.create_postulacion("Analista", "Acme", "texto", url=permalink, origen="linkedin", status="READY")
    perfil = "https://www.linkedin.com/in/autor-ficticio/"
    vieja_perfil = database.create_postulacion("Analista", "Acme", "texto", url=perfil, origen="linkedin", status="READY")

    post = _linkedin_post(url=permalink)
    assert await server._radar_pipeline(post) == vieja
    assert database.get_postulacion(vieja)["radar_post_ref"] == post["urn"]

    otro = _linkedin_post(url=perfil)
    assert await server._radar_pipeline(otro) not in (vieja, vieja_perfil)


@pytest.mark.asyncio
async def test_radar_pipeline_failure_raises_and_reruns_same_postulacion(monkeypatch):
    llamadas = []
    resultado = {"status": "ERROR"}

    async def fake_pipeline(pid, url=None):
        llamadas.append(pid)
        database.update_postulacion(pid, status=resultado["status"])

    monkeypatch.setattr(server, "run_pipeline_task", fake_pipeline)
    post = _linkedin_post()
    with pytest.raises(RuntimeError):
        await server._radar_pipeline(post)
    # El fallo libera el vínculo: el reintento del radar vuelve a correr el pipeline
    assert radar_store.get_post(post["id"])["postulacion_id"] is None

    resultado["status"] = "READY"
    pid = await server._radar_pipeline(post)
    assert llamadas == [pid, pid]  # misma postulación, sin duplicar
    assert len(database.list_postulaciones(origen="linkedin")) == 1


@pytest.mark.asyncio
async def test_radar_compose_uses_postulacion(monkeypatch):
    vistos = {}

    async def fake_compose(post, postulacion, profile=None, bridge=None):
        vistos["post"], vistos["postulacion"] = post, postulacion
        return {"to": "rrhh@empresa.test", "subject": "S", "body": "B"}

    monkeypatch.setattr(server, "compose_message", fake_compose)
    pid = database.create_postulacion("Analista", "Acme", "texto", origen="linkedin", status="READY")
    database.update_postulacion(pid, cv_markdown="# CV")
    post = _linkedin_post()
    radar_store.update_post(post["id"], postulacion_id=pid)
    email = await server._radar_compose(post, pid)
    assert email["subject"] == "S"
    assert vistos["postulacion"]["id"] == pid
    assert vistos["post"]["id"] == post["id"]
    assert radar_store.get_post(post["id"])["postulacion_id"] == pid


@pytest.mark.asyncio
async def test_radar_pipeline_cancelled_clears_link_and_propagates(monkeypatch):
    """stop()/cierre cancelan el pipeline: el post no queda vinculado a un CV a medio hacer."""
    entro = asyncio.Event()

    async def pipeline_colgado(pid, url=None):
        database.update_postulacion(pid, status="PROCESSING")
        entro.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(server, "run_pipeline_task", pipeline_colgado)
    post = _linkedin_post()
    tarea = asyncio.create_task(server._radar_pipeline(post))
    await asyncio.wait_for(entro.wait(), 60)
    assert radar_store.get_post(post["id"])["postulacion_id"] is not None  # vinculado durante el pipeline
    tarea.cancel()
    with pytest.raises(asyncio.CancelledError):
        await tarea
    assert radar_store.get_post(post["id"])["postulacion_id"] is None

    # El próximo intento re-ejecuta el pipeline sobre la misma fila
    async def pipeline_ok(pid, url=None):
        database.update_postulacion(pid, status="READY")

    monkeypatch.setattr(server, "run_pipeline_task", pipeline_ok)
    pid = await server._radar_pipeline(post)
    assert len(database.list_postulaciones(origen="linkedin")) == 1
    assert radar_store.get_post(post["id"])["postulacion_id"] == pid


@pytest.mark.asyncio
@pytest.mark.parametrize("status,cv", [("PROCESSING", "# CV"), ("PENDING", "# CV viejo"), ("READY", ""), ("READY", None)])
async def test_radar_compose_refuses_postulacion_without_ready_cv(monkeypatch, status, cv):
    compose = AsyncMock(return_value={"to": "a@b.test", "subject": "S", "body": "B"})
    monkeypatch.setattr(server, "compose_message", compose)
    pid = database.create_postulacion("Analista", "Acme", "texto", origen="linkedin", status=status)
    if cv is not None:
        database.update_postulacion(pid, cv_markdown=cv)
    post = _linkedin_post()
    radar_store.update_post(post["id"], postulacion_id=pid)
    with pytest.raises(RuntimeError):
        await server._radar_compose(post, pid)
    compose.assert_not_awaited()
    assert radar_store.get_post(post["id"])["postulacion_id"] is None


@pytest.mark.asyncio
async def test_radar_compose_missing_postulacion_raises(monkeypatch):
    post = _linkedin_post()
    radar_store.update_post(post["id"], postulacion_id=9999)
    with pytest.raises(RuntimeError):
        await server._radar_compose(post, 9999)
    assert radar_store.get_post(post["id"])["postulacion_id"] is None


def test_regenerate_cv_makes_old_cv_unusable_until_pipeline_finishes(client, radar, monkeypatch):
    """Tras regenerate {"cv": true} el CV viejo no puede usarse para redactar ni adjuntar."""
    compose = AsyncMock(return_value={"to": "a@b.test", "subject": "S", "body": "B"})
    monkeypatch.setattr(server, "compose_message", compose)
    pid = database.create_postulacion("Analista", "Acme", "texto", origen="linkedin", status="READY")
    database.update_postulacion(pid, cv_markdown="# CV viejo")
    listo = make_post("LISTO", postulacion_id=pid)

    assert client.post(f"/api/radar/posts/{listo}/regenerate", json={"cv": True}).status_code == 200
    post = radar_store.get_post(listo)
    assert post["postulacion_id"] is None
    with pytest.raises(RuntimeError):
        asyncio.run(server._radar_compose(post, pid))
    compose.assert_not_awaited()


@pytest.mark.asyncio
async def test_radar_send_without_gmail_raises_auth_error(monkeypatch):
    monkeypatch.setattr(server, "load_gmail_config", lambda: None)
    with pytest.raises(MailerAuthError):
        await server._radar_send(_linkedin_post())


@pytest.mark.asyncio
async def test_radar_send_compiles_pdf_and_sends(monkeypatch, tmp_path):
    config = GmailConfig("yo@correo-ficticio.test", "app-pass")
    monkeypatch.setattr(server, "load_gmail_config", lambda: config)
    monkeypatch.setattr(server, "load_candidate_profile", lambda: {"nombre": "Sam Ortega"})
    compilado = {}

    def fake_compile(markdown, target_role=None, output_filename=None, keywords=None, candidate_name=None):
        compilado.update(markdown=markdown, target_role=target_role, output_filename=output_filename, keywords=keywords)
        path = tmp_path / output_filename
        path.write_bytes(b"%PDF-1.4")
        return path

    enviado = {}
    before_send_calls = []

    def fake_send(to, subject, body, pdf_path, from_name, config, html_body=None, *, before_send=None):
        assert callable(before_send), "before_send debe ser un callable"
        before_send_calls.append(before_send)
        before_send()
        enviado.update(to=to, subject=subject, body=body, pdf_path=Path(pdf_path), from_name=from_name, config=config, html_body=html_body)
        return "<abc@correo-ficticio.test>"

    monkeypatch.setattr(server, "compile_markdown_to_pdf", fake_compile)
    monkeypatch.setattr(server, "send_application_email", fake_send)

    pid = database.create_postulacion("Analista Funcional", "Acme", "texto", origen="linkedin", status="READY")
    database.update_postulacion(
        pid, cv_markdown="# CV",
        requisitos=[{"nombre": "SQL", "approved": True}, {"nombre": "Java", "approved": False}],
    )
    post = _linkedin_post()
    radar_store.update_post(post["id"], postulacion_id=pid, estado="ENVIANDO", aprobado_at="aprobado")
    post = radar_store.get_post(post["id"])

    assert await server._radar_send(post) == "<abc@correo-ficticio.test>"
    assert compilado["markdown"] == "# CV"
    assert compilado["target_role"] == "Analista Funcional"
    assert compilado["keywords"] == ["SQL"]
    assert compilado["output_filename"].endswith(".pdf")
    assert enviado["to"] == "rrhh@empresa.test"
    assert enviado["subject"] == "Postulación – Analista Funcional"
    assert enviado["body"] == "Hola, adjunto mi CV."
    assert enviado["pdf_path"].name == compilado["output_filename"]
    assert enviado["from_name"] == "Sam Ortega"
    assert enviado["config"] is config
    assert enviado["html_body"].startswith("<div") and "Hola, adjunto mi CV." in enviado["html_body"]
    assert len(before_send_calls) == 1


@pytest.mark.asyncio
async def test_lifespan_resumes_and_stops_radar(radar):
    async with server.lifespan(server.app):
        radar.resume_if_needed.assert_awaited_once()
        radar.stop_for_shutdown.assert_not_awaited()
    radar.stop_for_shutdown.assert_awaited_once()


@pytest.mark.asyncio
async def test_lifespan_survives_radar_resume_failure(radar):
    radar.resume_if_needed.side_effect = RuntimeError("DB rota")
    async with server.lifespan(server.app):
        pass  # el portal arranca igual
    radar.stop_for_shutdown.assert_awaited_once()


@pytest.mark.asyncio
async def test_app_lifespan_context_runs_radar_hooks(radar):
    """La app quedó construida con el lifespan del radar (FastAPI puede envolverlo)."""
    async with server.app.router.lifespan_context(server.app):
        radar.resume_if_needed.assert_awaited_once()
    radar.stop_for_shutdown.assert_awaited_once()


# ----------------------------------------------------------------- solo operación local (C1)

from core.radar_api import ESTADO_HOSTS_DE_TEST, SOLO_LOCAL  # noqa: E402


@pytest.fixture
def sin_hook_de_test(monkeypatch):
    """Como en producción: sin hosts extra aceptados como locales."""
    monkeypatch.setattr(server.app.state, ESTADO_HOSTS_DE_TEST, set(), raising=False)


def _cliente(ip="127.0.0.1", base="http://localhost:8000"):
    return TestClient(server.app, base_url=base, client=(ip, 51000))


def _assert_403(r):
    assert r.status_code == 403, r.text
    assert r.json()["detail"].startswith(SOLO_LOCAL)


def test_guard_rejects_non_loopback_client(radar, gmail, sin_hook_de_test):
    pid = make_post("LISTO")
    c = _cliente(ip="192.168.1.50")
    _assert_403(c.patch(f"/api/radar/posts/{pid}", json={"email_to": "atacante@evil.test"}))
    _assert_403(c.post(f"/api/radar/posts/{pid}/approve", json={"updated_at": radar_store.get_post(pid)["updated_at"]}))
    _assert_403(c.post("/api/radar/approve-all", json={"ids": [pid]}))
    _assert_403(c.post("/api/radar/start", json={}))
    post = radar_store.get_post(pid)
    assert post["email_to"] == "rrhh@empresa.test" and post["aprobado_at"] is None
    radar.enqueue_send.assert_not_awaited()
    radar.start.assert_not_awaited()
    # Las lecturas también (S1): devuelven datos del candidato; el celular las hace emparejado
    _assert_403(c.get("/api/radar/status"))
    _assert_403(c.get(f"/api/radar/posts/{pid}"))


@pytest.mark.parametrize("header", ["CF-Connecting-IP", "Cf-Ray"])
def test_guard_rejects_cloudflare_tunnel_requests(radar, gmail, sin_hook_de_test, header):
    pid = make_post("LISTO")
    valor = "200.1.2.3" if header == "CF-Connecting-IP" else "8a1b2c3d4e5f-EZE"
    r = _cliente().post(f"/api/radar/posts/{pid}/approve", json={"updated_at": radar_store.get_post(pid)["updated_at"]}, headers={header: valor})
    _assert_403(r)
    assert radar_store.get_post(pid)["aprobado_at"] is None


def test_guard_rejects_cross_site_fetch(radar, gmail, sin_hook_de_test):
    pid = make_post("LISTO")
    r = _cliente().post(f"/api/radar/posts/{pid}/approve", headers={"Sec-Fetch-Site": "cross-site"})
    _assert_403(r)
    assert radar_store.get_post(pid)["aprobado_at"] is None


@pytest.mark.parametrize("origin", ["http://evil.com", "http://localhost:9999", "null", "http://evil.com:8000"])
def test_guard_rejects_foreign_origin(radar, gmail, sin_hook_de_test, origin):
    pid = make_post("LISTO")
    r = _cliente().post(f"/api/radar/posts/{pid}/approve", json={"updated_at": radar_store.get_post(pid)["updated_at"]}, headers={"Origin": origin})
    _assert_403(r)
    assert radar_store.get_post(pid)["aprobado_at"] is None


def test_guard_rejects_dns_rebinding_host(radar, gmail, sin_hook_de_test):
    """Un dominio ajeno que resuelve a 127.0.0.1: mismo origen que su Host, pero Host no local."""
    pid = make_post("LISTO")
    c = _cliente(base="http://evil.example:8000")
    _assert_403(c.post(f"/api/radar/posts/{pid}/approve", json={"updated_at": radar_store.get_post(pid)["updated_at"]}, headers={"Origin": "http://evil.example:8000"}))


def test_guard_testclient_host_needs_explicit_test_hook(radar, sin_hook_de_test):
    """Sin el hook de tests, el cliente "testclient" de TestClient no cuenta como local."""
    _assert_403(TestClient(server.app).post("/api/radar/start", json={}))


@pytest.mark.parametrize(
    "ip, base, headers",
    [
        ("127.0.0.1", "http://localhost:8000", {"Origin": "http://localhost:8000", "Sec-Fetch-Site": "same-origin"}),
        ("127.0.0.1", "http://127.0.0.1:8000", {"Origin": "http://127.0.0.1:8000"}),
        ("::1", "http://localhost:8000", {}),
        ("127.0.0.1", "http://localhost", {"Origin": "http://localhost"}),
    ],
)
def test_guard_allows_same_origin_loopback(radar, gmail, sin_hook_de_test, ip, base, headers):
    pid = make_post("LISTO")
    r = _cliente(ip=ip, base=base).post(f"/api/radar/posts/{pid}/approve", json={"updated_at": radar_store.get_post(pid)["updated_at"]}, headers=headers)
    assert r.status_code == 200, r.text
    assert radar_store.get_post(pid)["aprobado_at"]
    radar.enqueue_send.assert_awaited_once_with(pid)


# --- Alcance geográfico y estado REVISAR ---

def _con_alcance(alcance, estado="DESCARTADO"):
    triage = {"es_busqueda_laboral": True, "puesto": "Analista", "empresa": "Acme",
              "match_rapido": 80, "motivo": "encaja", "alcance": alcance, "pais": "Argentina"}
    return make_post(estado=estado, triage=triage)


def test_list_filters_by_alcance(client):
    ar = _con_alcance("ARGENTINA", estado="LISTO")
    ext = _con_alcance("EXTERIOR_COMPATIBLE", estado="LISTO")
    _con_alcance("EXTERIOR")
    make_post(estado="NUEVO", triage={})  # sin triage: no entra en ningún filtro de alcance

    ids = lambda q: [p["id"] for p in client.get(f"/api/radar/posts{q}").json()]
    assert ids("?alcance=ARGENTINA") == [ar]
    assert ids("?alcance=exterior_compatible") == [ext]
    assert ids("?alcance=ARGENTINA&estado=LISTO") == [ar]
    assert ids("?alcance=ARGENTINA&estado=DESCARTADO") == []
    assert client.get("/api/radar/posts?alcance=MARTE").status_code == 422


def test_list_summary_includes_alcance_and_pais(client):
    pid = _con_alcance("ARGENTINA", estado="LISTO")
    (p,) = [x for x in client.get("/api/radar/posts").json() if x["id"] == pid]
    assert p["alcance"] == "ARGENTINA" and p["pais"] == "Argentina"


def test_revisar_is_a_valid_state_filter(client):
    pid = _con_alcance("DESCONOCIDO", estado="REVISAR")
    assert [p["id"] for p in client.get("/api/radar/posts?estado=REVISAR").json()] == [pid]


def test_force_and_discard_from_revisar(client, radar):
    a = _con_alcance("DESCONOCIDO", estado="REVISAR")
    b = _con_alcance("DESCONOCIDO", estado="REVISAR")
    r = client.post(f"/api/radar/posts/{a}/force", json={})
    assert r.status_code == 200
    radar.generate_cv.assert_awaited_once_with(a)
    r = client.post(f"/api/radar/posts/{b}/discard", json={}, headers={"X-LinkedOut-Origen": "portal"})
    assert r.status_code == 200 and r.json()["estado"] == "DESCARTADO"


# ----------------------------------------------------------------- feed y canales (Task 6)

def make_canal_post(canal="whatsapp", destino="https://wa.me/5491155550000", estado="LISTO", body="Hola, te escribo por la vacante.", **extra) -> int:
    campos = dict(canal=canal, destino_canal=destino, fuente="feed", email_subject="", email_body=body,
                  links=["https://empresa.com/jobs/1"], whatsapp=["5491155550000"])
    campos.update(extra)
    return make_post(estado, email_to=destino, **campos)


def test_list_filters_by_fuente_and_exposes_channel_fields(client):
    busq = make_post("LISTO")
    feed = make_canal_post("link", "https://empresa.com/jobs/1")
    ids = lambda r: [p["id"] for p in r.json()]  # noqa: E731
    assert ids(client.get("/api/radar/posts", params={"fuente": "feed"})) == [feed]
    assert ids(client.get("/api/radar/posts", params={"fuente": "busqueda"})) == [busq]
    assert set(ids(client.get("/api/radar/posts"))) == {busq, feed}
    assert client.get("/api/radar/posts", params={"fuente": "otra"}).status_code == 422
    # El filtro de fuente convive con estado y alcance
    assert ids(client.get("/api/radar/posts", params={"fuente": "feed", "estado": "LISTO"})) == [feed]
    assert ids(client.get("/api/radar/posts", params={"fuente": "feed", "estado": "ERROR"})) == []
    radar_store.update_post(feed, triage={"alcance": "ARGENTINA"})
    assert ids(client.get("/api/radar/posts", params={"fuente": "feed", "alcance": "ARGENTINA"})) == [feed]
    assert ids(client.get("/api/radar/posts", params={"fuente": "busqueda", "alcance": "ARGENTINA"})) == []

    resumen = client.get("/api/radar/posts", params={"fuente": "feed"}).json()[0]
    assert resumen["fuente"] == "feed"
    assert resumen["canal"] == "link"
    assert resumen["destino_canal"] == "https://empresa.com/jobs/1"
    assert resumen["links"] == ["https://empresa.com/jobs/1"]
    assert resumen["whatsapp"] == ["5491155550000"]
    detalle = client.get(f"/api/radar/posts/{feed}").json()
    assert detalle["canal"] == "link" and detalle["fuente"] == "feed"


def test_approve_non_email_channel_returns_409(client, radar, gmail):
    pid = make_canal_post("link", "https://empresa.com/jobs/1")
    r = client.post(f"/api/radar/posts/{pid}/approve", json={"updated_at": radar_store.get_post(pid)["updated_at"]})
    assert r.status_code == 409
    assert r.json()["detail"] == "Este post se postula por link: usá «Ya me postulé»"
    assert radar_store.get_post(pid)["aprobado_at"] is None
    radar.enqueue_send.assert_not_awaited()


def test_approve_all_reports_non_email_channel_as_skipped(client, radar, gmail):
    mail = make_post("LISTO")
    wa = make_canal_post("whatsapp")
    legacy = make_post("LISTO", email_to="otro@empresa.test", canal="")
    ids = [mail, wa, legacy]
    posts = {p["id"]: p for p in client.get("/api/radar/posts").json()}
    r = client.post("/api/radar/approve-all", json={"ids": ids, "updated_at": {i: posts[i]["updated_at"] for i in ids}})
    assert r.status_code == 200
    cuerpo = r.json()
    assert sorted(cuerpo["encolados"]) == sorted([mail, legacy])
    assert cuerpo["omitidos"] == [{"id": wa, "motivo": "Este post se postula por whatsapp: usá «Ya me postulé»"}]
    assert radar_store.get_post(wa)["aprobado_at"] is None


def test_marcar_postulado_sets_enviado_and_counts(client, radar, no_gmail):
    pid = make_canal_post("whatsapp")
    antes = radar_store.get_radar_state()["contadores_dia"]
    r = client.post(f"/api/radar/posts/{pid}/marcar-postulado")
    assert r.status_code == 200
    assert r.json()["estado"] == "ENVIADO"
    post = radar_store.get_post(pid)
    assert post["estado"] == "ENVIADO"
    assert post["message_id"] == "manual:whatsapp"
    assert post["aprobado_at"] is None
    # hora local, mismo formato que el worker de envíos
    assert abs((datetime.now() - datetime.strptime(post["sent_at"], "%Y-%m-%d %H:%M:%S")).total_seconds()) < 60
    contadores = radar_store.get_radar_state()["contadores_dia"]
    assert contadores["postulados"] == antes["postulados"] + 1
    assert contadores["enviados"] == antes["enviados"]  # no gasta el tope diario de Gmail
    radar.notify_post.assert_awaited_with(pid)
    radar.enqueue_send.assert_not_awaited()
    # No se puede marcar dos veces
    assert client.post(f"/api/radar/posts/{pid}/marcar-postulado").status_code == 409
    assert radar_store.get_radar_state()["contadores_dia"]["postulados"] == antes["postulados"] + 1


@pytest.mark.parametrize("canal,destino", [("link", "https://empresa.com/x"), ("dm", "https://www.linkedin.com/in/laura/"), ("otro", "")])
def test_marcar_postulado_other_channels(client, radar, canal, destino):
    pid = make_canal_post(canal, destino)
    assert client.post(f"/api/radar/posts/{pid}/marcar-postulado").status_code == 200
    assert radar_store.get_post(pid)["message_id"] == f"manual:{canal}"


def test_marcar_postulado_rejects_email_channel(client, radar):
    pid = make_post("LISTO")  # canal vacío (legado) = email
    explicito = make_post("LISTO", email_to="otro@empresa.test", canal="email")
    for p in (pid, explicito):
        assert client.post(f"/api/radar/posts/{p}/marcar-postulado").status_code == 409
        assert radar_store.get_post(p)["estado"] == "LISTO"


@pytest.mark.parametrize("estado", ["GENERANDO", "NUEVO", "REVISAR", "DESCARTADO", "ENVIANDO", "ENVIADO", "ANALIZANDO"])
def test_marcar_postulado_rejects_states_in_progress_or_closed(client, radar, estado):
    pid = make_canal_post("whatsapp", estado=estado)
    assert client.post(f"/api/radar/posts/{pid}/marcar-postulado").status_code == 409
    assert radar_store.get_post(pid)["estado"] == estado


@pytest.mark.parametrize("estado", ["ANALIZADA", "LISTO", "ERROR"])
def test_listo_marks_without_cv_or_message(client, radar, estado):
    """Botón Listo: el usuario se postuló por su cuenta, aunque no haya generado CV ni mensaje."""
    pid = make_canal_post("whatsapp", body="   ", estado=estado)
    r = client.post(f"/api/radar/posts/{pid}/marcar-postulado")
    assert r.status_code == 200
    assert radar_store.get_post(pid)["estado"] == "ENVIADO"


def test_listo_marks_email_post_without_recipient(client, radar):
    pid = make_post("ANALIZADA", email_to="", canal="email")
    r = client.post(f"/api/radar/posts/{pid}/marcar-postulado")
    assert r.status_code == 200
    post = radar_store.get_post(pid)
    assert post["estado"] == "ENVIADO" and post["message_id"] == "manual:email"


def test_marcar_postulado_unknown_post_404(client):
    assert client.post("/api/radar/posts/999999/marcar-postulado").status_code == 404


def test_marcar_postulado_survives_notification_failure(client, radar):
    radar.notify_post.side_effect = RuntimeError("sse caído")
    pid = make_canal_post("whatsapp")
    assert client.post(f"/api/radar/posts/{pid}/marcar-postulado").status_code == 200
    assert radar_store.get_post(pid)["estado"] == "ENVIADO"


def test_patch_non_email_channel_allows_empty_subject_and_locks_recipient(client, radar):
    pid = make_canal_post("whatsapp")
    r = client.patch(f"/api/radar/posts/{pid}", json={"email_subject": "", "email_body": "  Mensaje nuevo  "})
    assert r.status_code == 200
    post = radar_store.get_post(pid)
    assert post["email_subject"] == "" and post["email_body"] == "Mensaje nuevo"
    # Cambiar el destinatario no está permitido; reenviar el mismo valor es inocuo
    assert client.patch(f"/api/radar/posts/{pid}", json={"email_to": "otro@empresa.test"}).status_code == 409
    assert client.patch(f"/api/radar/posts/{pid}", json={"email_to": ""}).status_code == 409
    assert radar_store.get_post(pid)["email_to"] == "https://wa.me/5491155550000"
    assert client.patch(f"/api/radar/posts/{pid}", json={"email_to": "https://wa.me/5491155550000"}).status_code == 200


def test_guard_rejects_marcar_postulado_from_outside(radar, sin_hook_de_test):
    pid = make_canal_post("whatsapp")
    c = _cliente(ip="192.168.1.50")
    _assert_403(c.post(f"/api/radar/posts/{pid}/marcar-postulado"))
    assert radar_store.get_post(pid)["estado"] == "LISTO"
    c2 = _cliente()
    _assert_403(c2.post(f"/api/radar/posts/{pid}/marcar-postulado", headers={"CF-Connecting-IP": "200.1.2.3"}))
    _assert_403(c2.post(f"/api/radar/posts/{pid}/marcar-postulado", headers={"Origin": "http://evil.com"}))
    assert radar_store.get_post(pid)["estado"] == "LISTO"
    assert c2.post(f"/api/radar/posts/{pid}/marcar-postulado").status_code == 200


@pytest.mark.asyncio
async def test_radar_compose_uses_message_composer_for_every_channel(monkeypatch):
    async def fake_message(post, postulacion, profile=None, bridge=None):
        return {"to": post.get("destino_canal", ""), "subject": "", "body": "B", "is_fallback": False}

    monkeypatch.setattr(server, "compose_message", fake_message)
    pid = database.create_postulacion("Analista", "Acme", "texto", origen="linkedin", status="READY")
    database.update_postulacion(pid, cv_markdown="# CV")
    post = {"id": 1, "canal": "whatsapp", "destino_canal": "https://wa.me/549"}
    msg = await server._radar_compose(post, pid)
    assert msg["subject"] == "" and msg["to"] == "https://wa.me/549"


def test_marcar_postulado_does_not_count_when_post_changed_meanwhile(client, radar, monkeypatch):
    """Si entre la validación y el update otra ruta lo sacó de LISTO, no se pisa ni se cuenta."""
    pid = make_canal_post("whatsapp")
    real = radar_store.mark_applied

    def carrera(*args, **kwargs):
        radar_store.update_post(pid, estado="DESCARTADO")
        return real(*args, **kwargs)

    monkeypatch.setattr(radar_store, "mark_applied", carrera)
    r = client.post(f"/api/radar/posts/{pid}/marcar-postulado")
    assert r.status_code == 409
    assert radar_store.get_post(pid)["estado"] == "DESCARTADO"
    assert radar_store.get_radar_state()["contadores_dia"]["postulados"] == 0


# ----------------------------------------------------------------- celular emparejado

from core import acceso_remoto  # noqa: E402


@pytest.fixture
def clave_celular(tmp_path, monkeypatch):
    monkeypatch.setattr(acceso_remoto, "ARCHIVO_CLAVE", tmp_path / "acceso_celular.key")
    monkeypatch.setattr(acceso_remoto, "ip_lan", lambda: "192.168.1.50")
    return acceso_remoto.clave()


def _celular(base="http://192.168.1.50:1111"):
    return _cliente(ip="192.168.1.51", base=base)


def test_paired_phone_can_operate_from_lan(radar, gmail, sin_hook_de_test, clave_celular):
    pid = make_post("LISTO")
    c = _celular()
    r = c.get(f"/api/radar/emparejar?clave={clave_celular}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert c.post("/api/radar/start", json={}).status_code == 200
    r = c.post(f"/api/radar/posts/{pid}/approve", json={"updated_at": radar_store.get_post(pid)["updated_at"]}, headers={"Origin": "http://192.168.1.50:1111"})
    assert r.status_code == 200, r.text
    radar.enqueue_send.assert_awaited_once()


def test_paired_phone_through_tunnel_is_accepted(radar, gmail, sin_hook_de_test, clave_celular):
    c = _cliente(base="https://algo.trycloudflare.com")
    c.cookies.set(acceso_remoto.COOKIE, clave_celular)
    r = c.post("/api/radar/start", json={}, headers={
        "CF-Connecting-IP": "200.1.2.3", "Origin": "https://algo.trycloudflare.com"})
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("clave", ["", "otra-clave"])
def test_wrong_pairing_key_is_rejected(radar, sin_hook_de_test, clave_celular, clave):
    c = _celular()
    r = c.get(f"/api/radar/emparejar?clave={clave}", follow_redirects=False)
    assert r.status_code == 403 and acceso_remoto.COOKIE not in r.headers.get("set-cookie", "")
    c.cookies.set(acceso_remoto.COOKIE, clave or "x")
    _assert_403(c.post("/api/radar/start", json={}))


def test_paired_phone_still_blocks_cross_site_and_foreign_origin(radar, gmail, sin_hook_de_test, clave_celular):
    pid = make_post("LISTO")
    c = _celular()
    c.cookies.set(acceso_remoto.COOKIE, clave_celular)
    _assert_403(c.post(f"/api/radar/posts/{pid}/approve", json={"updated_at": radar_store.get_post(pid)["updated_at"]}, headers={"Sec-Fetch-Site": "cross-site"}))
    _assert_403(c.post(f"/api/radar/posts/{pid}/approve", json={"updated_at": radar_store.get_post(pid)["updated_at"]}, headers={"Origin": "http://evil.com"}))
    assert radar_store.get_post(pid)["aprobado_at"] is None


def test_pairing_qr_only_from_the_pc_and_regenerate_unpairs(radar, sin_hook_de_test, clave_celular):
    r = _cliente().get("/api/radar/acceso-celular")
    assert r.status_code == 200
    assert r.json()["url"] == f"http://192.168.1.50:8000/api/radar/emparejar?clave={clave_celular}"
    assert r.json()["qr_svg"].lstrip().startswith("<")
    c = _celular()
    c.cookies.set(acceso_remoto.COOKIE, clave_celular)
    assert c.get("/api/radar/acceso-celular").status_code == 403
    assert c.post("/api/radar/acceso-celular/regenerar", json={}).status_code == 403
    assert _cliente().post("/api/radar/acceso-celular/regenerar", json={}).status_code == 200
    _assert_403(c.post("/api/radar/start", json={}))


def test_no_key_file_means_nobody_is_paired(tmp_path, monkeypatch):
    monkeypatch.setattr(acceso_remoto, "ARCHIVO_CLAVE", tmp_path / "no.key")
    assert acceso_remoto.es_valida("") is False and acceso_remoto.es_valida("x") is False
    assert not (tmp_path / "no.key").exists()


# ----------------------------------------------------------------- requisitos con un clic + aprendizaje

import core.aprendizajes as aprendizajes_mod  # noqa: E402


@pytest.fixture
def aprendizajes_aislados(tmp_path, monkeypatch):
    monkeypatch.setattr(aprendizajes_mod, "APRENDIZAJES_FILE", tmp_path / "aprendizajes.json")


def _analizada(reqs, score=40.0):
    return make_post("ANALIZADA", analisis={"match_score": score, "requisitos": reqs})


def test_click_cycles_requirement_recomputes_score_and_learns(client, radar, aprendizajes_aislados):
    pid = _analizada([
        {"nombre": "Docker", "estado": "BRECHA_REAL", "approved": False, "tipo": "REQUISITO_EXCLUYENTE"},
        {"nombre": "SQL", "estado": "MATCH_DIRECTO", "approved": True, "tipo": "REQUISITO_EXCLUYENTE"},
    ])
    r = client.post(f"/api/radar/posts/{pid}/requisito", json={"nombre": "docker"})
    assert r.status_code == 200, r.text
    req = radar_store.get_post(pid)["analisis"]["requisitos"][0]
    assert req["estado"] == "TRANSFERIBLE" and req["approved"] is True
    assert aprendizajes_mod.obtener_aprendizajes()["docker"]["estado"] == "TRANSFERIBLE"
    client.post(f"/api/radar/posts/{pid}/requisito", json={"nombre": "Docker"})
    analisis = radar_store.get_post(pid)["analisis"]
    assert analisis["requisitos"][0]["estado"] == "MATCH_DIRECTO" and analisis["match_score"] > 40
    radar.generar_automaticos.assert_awaited_with([pid])
    # Tercer clic: vuelve a brecha y se conserva
    client.post(f"/api/radar/posts/{pid}/requisito", json={"nombre": "Docker"})
    assert radar_store.get_post(pid)["analisis"]["requisitos"][0]["estado"] == "BRECHA_REAL"
    assert aprendizajes_mod.obtener_aprendizajes()["docker"]["estado"] == "BRECHA_REAL"


def test_click_on_unknown_requirement_or_busy_post(client, radar, aprendizajes_aislados):
    pid = _analizada([{"nombre": "SQL", "estado": "MATCH_DIRECTO"}])
    assert client.post(f"/api/radar/posts/{pid}/requisito", json={"nombre": "Kafka"}).status_code == 404
    ocupado = make_post("GENERANDO", analisis={"requisitos": [{"nombre": "SQL", "estado": "BRECHA_REAL"}]})
    assert client.post(f"/api/radar/posts/{ocupado}/requisito", json={"nombre": "SQL"}).status_code == 409


def test_click_also_updates_the_linked_postulacion(client, radar, aprendizajes_aislados):
    from core.database import create_postulacion, get_postulacion, update_postulacion
    postulacion = create_postulacion(titulo="Analista", empresa="Acme", contenido="x", url="", origen="linkedin")
    update_postulacion(postulacion, requisitos=[{"nombre": "Docker", "estado": "BRECHA_REAL", "approved": False}])
    pid = make_post("LISTO", postulacion_id=postulacion,
                    analisis={"requisitos": [{"nombre": "Docker", "estado": "BRECHA_REAL", "approved": False}]})
    assert client.post(f"/api/radar/posts/{pid}/requisito", json={"nombre": "Docker"}).status_code == 200
    req = get_postulacion(postulacion)["requisitos"][0]
    assert req["estado"] == "TRANSFERIBLE" and req["approved"] is True


def test_learned_requirement_is_not_a_gap_in_the_next_offer(aprendizajes_aislados):
    aprendizajes_mod.registrar_aprendizaje("Gestión de proyectos", "MATCH_DIRECTO")
    reqs = [{"nombre": "gestion de  PROYECTOS", "estado": "BRECHA_REAL", "approved": False},
            {"nombre": "Kafka", "estado": "BRECHA_REAL", "approved": False}]
    assert aprendizajes_mod.aplicar_aprendizajes_a_requisitos(reqs) == 1
    assert reqs[0]["estado"] == "MATCH_DIRECTO" and reqs[0]["approved"] is True
    assert reqs[1]["estado"] == "BRECHA_REAL"
    assert "Gestión de proyectos: MATCH_DIRECTO" in aprendizajes_mod.aprendizajes_para_prompt()


@pytest.mark.asyncio
async def test_radar_pipeline_rejects_the_deterministic_cv_and_retries_it(monkeypatch):
    """Si el Redactor se corta queda el CV determinístico (en español, genérico): no se ofrece como
    listo y el reintento vuelve a correr el pipeline sobre la misma postulación."""
    llamadas = []

    async def fake_pipeline(pid, url=None):
        llamadas.append(pid)
        fallback = len(llamadas) == 1
        database.update_postulacion(pid, status="READY", telemetry={
            "is_fallback": fallback, "fallback_reason": "excedió el tiempo límite de 600s." if fallback else None})

    monkeypatch.setattr(server, "run_pipeline_task", fake_pipeline)
    post = _linkedin_post()
    with pytest.raises(RuntimeError, match="no generó el CV.*600s"):
        await server._radar_pipeline(post)
    assert radar_store.get_post(post["id"])["postulacion_id"] is None
    pid = await server._radar_pipeline(radar_store.get_post(post["id"]))
    assert llamadas == [pid, pid]
    assert database.get_postulacion(pid)["status"] == "READY"


def test_approve_all_duplicado_con_previo_enviado_trae_tipo_email_y_previo(client, radar, gmail):
    previo_id = mark_sent("viejo@empresa.test", days_ago=3)
    radar_store.update_post(previo_id, triage={"puesto": "Contador", "empresa": "Cyr"})
    dup = make_post("LISTO", email_to="Viejo@empresa.test")
    r = client.post("/api/radar/approve-all", json=_lo_que_ve_el_usuario(client))
    assert r.status_code == 200
    (o,) = r.json()["omitidos"]
    assert o["id"] == dup and o["tipo"] == "duplicado" and o["email"] == "Viejo@empresa.test"
    assert o["motivo"].startswith("duplicado:")
    previo = o["previo"]
    assert previo["post_id"] == previo_id and previo["puesto"] == "Contador" and previo["empresa"] == "Cyr"
    assert previo["estado"] == "ENVIADO" and previo["sent_at"]


def test_approve_all_duplicado_con_previo_en_cola_tiene_sent_at_nulo(client, radar, gmail):
    uno = make_post("LISTO", email_to="dos@empresa.test")
    repetido = make_post("LISTO", email_to="dos@empresa.test")
    r = client.post("/api/radar/approve-all", json=_lo_que_ve_el_usuario(client))
    data = r.json()
    assert data["encolados"] == [uno]
    (o,) = data["omitidos"]
    assert o["id"] == repetido and o["tipo"] == "duplicado"
    assert o["previo"]["post_id"] == uno and o["previo"]["sent_at"] is None
    assert o["previo"]["estado"] in ("LISTO", "ENVIANDO")


def test_approve_all_otros_omitidos_no_llevan_tipo_ni_previo(client, radar, gmail):
    sin_cuerpo = make_post("LISTO", email_to="tres@empresa.test", body="")
    wa = make_canal_post("whatsapp")
    ids = [sin_cuerpo, wa]
    posts = {p["id"]: p for p in client.get("/api/radar/posts").json()}
    r = client.post("/api/radar/approve-all", json={"ids": ids, "updated_at": {i: posts[i]["updated_at"] for i in ids}})
    omitidos = r.json()["omitidos"]
    assert len(omitidos) == 2
    for o in omitidos:
        assert set(o) == {"id", "motivo"}


def test_approve_con_updated_at_viejo_devuelve_409_sin_aprobar_ni_encolar(client, radar, gmail):
    pid = make_post("LISTO", email_to="uno@empresa.test")
    visto = radar_store.get_post(pid)["updated_at"]
    radar_store.update_post(pid, email_body="Lo editaron después de revisarlo")
    r = client.post(f"/api/radar/posts/{pid}/approve", json={"updated_at": visto})
    assert r.status_code == 409
    assert r.json()["detail"] == "Cambió desde que lo revisaste: abrilo y aprobalo de nuevo"
    assert radar_store.get_post(pid)["aprobado_at"] is None
    radar.enqueue_send.assert_not_awaited()


def test_approve_con_updated_at_igual_aprueba(client, radar, gmail):
    uno = make_post("LISTO", email_to="uno@empresa.test")
    r = client.post(f"/api/radar/posts/{uno}/approve", json={"updated_at": radar_store.get_post(uno)["updated_at"]})
    assert r.status_code == 200
    assert radar_store.get_post(uno)["aprobado_at"]


@pytest.mark.parametrize("payload", [None, {}, {"updated_at": None}, {"updated_at": ""}, {"confirm_duplicate": True}])
def test_approve_exige_version_revisada(client, radar, gmail, payload):
    pid = make_post("LISTO")
    visto = radar_store.get_post(pid)["updated_at"]
    r = client.post(f"/api/radar/posts/{pid}/approve", json=payload)
    assert r.status_code == 400
    post = radar_store.get_post(pid)
    assert post["aprobado_at"] is None and post["updated_at"] == visto
    radar.enqueue_send.assert_not_awaited()


def test_approve_duplicado_confirmado_con_updated_at_viejo_tambien_da_409(client, radar, gmail):
    mark_sent("viejo@empresa.test", days_ago=2)
    pid = make_post("LISTO", email_to="viejo@empresa.test")
    visto = radar_store.get_post(pid)["updated_at"]
    radar_store.update_post(pid, email_subject="Asunto nuevo")
    r = client.post(f"/api/radar/posts/{pid}/approve", json={"confirm_duplicate": True, "updated_at": visto})
    assert r.status_code == 409 and "Cambió" in r.json()["detail"]
    assert radar_store.get_post(pid)["aprobado_at"] is None


# Revisión final: la aprobación incluye el CV que se revisó, no solo el mensaje.
def _post_con_cv_aprobado():
    pid = database.create_postulacion("Analista", "Acme", "Oferta", status="READY")
    database.update_postulacion(pid, cv_markdown="# CV revisado")
    post_id = make_post("LISTO", postulacion_id=pid, aprobado_at="aprobado",
                        updated_at="2000-01-01 00:00:00.000")
    return pid, post_id


@pytest.mark.parametrize("ruta,tarea", [
    ("recalculate-cv", "recalculate_cv_task"),
    ("regenerate", "run_pipeline_task"),
    ("remediar", "remediate_cv_task"),
])
def test_editor_invalida_aprobacion_antes_de_regenerar(client, monkeypatch, ruta, tarea):
    pid, post_id = _post_con_cv_aprobado()
    al_empezar = []

    async def generar(postulacion_id):
        al_empezar.append(radar_store.get_post(post_id))
        database.update_postulacion(postulacion_id, status="READY", cv_markdown="# CV nuevo")

    monkeypatch.setattr(server, tarea, generar)
    assert client.post(f"/api/ofertas/{pid}/{ruta}").status_code == 200
    assert al_empezar[0]["aprobado_at"] is None
    assert al_empezar[0]["updated_at"] != "2000-01-01 00:00:00.000"
    assert radar_store.get_post(post_id)["aprobado_at"] is None


@pytest.mark.parametrize("cambio", [{"cv_markdown": "# CV editado"}, {"status": "PROCESSING"}])
def test_editar_cv_invalida_aprobacion_en_la_misma_escritura(cambio):
    pid, post_id = _post_con_cv_aprobado()
    database.update_postulacion(pid, **cambio)
    post = radar_store.get_post(post_id)
    assert post["aprobado_at"] is None
    assert post["updated_at"] != "2000-01-01 00:00:00.000"


def test_editar_notas_o_guardar_el_mismo_cv_conserva_aprobacion():
    pid, post_id = _post_con_cv_aprobado()
    database.update_postulacion(pid, notas_seguimiento="Llamar mañana", cv_markdown="# CV revisado")
    assert radar_store.get_post(post_id)["aprobado_at"] == "aprobado"


def test_no_aprueba_cv_en_regeneracion(client, radar, gmail):
    pid, post_id = _post_con_cv_aprobado()
    database.update_postulacion(pid, status="PROCESSING")
    post = radar_store.get_post(post_id)
    r = client.post(f"/api/radar/posts/{post_id}/approve", json={"updated_at": post["updated_at"]})
    assert r.status_code == 409
    radar.enqueue_send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("momento", ["pendiente", "compilando", "reintento"])
async def test_cv_cambiado_no_se_envia(monkeypatch, tmp_path, momento):
    from test_radar_workers import make_radar

    pid, post_id = _post_con_cv_aprobado()
    monkeypatch.setattr(server, "load_gmail_config", lambda: GmailConfig("yo@correo-ficticio.test", "app-pass"))
    monkeypatch.setattr(server, "load_candidate_profile", lambda: {"nombre": "Candidato"})
    enviados = []

    def compilar(*args, **kwargs):
        if momento == "compilando":
            database.update_postulacion(pid, cv_markdown="# CV nuevo")
        return tmp_path / "cv.pdf"

    def enviar(**kwargs):
        enviados.append(kwargs)
        if momento == "reintento" and len(enviados) == 1:
            raise MailerTemporaryError("conexión rechazada antes de entregar")
        return "<enviado>"

    async def esperar_reintento(segundos, evento):
        database.update_postulacion(pid, cv_markdown="# CV nuevo")
        return True

    monkeypatch.setattr(server, "compile_markdown_to_pdf", compilar)
    monkeypatch.setattr(server, "send_application_email", enviar)
    radar = make_radar(tmp_path, send=server._radar_send)
    monkeypatch.setattr(radar, "_wait", esperar_reintento)
    if momento == "pendiente":
        database.update_postulacion(pid, cv_markdown="# CV nuevo")
    assert await radar._procesar_envio(post_id) is False
    assert len(enviados) == (1 if momento == "reintento" else 0)
    post = radar_store.get_post(post_id)
    assert post["estado"] == "LISTO" and post["aprobado_at"] is None
    assert radar_store.get_radar_state()["contadores_dia"]["enviados"] == 0


@pytest.mark.parametrize("cambio_cv", [False, True])
def test_guardado_con_version_vieja_no_incorpora_contenido_sin_revisar(client, cambio_cv):
    pid, post_id = _post_con_cv_aprobado()
    radar_store.update_post(post_id, aprobado_at=None)
    visto = radar_store.get_post(post_id)["updated_at"]
    if cambio_cv:
        database.update_postulacion(pid, cv_markdown="# CV concurrente")
    else:
        radar_store.update_post(post_id, email_body="Mensaje remoto no revisado")
    r = client.patch(f"/api/radar/posts/{post_id}", json={"email_subject": "Asunto propio", "updated_at": visto})
    assert r.status_code == 409
    assert radar_store.get_post(post_id)["email_subject"] == "Postulación – Analista Funcional"


def test_guardado_devuelve_solo_la_version_que_guardo(client, radar):
    post_id = make_post("LISTO", body="Mensaje revisado")
    visto = radar_store.get_post(post_id)["updated_at"]

    async def editar_otra_pestana(*args):
        radar_store.update_post(post_id, email_body="Cambio posterior al guardado")

    radar.notify_post.side_effect = editar_otra_pestana
    r = client.patch(f"/api/radar/posts/{post_id}", json={"email_subject": "Asunto propio", "updated_at": visto})
    assert r.status_code == 200
    assert r.json()["email_body"] == "Mensaje revisado"
    assert radar_store.get_post(post_id)["email_body"] == "Cambio posterior al guardado"


def test_aprobar_revalida_version_al_escribir_aprobacion(client, radar, monkeypatch):
    post_id = make_post("LISTO", updated_at="2000-01-01 00:00:00.000")
    visto = radar_store.get_post(post_id)["updated_at"]

    def config_con_edicion_concurrente():
        radar_store.update_post(post_id, email_body="Edición posterior a la validación")
        return GmailConfig("yo@correo-ficticio.test", "app-pass")

    monkeypatch.setattr("core.radar_api.load_gmail_config", config_con_edicion_concurrente)
    r = client.post(f"/api/radar/posts/{post_id}/approve", json={"updated_at": visto})
    assert r.status_code == 409
    assert radar_store.get_post(post_id)["aprobado_at"] is None
    radar.enqueue_send.assert_not_awaited()


@pytest.mark.parametrize("formato", ["una_pagina", "completo"])
def test_regenerate_con_cv_guarda_el_formato_pedido(client, formato):
    """el CV completo es el de siempre; la versión de 1 página se pide al rehacer el CV."""
    listo = make_post("LISTO", postulacion_id=None)
    p_id = database.create_postulacion("Analista", "Acme", "texto", origen="linkedin", status="READY")
    database.update_postulacion(p_id, formato_cv="completo" if formato == "una_pagina" else "una_pagina")
    radar_store.update_post(listo, postulacion_id=p_id)
    r = client.post(f"/api/radar/posts/{listo}/regenerate", json={"cv": True, "formato": formato})
    assert r.status_code == 200
    assert database.get_postulacion(p_id)["formato_cv"] == formato


def test_regenerate_rechaza_formato_invalido_sin_tocar_nada(client):
    listo = make_post("LISTO", postulacion_id=None)
    p_id = database.create_postulacion("Analista", "Acme", "texto", origen="linkedin", status="READY")
    radar_store.update_post(listo, postulacion_id=p_id)
    r = client.post(f"/api/radar/posts/{listo}/regenerate", json={"cv": True, "formato": "dos_paginas"})
    assert r.status_code == 422
    assert radar_store.get_post(listo)["estado"] == "LISTO"
    assert database.get_postulacion(p_id)["status"] == "READY"


# ----------------------------------------------------------------- descartar solo desde el portal

def test_descartar_sin_la_cabecera_del_portal_se_rechaza_con_ticket(client, monkeypatch):
    # 107 ofertas aparecieron descartadas «a mano» sin que el usuario las descartara (9 en el mismo segundo)
    from core import tickets
    abiertos = []
    monkeypatch.setattr(tickets, "reportar_error", lambda *a, **kw: abiertos.append((a, kw)) or 1)
    pid = make_post("LISTO")
    r = client.post(f"/api/radar/posts/{pid}/discard")
    assert r.status_code == 403
    assert radar_store.get_post(pid)["estado"] == "LISTO"
    assert any(a[1] == "descarte_sin_origen" for a, _ in abiertos)


def test_descartar_desde_el_portal_guarda_desde_donde_y_cuando(client):
    pid = make_post("LISTO")
    r = client.post(f"/api/radar/posts/{pid}/discard", json={"desde": "duplicado"},
                    headers={"X-LinkedOut-Origen": "portal"})
    assert r.status_code == 200
    triage = radar_store.get_post(pid)["triage"]
    assert triage["descartado_desde"] == "duplicado" and triage["descartado_at"]
    assert client.post(f"/api/radar/posts/{pid}/restaurar").status_code == 200
    assert "descartado_desde" not in radar_store.get_post(pid)["triage"]


@pytest.mark.parametrize("triage,corto,largo", [
    ({"motivo_descarte": "manual", "descartado_at": "2026-11-13 12:31:57"}, "A mano · 13/11", "la descartaste a mano el 13/11"),
    ({"motivo_descarte": "prefiltro", "alcance_motivo": "Descartado sin IA: ubicación Madrid"}, "Exterior: Madrid",
     "es un puesto en el exterior (Madrid)"),
    ({"alcance": "EXTERIOR", "pais": "Chile"}, "Exterior: Chile",
     "es un puesto en el exterior que no acepta candidatos de Argentina (Chile)"),
    ({"motivo": "El clasificador no lo vio como oferta"}, "No parece oferta", "el clasificador no la vio como oferta"),
])
def test_la_tarjeta_descartada_dice_por_que(triage, corto, largo):
    from core.radar_api import motivo_descarte
    assert motivo_descarte({"estado": "DESCARTADO", "triage": triage}) == {"corto": corto, "largo": largo}
    assert motivo_descarte({"estado": "LISTO", "triage": triage}) is None
