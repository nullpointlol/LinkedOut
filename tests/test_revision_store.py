"""Revisión pendiente: lo que se completó o corrigió sin datos suficientes queda registrado."""
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import server
from core import revision_store
from core.radar import RadarLoop


def test_register_list_and_mark_reviewed():
    a = revision_store.registrar("dato_inventado", "pretensión salarial", "USD 1.800–2.200 brutos/mes",
                                 "La oferta pide pretensión", postulacion_id=35, post_id=62)
    # El mismo ítem no se duplica
    assert revision_store.registrar("dato_inventado", "pretensión salarial", "USD 1.800–2.200 brutos/mes",
                                    postulacion_id=35, post_id=62) == a
    revision_store.registrar("contradiccion", "inglés", "Advanced English", postulacion_id=36)
    assert revision_store.contar_pendientes() == 2
    assert [i["tipo"] for i in revision_store.listar()] == ["contradiccion", "dato_inventado"]
    assert revision_store.marcar_revisado(a) is True
    assert revision_store.contar_pendientes() == 1
    assert len(revision_store.listar(pendientes=False)) == 2
    assert revision_store.marcar_revisado(9999) is False


def test_unknown_type_is_rejected():
    with pytest.raises(ValueError):
        revision_store.registrar("otro", "x")


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(server, "radar_loop", AsyncMock(spec=RadarLoop))
    return TestClient(server.app)


def test_api_lists_and_marks_items_and_status_counts_them(client):
    item = revision_store.registrar("placeholder", "remuneración", "[Insertar remuneración pretendida]", post_id=62)
    assert client.get("/api/radar/status").json()["revision_pendientes"] == 1
    r = client.get("/api/radar/revision").json()
    assert r["pendientes"] == 1 and r["items"][0]["valor_usado"] == "[Insertar remuneración pretendida]"
    assert client.post(f"/api/radar/revision/{item}/revisado").json() == {"pendientes": 0}
    assert client.post("/api/radar/revision/9999/revisado").status_code == 404


REGLA = "La línea bajo el nombre es el rol con el que se presenta el candidato: 2 a 5 palabras. Reemplazá esa línea"


def test_stored_and_old_details_lose_the_prompt_rule():
    revision_store.registrar("contradiccion", "puesto objetivo", "X", f"No se pudo corregir: tiene 8 palabras. {REGLA}",
                             postulacion_id=1)
    # Ítem viejo, guardado antes del recorte: se limpia al devolverlo
    conn = revision_store._connect()
    with conn:
        conn.execute("UPDATE revision_pendiente SET detalle = ?", (f"tiene 8 palabras. {REGLA}",))
    conn.close()
    assert revision_store.listar()[0]["detalle"] == "tiene 8 palabras"
    revision_store.registrar("contradiccion", "puesto objetivo", "Y", f"tiene un nivel. {REGLA}", postulacion_id=1)
    conn = revision_store._connect()
    guardado = conn.execute("SELECT detalle FROM revision_pendiente WHERE valor_usado = 'Y'").fetchone()[0]
    conn.close()
    assert guardado == "tiene un nivel"


def test_resolver_anteriores_closes_only_the_unfixed_items_of_that_pair():
    sin_corregir = revision_store.registrar("contradiccion", "puesto objetivo", "A", "No se pudo corregir: x", postulacion_id=5)
    otro_campo = revision_store.registrar("contradiccion", "inglés", "B", "No se pudo corregir: x", postulacion_id=5)
    otra_postulacion = revision_store.registrar("contradiccion", "puesto objetivo", "A", "No se pudo corregir: x",
                                                postulacion_id=6)
    completado = revision_store.registrar("dato_inventado", "puesto objetivo", "C", "Completado en el CV", postulacion_id=5)
    assert revision_store.resolver_anteriores(5, "puesto objetivo") == 1
    assert revision_store.resolver_anteriores(5, "puesto objetivo") == 0
    assert revision_store.resolver_anteriores(None, "puesto objetivo") == 0
    pendientes = {i["id"] for i in revision_store.listar()}
    assert sin_corregir not in pendientes and {otro_campo, otra_postulacion, completado} <= pendientes


def test_a_fixed_contradiction_closes_the_earlier_unfixed_one():
    revision_store.registrar("contradiccion", "puesto objetivo", "CONVOCATORIA", "No se pudo corregir: x", postulacion_id=9)
    server._registrar_revision([{"campo": "puesto objetivo", "valor": "DATA ANALYST", "tipo": "contradiccion"}], 9, 4, "CV")
    assert [(i["valor_usado"], i["detalle"]) for i in revision_store.listar()] == [("DATA ANALYST", "Completado en el CV")]


def test_a_contradiction_still_in_the_final_cv_comes_back_pending():
    """El modelo dice que corrigió el campo, pero el chequeo final del CV la sigue encontrando: no se pierde el aviso."""
    sin_corregir = revision_store.registrar("contradiccion", "nivel de inglés", "Fluent in English",
                                            "No se pudo corregir: x", postulacion_id=9, post_id=4)
    server._registrar_revision([{"campo": "nivel de inglés", "valor": "Advanced English", "tipo": "contradiccion"}], 9, 4, "CV")
    assert sin_corregir not in {i["id"] for i in revision_store.listar()}
    # chequeo final: la misma frase sigue en el CV
    assert revision_store.registrar("contradiccion", "nivel de inglés", "Fluent in English",
                                    "No se pudo corregir: x", postulacion_id=9, post_id=4) == sin_corregir
    pendientes = {i["id"]: i for i in revision_store.listar()}
    assert sin_corregir in pendientes and pendientes[sin_corregir]["detalle"].startswith("No se pudo corregir")


def test_a_contradiction_the_user_already_reviewed_stays_closed():
    visto = revision_store.registrar("contradiccion", "nivel de inglés", "Fluent in English",
                                     "No se pudo corregir: x", postulacion_id=9)
    revision_store.marcar_revisado(visto)
    assert revision_store.registrar("contradiccion", "nivel de inglés", "Fluent in English",
                                    "No se pudo corregir: x", postulacion_id=9) == visto
    assert visto not in {i["id"] for i in revision_store.listar()}


def test_a_contradiction_fixed_in_the_message_does_not_close_the_cv_item():
    sin_corregir = revision_store.registrar("contradiccion", "nivel de inglés", "Fluent in English",
                                            "No se pudo corregir: x", postulacion_id=9)
    for donde in ("mensaje", "asunto", "pitch", "mensaje (email)"):
        server._registrar_revision([{"campo": "nivel de inglés", "valor": "Advanced", "tipo": "contradiccion"}], 9, 4, donde)
    assert sin_corregir in {i["id"] for i in revision_store.listar()}


def test_api_adds_puesto_and_empresa_from_the_post_or_the_postulacion(client):
    from core import database, radar_store
    post = radar_store.insert_post_if_new({"urn": "urn:li:activity:900", "texto": "t"}, query="q")
    radar_store.update_post(post, triage={"puesto": "Analista de Datos", "empresa": "Acme"})
    sin_triage = radar_store.insert_post_if_new({"urn": "urn:li:activity:901", "texto": "t"}, query="q")
    pid = database.create_postulacion(titulo="Dev Python", empresa="Globex", contenido="x", url="https://e.com/9")
    revision_store.registrar("placeholder", "a", "a", post_id=post)
    revision_store.registrar("placeholder", "b", "b", postulacion_id=pid)
    revision_store.registrar("placeholder", "c", "c", post_id=sin_triage)
    por_campo = {i["campo"]: i for i in client.get("/api/radar/revision").json()["items"]}
    assert (por_campo["a"]["puesto"], por_campo["a"]["empresa"]) == ("Analista de Datos", "Acme")
    assert (por_campo["b"]["puesto"], por_campo["b"]["empresa"]) == ("Dev Python", "Globex")
    assert (por_campo["c"]["puesto"], por_campo["c"]["empresa"]) == ("", "")
