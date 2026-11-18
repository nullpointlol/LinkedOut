"""Contrato del lote: selección, formatos y aprobación con SQLite temporal.

El TestClient no ejecuta lifespan; RadarLoop y la frontera de aprobación son
dobles. INVARIANT_CHECK retorna antes de invocar la ruta nueva, sin skips.
"""
import os
from datetime import datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import server
from core import database, radar_api, radar_store
from core.radar import RadarLoop


LOTE = "/api/radar/regenerar-listos"


@pytest.fixture
def radar(monkeypatch):
    stub = AsyncMock(spec=RadarLoop)
    monkeypatch.setattr(server, "radar_loop", stub)
    return stub


@pytest.fixture
def client(radar):
    return TestClient(server.app)


@pytest.fixture(autouse=True)
def no_aprobacion(monkeypatch):
    aprobar = AsyncMock(spec=radar_api._aprobar)
    monkeypatch.setattr(radar_api, "_aprobar", aprobar)
    yield
    aprobar.assert_not_awaited()


def crear_post(estado="LISTO", fuente="busqueda", **extra):
    urn = f"urn:li:activity:perfil-{uuid4().hex}"
    post_id = radar_store.insert_post_if_new(
        {"urn": urn, "url": f"https://www.linkedin.com/feed/update/{urn}/",
         "autor": "Empresa de prueba", "cargo_autor": "Selección",
         "texto": "Buscamos analista funcional. CV a rrhh@example.com",
         "fecha_rel": "1 h", "emails": ["rrhh@example.com"]},
        query="analista funcional", fuente=fuente,
    )
    assert post_id is not None
    campos = {
        "estado": estado,
        "triage": {"es_busqueda_laboral": True, "puesto": "Analista funcional",
                   "empresa": "Empresa de prueba", "match_rapido": 80, "motivo": "Encaja"},
        "email_to": "rrhh@example.com", "email_subject": "Mi postulación",
        "email_body": "Hola, adjunto mi CV.",
    }
    campos.update(extra)
    assert radar_store.update_post(post_id, **campos)
    return post_id


def vincular_cv(post_id, formato="completo"):
    postulacion_id = database.create_postulacion(
        "Analista funcional", "Empresa de prueba", "Oferta sintética",
        status="READY", origen="linkedin",
    )
    database.update_postulacion(
        postulacion_id, formato_cv=formato, cv_markdown="# CV anterior",
    )
    assert radar_store.update_post(post_id, postulacion_id=postulacion_id)
    return postulacion_id


def snapshot(ids):
    """Todas las columnas del post y de su postulación, no solo estado/aprobación."""
    filas = {}
    for post_id in ids:
        post = radar_store.get_post(post_id)
        pid = post["postulacion_id"]
        filas[post_id] = (post, database.get_postulacion(pid) if pid else None)
    return filas


def verificar_get(client, ids, manuales=()):
    # invariante: GET devuelve lista, conserva el detalle y excluye manual por defecto.
    respuesta = client.get("/api/radar/posts")
    assert respuesta.status_code == 200
    assert isinstance(respuesta.json(), list)
    assert {p["id"] for p in respuesta.json()} == set(ids) - set(manuales)
    for post_id in ids:
        detalle = client.get(f"/api/radar/posts/{post_id}")
        assert detalle.status_code == 200
        post, postulacion = snapshot([post_id])[post_id]
        assert detalle.json() == {**post, "postulacion": postulacion}
    if manuales:
        manual = client.get("/api/radar/posts", params={"fuente": "manual"})
        assert manual.status_code == 200
        assert isinstance(manual.json(), list)
        assert {p["id"] for p in manual.json()} == set(manuales)


@pytest.mark.parametrize("estado", ["LISTO", "ERROR"])
@pytest.mark.parametrize("cv", [False, True])
@pytest.mark.parametrize("formato", ["completo", "una_pagina"])
def test_invariante_regenerate_individual_y_get(client, radar, estado, cv, formato):
    # invariante: regenerate individual admite LISTO/ERROR incluso con aprobación.
    aprobado = (datetime.now() - timedelta(hours=1)).isoformat(sep=" ")
    post_id = crear_post(estado, aprobado_at=aprobado, intentos=2, error="Fallo anterior")
    pid = vincular_cv(post_id, formato)
    manual = crear_post(fuente="manual")
    antes = snapshot([manual])
    verificar_get(client, [post_id, manual], manuales=[manual])

    respuesta = client.post(f"/api/radar/posts/{post_id}/regenerate", json={"cv": cv})
    assert respuesta.status_code == 200
    post = radar_store.get_post(post_id)
    assert post["estado"] == "GENERANDO"
    assert post["aprobado_at"] is None
    assert post["email_subject"] is None and post["email_body"] is None
    assert post["email_to"] == "rrhh@example.com"
    assert post["intentos"] == 0 and post["error"] is None
    assert post["postulacion_id"] == (None if cv else pid)
    postulacion = database.get_postulacion(pid)
    assert postulacion["status"] == ("PENDING" if cv else "READY")
    assert postulacion["formato_cv"] == formato
    assert respuesta.json() == {**post, "postulacion": None if cv else postulacion}
    assert snapshot([manual]) == antes
    radar.notify_post.assert_awaited_once_with(post_id)
    radar.wake_generation.assert_awaited_once()
    radar.enqueue_send.assert_not_awaited()
    verificar_get(client, [post_id, manual], manuales=[manual])


def test_solo_listos_no_aprobados_con_triage(client, radar):
    listo = crear_post()
    pid = vincular_cv(listo)
    excluidos = [
        crear_post(aprobado_at=(datetime.now() - timedelta(hours=1)).isoformat(sep=" ")),
        crear_post("ERROR", error="Falló la generación"),
        crear_post("ENVIADO", sent_at=datetime.now().isoformat(sep=" "), message_id="<prueba>"),
        crear_post("GENERANDO"), crear_post(triage=None), crear_post(triage={}),
    ]
    for post_id in excluidos:
        vincular_cv(post_id, "una_pagina")
    antes = snapshot(excluidos)
    # invariante: cada excluido y su CV son consultables con el contrato actual.
    verificar_get(client, [listo, *excluidos])
    if os.environ.get("INVARIANT_CHECK") == "1":
        return

    respuesta = client.post(LOTE)
    assert respuesta.status_code == 200
    assert respuesta.json() == {"regenerados": 1, "ids": [listo]}
    post = radar_store.get_post(listo)
    assert post["estado"] == "GENERANDO"
    assert post["aprobado_at"] is None
    assert post["email_subject"] is None and post["email_body"] is None
    assert post["postulacion_id"] is None
    assert database.get_postulacion(pid)["status"] == "PENDING"
    assert snapshot(excluidos) == antes
    radar.notify_post.assert_awaited_once_with(listo)
    radar.wake_generation.assert_awaited_once()
    radar.enqueue_send.assert_not_awaited()

    # Una segunda ejecución no reclama de nuevo el post que ya está GENERANDO.
    despues = snapshot([listo, *excluidos])
    cv_despues = database.get_postulacion(pid)
    radar.reset_mock()
    repetida = client.post(LOTE)
    assert repetida.status_code == 200
    assert repetida.json() == {"regenerados": 0, "ids": []}
    assert snapshot([listo, *excluidos]) == despues
    assert database.get_postulacion(pid) == cv_despues
    radar.notify_post.assert_not_awaited()
    radar.wake_generation.assert_not_awaited()
    radar.enqueue_send.assert_not_awaited()


@pytest.mark.parametrize("formato", ["completo", "una_pagina"])
def test_preserva_formato_y_marca_pending(client, radar, formato):
    post_id = crear_post(intentos=3, error="Fallo anterior")
    pid = vincular_cv(post_id, formato)
    original = database.get_postulacion(pid)
    # invariante: CV READY ligado, con el formato elegido, visible en el detalle.
    verificar_get(client, [post_id])
    assert original["status"] == "READY" and original["formato_cv"] == formato
    if os.environ.get("INVARIANT_CHECK") == "1":
        return

    respuesta = client.post(LOTE)
    assert respuesta.status_code == 200
    assert respuesta.json() == {"regenerados": 1, "ids": [post_id]}
    assert database.get_postulacion(pid) == {**original, "status": "PENDING"}
    post = radar_store.get_post(post_id)
    assert post["estado"] == "GENERANDO" and post["postulacion_id"] is None
    assert post["aprobado_at"] is None
    assert post["email_subject"] is None and post["email_body"] is None
    assert post["intentos"] == 0 and post["error"] is None
    radar.notify_post.assert_awaited_once_with(post_id)
    radar.wake_generation.assert_awaited_once()
    radar.enqueue_send.assert_not_awaited()


def test_lote_vacio(client, radar):
    # invariante: base temporal vacía y GET lista vacía.
    verificar_get(client, [])
    if os.environ.get("INVARIANT_CHECK") == "1":
        return

    respuesta = client.post(LOTE)
    assert respuesta.status_code == 200
    assert respuesta.json() == {"regenerados": 0, "ids": []}
    verificar_get(client, [])
    radar.notify_post.assert_not_awaited()
    radar.wake_generation.assert_not_awaited()
    radar.enqueue_send.assert_not_awaited()


def test_incluye_manual_y_no_envia(client, radar):
    busqueda = crear_post()
    manual = crear_post(fuente="manual")
    feed = crear_post(fuente="feed")
    pids = [vincular_cv(p) for p in (busqueda, manual, feed)]
    # invariante: el GET general oculta manual, pero su GET explícito la devuelve.
    verificar_get(client, [busqueda, manual, feed], manuales=[manual])
    if os.environ.get("INVARIANT_CHECK") == "1":
        return

    respuesta = client.post(LOTE)
    assert respuesta.status_code == 200
    resultado = respuesta.json()
    assert set(resultado) == {"regenerados", "ids"}
    assert resultado["regenerados"] == 3
    assert isinstance(resultado["ids"], list)
    assert sorted(resultado["ids"]) == sorted([busqueda, manual, feed])
    for post_id in (busqueda, manual, feed):
        post = radar_store.get_post(post_id)
        assert post["estado"] == "GENERANDO" and post["aprobado_at"] is None
        assert post["email_subject"] is None and post["email_body"] is None
        assert post["postulacion_id"] is None
    assert [database.get_postulacion(pid)["status"] for pid in pids] == ["PENDING"] * 3
    assert sorted(c.args[0] for c in radar.notify_post.await_args_list) == sorted([busqueda, manual, feed])
    assert radar.wake_generation.await_count >= 1
    radar.enqueue_send.assert_not_awaited()


def test_aprobacion_sobrevenida_no_se_pierde(client, radar, monkeypatch):
    post_id = crear_post()
    pid = vincular_cv(post_id, "una_pagina")
    version = (datetime.now() - timedelta(hours=1)).isoformat(sep=" ", timespec="microseconds")
    assert radar_store.update_post(post_id, updated_at=version)
    cv_original = database.get_postulacion(pid)
    # invariante: versión leída de una fila LISTO sin aprobación y CV READY.
    verificar_get(client, [post_id])
    assert radar_store.get_post(post_id)["aprobado_at"] is None
    assert cv_original["status"] == "READY"
    if os.environ.get("INVARIANT_CHECK") == "1":
        return

    update_real = radar_store.update_post
    aprobada = {}
    reclamos = []

    def aprobar_antes_del_reclamo(id_actual, **campos):
        if id_actual == post_id and campos.get("estado") == "GENERANDO":
            assert not reclamos, "El reclamo perdido no debe reintentarse"
            version_aprobada = (datetime.fromisoformat(version) + timedelta(seconds=1)).isoformat(sep=" ")
            assert update_real(post_id, aprobado_at=version_aprobada, updated_at=version_aprobada)
            aprobada.update(radar_store.get_post(post_id))
            assert aprobada["updated_at"] != version
            assert campos.get("updated_at_esperado") == version
            # Se delega al UPDATE condicional real; no se simula su resultado.
            obtenido = update_real(id_actual, **campos)
            reclamos.append(obtenido)
            return obtenido
        return update_real(id_actual, **campos)

    monkeypatch.setattr(radar_store, "update_post", aprobar_antes_del_reclamo)
    respuesta = client.post(LOTE)
    assert respuesta.status_code == 200
    assert respuesta.json() == {"regenerados": 0, "ids": []}
    assert reclamos == [False]
    assert radar_store.get_post(post_id) == aprobada
    assert aprobada["estado"] == "LISTO" and aprobada["aprobado_at"]
    assert database.get_postulacion(pid) == cv_original
    radar.notify_post.assert_not_awaited()
    radar.wake_generation.assert_not_awaited()
    radar.enqueue_send.assert_not_awaited()
