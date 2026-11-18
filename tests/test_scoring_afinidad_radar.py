"""Tests de propagación protegida de requisitos y cálculo de afinidad en Radar.

Verifica la propagación de decisiones a otros posts editables con la misma _clave,
la sincronización de la postulación vinculada, la preservación de posts protegidos
y la seguridad ante concurrencia con CAS.
"""
import copy
import itertools
import json
import os
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import server
from core import aprendizajes as learning, database, descartes, radar_store
from core.radar import RadarLoop

_ids = itertools.count(85000)
RESTRICCIONES = {
    "base": "San Isidro, AMBA",
    "presencial_hibrido_permitido": "AMBA (CABA y GBA)", "localidades_permitidas": ["amba", "caba", "gba", "buenos aires", "capital federal", "san martin", "vicente lopez", "san isidro", "san fernando", "tigre", "pilar", "escobar", "moron", "la matanza", "avellaneda", "lanus", "quilmes", "lomas de zamora", "tres de febrero", "san miguel", "jose c paz", "malvinas argentinas", "hurlingham", "ituzaingo", "moreno", "merlo", "berazategui", "florencio varela", "ezeiza", "esteban echeverria", "almirante brown"],
    "remoto_desde": "Argentina",
    "viajes_frecuentes_o_interior": False,
    "reubicacion": False,
    "rubro_o_funcion_exigida_sin_experiencia": False,
    "edad": {"actual": 36, "al": "2026-11-09"},
}


@pytest.fixture
def entorno(tmp_path, monkeypatch):
    monkeypatch.setattr(learning, "APRENDIZAJES_FILE", tmp_path / "aprendizajes.json")
    restricciones = tmp_path / "restricciones.json"
    restricciones.write_text(json.dumps(RESTRICCIONES), encoding="utf-8")
    monkeypatch.setattr(descartes, "RESTRICCIONES_FILE", restricciones)
    monkeypatch.setattr("core.radar_api.load_gmail_config", lambda: object())
    stub = AsyncMock(spec=RadarLoop)
    monkeypatch.setattr(server, "radar_loop", stub)
    return TestClient(server.app), stub


def requisito(nombre="Python", **extra):
    return {
        "nombre": nombre,
        "texto": nombre,
        "tipo": "REQUISITO_EXCLUYENTE",
        "estado": "BRECHA_REAL",
        "approved": False,
        **extra,
    }


def post(req=None, estado="ANALIZADA", sello=False, desde="ANALIZADA", **extra):
    n = next(_ids)
    if isinstance(req, list):
        reqs = req
    else:
        reqs = [req or requisito()]
    analisis = {
        "requisitos": reqs,
        "match_score": 40.0,
        "desglose_afinidad": {"brechas": [], "transferibles": [], "puntos_faltantes": 60.0},
    }
    if sello:
        primer_req = reqs[0]
        analisis["no_apto"] = {
            "motivos": [
                {
                    "requisito": primer_req["nombre"],
                    "categoria": "RUBRO",
                    "texto": primer_req.get("texto", primer_req["nombre"]),
                }
            ],
            "desde": desde,
        }
    primer_texto = reqs[0].get("texto", reqs[0]["nombre"])
    pid = radar_store.insert_post_if_new(
        {
            "urn": f"urn:li:activity:{n}",
            "texto": f"Oferta para {primer_texto} (id {n})",
            "url": f"https://example.test/{n}",
        },
        query="test",
    )
    radar_store.update_post(
        pid,
        estado=estado,
        analisis=analisis,
        email_to=f"r{n}@example.test",
        email_subject="Postulación",
        email_body="Hola",
        **extra,
    )
    return pid


def marcar(client, pid, nombre, valor=None):
    payload = {"nombre": nombre}
    if valor is not None:
        payload["eliminatorio"] = valor
    return client.post(f"/api/radar/posts/{pid}/requisito", json=payload)


def test_propagar_y_sincronizar(entorno):
    client, stub = entorno
    pid_a = post(requisito("Python"), estado="ANALIZADA")

    # invariante: base API 404 para requisito o post ausente, 409 para post ocupado (GENERANDO)
    assert marcar(client, pid_a, "ausente").status_code == 404
    assert marcar(client, 999999, "Python").status_code == 404
    busy_pid = post(requisito("Python"), estado="GENERANDO")
    assert marcar(client, busy_pid, "Python").status_code == 409

    # invariante: ciclar Post A a TRANSFERIBLE; sin aprobado_at ni enqueue_send
    resp = marcar(client, pid_a, "Python")
    assert resp.status_code == 200
    post_a = radar_store.get_post(pid_a)
    req_a = post_a["analisis"]["requisitos"][0]
    assert req_a["estado"] == "TRANSFERIBLE"
    assert req_a["approved"] is True
    assert req_a["aprendido"] is True
    assert post_a["aprobado_at"] is None
    stub.enqueue_send.assert_not_awaited()

    if os.getenv("INVARIANT_CHECK") == "1":
        return

    # defecto: propagación de estado a posts protegidos y destinos
    # Destinos editables sin aprobado_at:
    # 1. Post B en ANALIZADA con requisito normalizado por _clave ("  PYTHON  ")
    pid_b = post(requisito("  PYTHON  "), estado="ANALIZADA")
    linked_b = database.create_postulacion(
        titulo="Post B", empresa="Empresa B", contenido="x", url="", origen="linkedin"
    )
    database.update_postulacion(linked_b, requisitos=[requisito("  PYTHON  ")], match_score=40.0)
    radar_store.update_post(pid_b, postulacion_id=linked_b)

    # 2. Post C en LISTO sin aprobado_at
    pid_c = post(requisito("Python"), estado="LISTO")
    linked_c = database.create_postulacion(
        titulo="Post C", empresa="Empresa C", contenido="x", url="", origen="linkedin"
    )
    database.update_postulacion(linked_c, requisitos=[requisito("Python")], match_score=40.0)
    radar_store.update_post(pid_c, postulacion_id=linked_c)

    # 3. Post D en IGNORADA con sello y desde="ANALIZADA"
    pid_d = post(requisito("Python"), estado="IGNORADA", sello=True, desde="ANALIZADA")

    # 4. Post D2 en IGNORADA con sello y desde="LISTO"
    pid_d2 = post(requisito("Python"), estado="IGNORADA", sello=True, desde="LISTO")

    # Posts protegidos (EXCLUIDOS de propagación):
    pid_enviado = post(requisito("Python"), estado="ENVIADO")
    pid_enviando = post(requisito("Python"), estado="ENVIANDO")
    pid_generando = post(requisito("Python"), estado="GENERANDO")
    pid_analizando = post(requisito("Python"), estado="ANALIZANDO")
    pid_descartado = post(requisito("Python"), estado="DESCARTADO")
    pid_aprobado = post(requisito("Python"), estado="LISTO", aprobado_at="2026-11-10 10:00:00")
    pid_ign_sin_sello = post(requisito("Python"), estado="IGNORADA", sello=False)
    # Nombre parecido pero distinta _clave: no debe propagar
    pid_parecido = post(requisito("Pythonista"), estado="ANALIZADA")

    protegidos_antes = {
        pid: copy.deepcopy(radar_store.get_post(pid))
        for pid in (
            pid_enviado,
            pid_enviando,
            pid_generando,
            pid_analizando,
            pid_descartado,
            pid_aprobado,
            pid_ign_sin_sello,
            pid_parecido,
        )
    }

    # Ciclar en Post A de TRANSFERIBLE a MATCH_DIRECTO
    resp2 = marcar(client, pid_a, "Python")
    assert resp2.status_code == 200
    post_a_actual = radar_store.get_post(pid_a)
    assert post_a_actual["analisis"]["requisitos"][0]["estado"] == "MATCH_DIRECTO"

    # Verificar sincronización y asignación directa (sin segundo ciclo) en Post B
    post_b_actual = radar_store.get_post(pid_b)
    req_b = post_b_actual["analisis"]["requisitos"][0]
    assert req_b["estado"] == "MATCH_DIRECTO"
    assert req_b["approved"] is True
    assert req_b["aprendido"] is True
    assert post_b_actual["analisis"]["match_score"] > 40.0
    assert post_b_actual["aprobado_at"] is None
    # Postulación vinculada sincronizada
    postulacion_b = database.get_postulacion(linked_b)
    assert postulacion_b["requisitos"][0]["estado"] == "MATCH_DIRECTO"
    assert postulacion_b["requisitos"][0]["approved"] is True
    assert postulacion_b["match_score"] == post_b_actual["analisis"]["match_score"]

    # Verificar Post C en LISTO
    post_c_actual = radar_store.get_post(pid_c)
    assert post_c_actual["analisis"]["requisitos"][0]["estado"] == "MATCH_DIRECTO"
    assert post_c_actual["analisis"]["match_score"] > 40.0
    assert post_c_actual["aprobado_at"] is None
    postulacion_c = database.get_postulacion(linked_c)
    assert postulacion_c["requisitos"][0]["estado"] == "MATCH_DIRECTO"

    # Verificar Post D: sello se limpia y vuelve a su desde ("ANALIZADA")
    post_d_actual = radar_store.get_post(pid_d)
    assert post_d_actual["estado"] == "ANALIZADA"
    assert "no_apto" not in post_d_actual["analisis"] or not post_d_actual["analisis"]["no_apto"].get("motivos")
    assert post_d_actual["aprobado_at"] is None

    # Verificar Post D2: sello se limpia y vuelve a su desde ("LISTO")
    post_d2_actual = radar_store.get_post(pid_d2)
    assert post_d2_actual["estado"] == "LISTO"
    assert "no_apto" not in post_d2_actual["analisis"] or not post_d2_actual["analisis"]["no_apto"].get("motivos")
    assert post_d2_actual["aprobado_at"] is None

    # Verificar que NINGUNO de los protegidos cambió (idénticos incluida updated_at y estado)
    for pid, antes in protegidos_antes.items():
        despues = radar_store.get_post(pid)
        assert despues == antes

    # Invariante de envío mantenido
    stub.enqueue_send.assert_not_awaited()


def test_invariantes_ciclo_vuelta_y_envio(entorno):
    client, stub = entorno
    pid = post(requisito("SQL"), estado="ANALIZADA")
    radar_store.update_radar_state(analisis_pausado=True, generacion_pausada=True, envios_pausados=True)

    # invariante: ciclo de 3 estados BRECHA_REAL -> TRANSFERIBLE -> MATCH_DIRECTO -> BRECHA_REAL
    # Clic 1: BRECHA_REAL -> TRANSFERIBLE
    assert marcar(client, pid, "SQL").status_code == 200
    p1 = radar_store.get_post(pid)
    assert p1["analisis"]["requisitos"][0]["estado"] == "TRANSFERIBLE"
    assert p1["aprobado_at"] is None

    # Clic 2: TRANSFERIBLE -> MATCH_DIRECTO
    assert marcar(client, pid, "SQL").status_code == 200
    p2 = radar_store.get_post(pid)
    assert p2["analisis"]["requisitos"][0]["estado"] == "MATCH_DIRECTO"
    assert p2["aprobado_at"] is None

    # Clic 3: MATCH_DIRECTO -> BRECHA_REAL
    assert marcar(client, pid, "SQL").status_code == 200
    p3 = radar_store.get_post(pid)
    assert p3["analisis"]["requisitos"][0]["estado"] == "BRECHA_REAL"
    assert p3["aprobado_at"] is None

    # invariante: marcar y desmarcar devuelve desde IGNORADA a LISTO / ANALIZADA
    pid_listo = post(requisito("Presencial en Mendoza"), estado="LISTO")
    # Marcar descarte
    assert marcar(client, pid_listo, "Presencial en Mendoza", True).status_code == 200
    p_ign = radar_store.get_post(pid_listo)
    assert p_ign["estado"] == "IGNORADA"
    assert p_ign["analisis"]["no_apto"]["desde"] == "LISTO"
    assert p_ign["aprobado_at"] is None
    # Desmarcar descarte devuelve a LISTO
    assert marcar(client, pid_listo, "Presencial en Mendoza", False).status_code == 200
    p_rec = radar_store.get_post(pid_listo)
    assert p_rec["estado"] == "LISTO"
    assert "no_apto" not in p_rec["analisis"]
    assert p_rec["aprobado_at"] is None

    # Pausas del radar intactas y sin envíos
    assert all(
        radar_store.get_radar_state()[k] is True
        for k in ("analisis_pausado", "generacion_pausada", "envios_pausados")
    )
    stub.enqueue_send.assert_not_awaited()

    if os.getenv("INVARIANT_CHECK") == "1":
        return

    # defecto: propagación del descarte y su vuelta conservando origen
    pid_a = post(requisito("Zona Oeste"), estado="LISTO")
    pid_b = post(requisito("Zona Oeste"), estado="LISTO")
    pid_c = post(requisito("Zona Oeste"), estado="ANALIZADA")

    # Marcar en Post A propaga a Post B y Post C llevándolos a IGNORADA con su respectivo desde
    assert marcar(client, pid_a, "Zona Oeste", True).status_code == 200
    assert radar_store.get_post(pid_a)["estado"] == "IGNORADA"
    assert radar_store.get_post(pid_b)["estado"] == "IGNORADA"
    assert radar_store.get_post(pid_b)["analisis"]["no_apto"]["desde"] == "LISTO"
    assert radar_store.get_post(pid_c)["estado"] == "IGNORADA"
    assert radar_store.get_post(pid_c)["analisis"]["no_apto"]["desde"] == "ANALIZADA"

    # Desmarcar en Post A propaga la vuelta restaurando los estados originales
    assert marcar(client, pid_a, "Zona Oeste", False).status_code == 200
    assert radar_store.get_post(pid_a)["estado"] == "LISTO"
    assert radar_store.get_post(pid_b)["estado"] == "LISTO"
    assert "no_apto" not in radar_store.get_post(pid_b)["analisis"]
    assert radar_store.get_post(pid_c)["estado"] == "ANALIZADA"
    assert "no_apto" not in radar_store.get_post(pid_c)["analisis"]

    # IGNORADA sin sello no es destino retroactivo editable
    pid_ign_sin_sello = post(requisito("Zona Oeste"), estado="IGNORADA", sello=False)
    assert marcar(client, pid_ign_sin_sello, "Zona Oeste", False).status_code == 409

    # En ningún caso se envía ni se aprueba
    assert radar_store.get_post(pid_b)["aprobado_at"] is None
    assert radar_store.get_post(pid_c)["aprobado_at"] is None
    stub.enqueue_send.assert_not_awaited()


def test_destino_cambia_antes_de_guardar(entorno, monkeypatch):
    client, stub = entorno
    pid = post(requisito("Docker"), estado="ANALIZADA")
    p_orig = radar_store.get_post(pid)
    v1 = p_orig["updated_at"]

    # invariante: CAS en update_post rechaza versión obsoleta y acepta versión actual
    assert radar_store.update_post(pid, updated_at_esperado=v1, estado="LISTO") is True
    p_mod = radar_store.get_post(pid)
    assert p_mod["estado"] == "LISTO"
    assert radar_store.update_post(pid, updated_at_esperado=v1, estado="ANALIZADA") is False
    assert radar_store.get_post(pid)["estado"] == "LISTO"
    stub.enqueue_send.assert_not_awaited()

    if os.getenv("INVARIANT_CHECK") == "1":
        return

    # defecto: simulación de actualización concurrente durante la propagación
    pid_a = post(requisito("Kubernetes"), estado="ANALIZADA")
    pid_b = post(requisito("Kubernetes"), estado="LISTO")
    linked_b = database.create_postulacion(
        titulo="Post B", empresa="Empresa B", contenido="x", url="", origen="linkedin"
    )
    database.update_postulacion(linked_b, requisitos=[requisito("Kubernetes")], match_score=40.0)
    radar_store.update_post(pid_b, postulacion_id=linked_b)

    pid_c = post(requisito("Kubernetes"), estado="ANALIZADA")
    linked_c = database.create_postulacion(
        titulo="Post C", empresa="Empresa C", contenido="x", url="", origen="linkedin"
    )
    database.update_postulacion(linked_c, requisitos=[requisito("Kubernetes")], match_score=40.0)
    radar_store.update_post(pid_c, postulacion_id=linked_c)

    # Interceptar update_post para simular carrera en pid_b: otro worker aprueba o mueve Post B a ENVIANDO
    real_update = radar_store.update_post

    def update_con_carrera(post_id, **kwargs):
        if post_id == pid_b and kwargs.get("updated_at_esperado"):
            real_update(pid_b, estado="ENVIANDO", aprobado_at="2026-11-10 12:34:56")
        return real_update(post_id, **kwargs)

    monkeypatch.setattr(radar_store, "update_post", update_con_carrera)

    # Ciclar en Post A: debe actualizar Post A, intentar Post B (falla CAS sin pisarlo ni actualizar su postulación)
    # y actualizar Post C exitosamente.
    resp = marcar(client, pid_a, "Kubernetes")
    assert resp.status_code == 200

    # Post B no debe ser sobrescrito: queda en ENVIANDO con su aprobado_at
    post_b_actual = radar_store.get_post(pid_b)
    assert post_b_actual["estado"] == "ENVIANDO"
    assert post_b_actual["aprobado_at"] == "2026-11-10 12:34:56"
    assert post_b_actual["analisis"]["requisitos"][0]["estado"] == "BRECHA_REAL"

    # Postulación vinculada de Post B NO fue actualizada
    postulacion_b = database.get_postulacion(linked_b)
    assert postulacion_b["requisitos"][0]["estado"] == "BRECHA_REAL"
    assert postulacion_b["match_score"] == 40.0

    # Post C que no sufrió carrera sí fue propagado
    post_c_actual = radar_store.get_post(pid_c)
    assert post_c_actual["analisis"]["requisitos"][0]["estado"] == "TRANSFERIBLE"
    postulacion_c = database.get_postulacion(linked_c)
    assert postulacion_c["requisitos"][0]["estado"] == "TRANSFERIBLE"

    stub.enqueue_send.assert_not_awaited()
