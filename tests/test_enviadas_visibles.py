"""Postulaciones enviadas visibles: toda postulación del Radar se puede abrir desde el portal y
la que salió figura «Postulada» con su fecha de envío.

- Un post DESCARTADO con postulación (CV generado) entra siempre en la lista del Radar, sin el tope de descartados.
- `/api/ofertas?origen=radar` lista las postulaciones del Radar que no tienen post (el post se borró o nunca se
  creó): sin eso no se ven en ninguna vista.
- Al pasar un post a ENVIADO (por Gmail o marcado a mano), su postulación pasa de «Por postular» a «Postulada» con
  la fecha de envío; al iniciar, lo mismo para las enviadas de antes.
"""
import itertools
import sqlite3

import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock

import server
from core import database, radar_store
from core.radar import RadarLoop

_URN = itertools.count(1)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(server, "radar_loop", AsyncMock(spec=RadarLoop))
    return TestClient(server.app)


def _post(estado="ANALIZADA", postulacion=False, canal="email"):
    n = next(_URN)
    pid = radar_store.insert_post_if_new({
        "urn": f"urn:li:activity:{70000 + n}", "url": f"https://www.linkedin.com/feed/update/urn:li:activity:{70000 + n}/",
        "autor": f"Autor {n}", "cargo_autor": "HR", "texto": "Buscamos analista funcional. CV a rrhh@acme.test",
        "emails": ["rrhh@acme.test"],
    }, "consulta")
    campos = {"estado": estado, "canal": canal}
    if postulacion:
        campos["postulacion_id"] = database.create_postulacion(
            "Analista Funcional", "Acme", "texto", status="READY", origen="linkedin",
            radar_post_ref=f"urn:li:activity:{70000 + n}")
    radar_store.update_post(pid, **campos)
    return pid, campos.get("postulacion_id")


def test_descartado_con_postulacion_entra_siempre_en_la_lista(client):
    viejo, _ = _post("DESCARTADO", postulacion=True)
    _post("DESCARTADO")
    nuevo, _ = _post("DESCARTADO")
    ids = [p["id"] for p in client.get("/api/radar/posts", params={"limite_descartados": 1}).json()]
    assert ids == [nuevo, viejo]


def test_ofertas_origen_radar_lista_las_postulaciones_del_radar_sin_post(client):
    _post("ENVIADO", postulacion=True)
    huerfana = database.create_postulacion("Soporte", "Korva", "texto", status="READY", origen="linkedin",
                                           radar_post_ref="urn:li:activity:1")
    database.create_postulacion("Manual", "Velora Sistemas", "texto", status="READY")
    assert [o["id"] for o in client.get("/api/ofertas", params={"origen": "radar"}).json()] == [huerfana]
    assert [o["titulo"] for o in client.get("/api/ofertas").json()] == ["Manual"]
    assert client.get("/api/ofertas", params={"origen": "otro"}).status_code == 422


def test_post_enviado_deja_la_postulacion_postulada_con_la_fecha_de_envio():
    post, pid = _post("LISTO", postulacion=True)
    radar_store.update_post(post, estado="ENVIADO", sent_at="2026-11-09 23:49:00", message_id="<m@x>")
    p = database.get_postulacion(pid)
    assert (p["estado_postulacion"], p["fecha_postulacion"]) == ("POSTULADA", "2026-11-09 23:49:00")


def test_marcar_postulado_a_mano_deja_la_postulacion_postulada():
    post, pid = _post("ANALIZADA", postulacion=True, canal="dm")
    assert radar_store.mark_applied(post, "manual:dm", "2026-11-08 10:00:00")
    p = database.get_postulacion(pid)
    assert (p["estado_postulacion"], p["fecha_postulacion"]) == ("POSTULADA", "2026-11-08 10:00:00")


def test_al_iniciar_las_enviadas_de_antes_quedan_postuladas_sin_pisar_un_avance():
    enviado, pid = _post("LISTO", postulacion=True)
    otro, pid_entrevista = _post("LISTO", postulacion=True)
    # Como en la base real: ENVIADO escrito antes de que existiera la sincronización
    conn = sqlite3.connect(database.DB_PATH)
    with conn:
        conn.execute("UPDATE linkedin_posts SET estado='ENVIADO', sent_at='2026-11-01 09:00:00' WHERE id IN (?, ?)",
                     (enviado, otro))
        conn.execute("UPDATE postulaciones SET estado_postulacion='EN_ENTREVISTA' WHERE id = ?", (pid_entrevista,))
    conn.close()
    database.init_db(database.DB_PATH)
    p = database.get_postulacion(pid)
    assert (p["estado_postulacion"], p["fecha_postulacion"]) == ("POSTULADA", "2026-11-01 09:00:00")
    assert database.get_postulacion(pid_entrevista)["estado_postulacion"] == "EN_ENTREVISTA"
