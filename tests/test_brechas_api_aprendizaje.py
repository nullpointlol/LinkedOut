"""Contrato de decisiones de descarte; SQLite y proveedores completamente aislados."""
import itertools
import json
import os
from types import MethodType
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient

import server
from core import aprendizajes as learning, database, descartes, radar_store
from core.radar import RadarLoop
from core.strategist import StrategistAgent

_ids = itertools.count(80000)
RESTRICCIONES = {"base": "San Isidro, AMBA", "presencial_hibrido_permitido": "AMBA (CABA y GBA)", "localidades_permitidas": ["amba", "caba", "gba", "buenos aires", "capital federal", "san martin", "vicente lopez", "san isidro", "san fernando", "tigre", "pilar", "escobar", "moron", "la matanza", "avellaneda", "lanus", "quilmes", "lomas de zamora", "tres de febrero", "san miguel", "jose c paz", "malvinas argentinas", "hurlingham", "ituzaingo", "moreno", "merlo", "berazategui", "florencio varela", "ezeiza", "esteban echeverria", "almirante brown"],
                 "remoto_desde": "Argentina", "viajes_frecuentes_o_interior": False,
                 "reubicacion": False, "rubro_o_funcion_exigida_sin_experiencia": False,
                 "edad": {"actual": 36, "al": "2026-11-09"}}


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


def requisito(nombre="Experiencia en gastronomía", **extra):
    return {"nombre": nombre, "texto": nombre, "tipo": "REQUISITO_EXCLUYENTE",
            "estado": "BRECHA_REAL", "approved": False, **extra}


def post(req=None, estado="ANALIZADA", sello=False, **extra):
    n = next(_ids)
    req = req or requisito()
    analisis = {"requisitos": [req], "match_score": 40}
    if sello:
        analisis["no_apto"] = {"motivos": [{"requisito": req["nombre"], "categoria": "RUBRO", "texto": req["texto"]}]}
    pid = radar_store.insert_post_if_new({"urn": str(n), "texto": req["texto"], "url": f"https://example.test/{n}"}, query="test")
    radar_store.update_post(pid, estado=estado, analisis=analisis, email_to=f"r{n}@example.test",
                            email_subject="Postulación", email_body="Hola", **extra)
    return pid


def marcar(client, pid, nombre, valor):
    return client.post(f"/api/radar/posts/{pid}/requisito", json={"nombre": nombre, "eliminatorio": valor})


def test_descarte_aprende_y_desmarca(entorno):
    client, stub = entorno
    nombre = "Híbrido en Córdoba"
    pid = post(requisito(nombre))
    # invariante: base API 404 para nombre ausente, 409 ocupado; sin envío implícito.
    assert marcar(client, pid, "ausente", True).status_code == 404
    busy = post(estado="GENERANDO")
    assert marcar(client, busy, requisito()["nombre"], True).status_code == 409
    assert marcar(client, 999999, nombre, True).status_code == 404
    stub.enqueue_send.assert_not_awaited()
    if os.getenv("INVARIANT_CHECK") == "1":
        return
    linked = database.create_postulacion(titulo="Test", empresa="Test", contenido="x", url="", origen="linkedin")
    database.update_postulacion(linked, requisitos=[requisito(nombre)])
    radar_store.update_post(pid, postulacion_id=linked)
    for valor in (True, False):
        response = marcar(client, pid, nombre, valor)
        assert response.status_code == 200, response.text
        saved = radar_store.get_post(pid)
        req = saved["analisis"]["requisitos"][0]
        assert req["estado"] == "BRECHA_REAL" and req["approved"] is False
        assert saved["estado"] == ("IGNORADA" if valor else "ANALIZADA") and saved["aprobado_at"] is None
        assert bool(saved["analisis"].get("no_apto")) is valor
        assert learning.obtener_aprendizajes()[nombre.lower()]["eliminatorio"] is valor
        assert database.get_postulacion(linked)["requisitos"][0]["eliminatorio"] is valor
        siguientes = [requisito("HÍBRIDO EN CÓRDOBA")]
        learning.aplicar_aprendizajes_a_requisitos(siguientes)
        assert bool(descartes.motivos_descarte({"requisitos": siguientes}, RESTRICCIONES)) is valor
        for _ in range(3):
            assert client.post(f"/api/radar/posts/{pid}/requisito", json={"nombre": nombre}).status_code == 200
            assert learning.obtener_aprendizajes()[nombre.lower()]["eliminatorio"] is valor
    learning.registrar_aprendizaje("Docker", "TRANSFERIBLE")
    learning.registrar_aprendizaje("Docker", "BRECHA_REAL")
    assert learning.obtener_aprendizajes()["docker"]["estado"] == "BRECHA_REAL"


@pytest.mark.asyncio
async def test_equivalencia_en_prompt(entorno):
    if os.getenv("INVARIANT_CHECK") == "1":
        return
    client, _ = entorno
    pid = post(requisito("Experiencia en rubro gastronómico"))
    assert marcar(client, pid, "Experiencia en rubro gastronómico", True).status_code == 200
    bridge = Mock()

    async def respuesta(prompt, **kwargs):
        assert "Experiencia en rubro gastronómico" in prompt
        assert "eliminatorio: true" in prompt
        return {"requisitos": [requisito(eliminatorio=True, categoria_descarte="RUBRO")]}

    bridge.run_prompt = AsyncMock(side_effect=respuesta)
    agent = StrategistAgent(bridge=bridge)
    agent.load_candidate_experience = lambda: "Perfil sintético"
    agent.expander = Mock(get_expanded_competencies=lambda: [])
    resultado = await agent.analyze_offer("Experiencia en gastronomía")
    assert resultado["requisitos"][0]["estado"] == "BRECHA_REAL"
    assert resultado["no_apto"]["motivos"][0]["categoria"] == "RUBRO"
    learning.registrar_aprendizaje("Experiencia en rubro gastronómico", "BRECHA_REAL", eliminatorio=False)
    assert "eliminatorio: false" in learning.aprendizajes_para_prompt()
    unrelated = [requisito("Experiencia en finanzas")]
    learning.aplicar_aprendizajes_a_requisitos(unrelated)
    assert "eliminatorio" not in unrelated[0]


def test_force_no_aprueba_y_error_conserva_sello(entorno):
    client, stub = entorno
    pid = post(sello=True)
    radar_store.update_radar_state(analisis_pausado=True, generacion_pausada=True, envios_pausados=True)
    stub.generate_cv.side_effect = ValueError("rechazado")
    before = radar_store.get_post(pid)
    # invariante: base generate_cv rechaza con 409; no aprueba ni envía ni cambia pausas.
    assert client.post(f"/api/radar/posts/{pid}/force").status_code == 409
    assert radar_store.get_post(pid) == before
    stub.enqueue_send.assert_not_awaited()
    assert all(radar_store.get_radar_state()[k] is True for k in ("analisis_pausado", "generacion_pausada", "envios_pausados"))
    if os.getenv("INVARIANT_CHECK") == "1":
        return
    stub.generate_cv = MethodType(RadarLoop.generate_cv, stub)
    assert client.post(f"/api/radar/posts/{pid}/force").status_code == 200
    saved = radar_store.get_post(pid)
    assert saved["estado"] == "GENERANDO" and saved["gen_prioridad"] == 2
    assert "no_apto" not in saved["analisis"] and saved["aprobado_at"] is None
    assert all(radar_store.get_radar_state()[k] is True for k in ("analisis_pausado", "generacion_pausada", "envios_pausados"))
    listo = post(estado="LISTO", sello=True)
    stub.generate_cv = AsyncMock()
    assert client.post(f"/api/radar/posts/{listo}/force").status_code == 200
    saved = radar_store.get_post(listo)
    assert saved["estado"] == "LISTO" and saved["aprobado_at"] is None
    assert "no_apto" not in saved["analisis"]
    stub.generate_cv.assert_not_awaited()
    stub.enqueue_send.assert_not_awaited()


def test_approve_all_omite_y_unitario_valida_version(entorno):
    client, stub = entorno
    bloqueado = post(estado="LISTO", sello=True)
    # invariante: base sin versión=400 y vieja=409, sin aprobación ni enqueue_send.
    assert client.post(f"/api/radar/posts/{bloqueado}/approve", json={}).status_code == 400
    assert client.post(f"/api/radar/posts/{bloqueado}/approve", json={"updated_at": "viejo"}).status_code == 409
    assert radar_store.get_post(bloqueado)["aprobado_at"] is None
    stub.enqueue_send.assert_not_awaited()
    if os.getenv("INVARIANT_CHECK") == "1":
        return
    valido, duplicado, viejo, aprobado = [post(estado="LISTO") for _ in range(4)]
    email = radar_store.get_post(valido)["email_to"]
    radar_store.update_post(bloqueado, email_to=email)
    radar_store.update_post(duplicado, email_to=email)
    radar_store.update_post(aprobado, aprobado_at="2026-11-09 15:00:00")
    ids = [bloqueado, valido, duplicado, viejo, aprobado]
    versiones = {str(i): radar_store.get_post(i)["updated_at"] for i in ids}
    versiones[str(viejo)] = "viejo"
    response = client.post("/api/radar/approve-all", json={"ids": ids, "updated_at": versiones})
    assert response.status_code == 200
    data = response.json()
    assert data["encolados"] == [valido]
    omitidos = {o["id"]: o for o in data["omitidos"]}
    assert omitidos[bloqueado] == {"id": bloqueado, "motivo": "No apto: Experiencia en gastronomía", "tipo": "no_apto"}
    assert omitidos[duplicado]["tipo"] == "duplicado"
    assert "cambió" in omitidos[viejo]["motivo"] and "ya aprobado" in omitidos[aprobado]["motivo"]
    assert radar_store.get_post(bloqueado)["aprobado_at"] is None
    stub.enqueue_send.assert_awaited_once_with(valido)
    response = client.post(f"/api/radar/posts/{bloqueado}/approve", json={"updated_at": versiones[str(bloqueado)], "confirm_duplicate": True})
    assert response.status_code == 200
    assert radar_store.get_post(bloqueado)["aprobado_at"]


def test_resumen_expone_motivos(entorno):
    if os.getenv("INVARIANT_CHECK") == "1":
        return
    client, _ = entorno
    pid = post(requisito(eliminatorio=True, categoria_descarte="RUBRO"), sello=True)
    response = client.get("/api/radar/posts")
    rows = response.json()
    if isinstance(rows, dict):
        rows = rows["posts"]
    row = next(p for p in rows if p["id"] == pid)
    assert row["no_apto"] == radar_store.get_post(pid)["analisis"]["no_apto"]
    assert row["requisitos_estado"][0]["nombre"] == "Experiencia en gastronomía"
    assert row["requisitos_estado"][0]["eliminatorio"] is True
    assert row["requisitos_estado"][0]["categoria_descarte"] == "RUBRO"
    assert client.get(f"/api/radar/posts/{pid}").json()["analisis"]["no_apto"] == row["no_apto"]


def test_fallback_no_se_sella_al_marcar(entorno):
    client, stub = entorno
    pid = post()
    analisis = radar_store.get_post(pid)["analisis"]
    analisis["analisis_fallback"] = True
    radar_store.update_post(pid, analisis=analisis)
    response = marcar(client, pid, requisito()["nombre"], True)
    # invariante: base fallback no lleva sello, marcar no cambia estado ni aprueba/envía.
    assert response.status_code == 200
    saved = radar_store.get_post(pid)
    assert saved["estado"] == "ANALIZADA" and saved["aprobado_at"] is None
    assert "no_apto" not in saved["analisis"]
    stub.enqueue_send.assert_not_awaited()
    if os.getenv("INVARIANT_CHECK") == "1":
        return
    assert saved["analisis"]["requisitos"][0]["estado"] == "BRECHA_REAL"


@pytest.mark.parametrize("nombre", ["Docker", "Inglés", "Certificación AWS", "8 años en desarrollo"])
def test_decision_manual_tecnica_prevalece_sobre_confirmador(entorno, nombre):
    if os.getenv("INVARIANT_CHECK") == "1":
        return
    client, _ = entorno
    pid = post(requisito(nombre))
    assert marcar(client, pid, nombre, True).status_code == 200
    assert radar_store.get_post(pid)["analisis"]["no_apto"]["motivos"] == [
        {"requisito": nombre, "categoria": "RUBRO", "texto": nombre}]


@pytest.mark.parametrize("estado", ["ENVIADO", "DESCARTADO", "IGNORADA", "ENVIANDO"])
def test_acciones_no_tocan_cerrados(entorno, estado):
    if os.getenv("INVARIANT_CHECK") == "1":
        return
    client, stub = entorno
    pid = post(estado=estado, sello=estado != "IGNORADA")
    before = radar_store.get_post(pid)
    assert marcar(client, pid, requisito()["nombre"], True).status_code == 409
    assert client.post(f"/api/radar/posts/{pid}/force").status_code == 409
    assert radar_store.get_post(pid) == before
    stub.generate_cv.assert_not_awaited()


def test_marcar_true_sella_y_false_limpia(entorno):
    client, stub = entorno
    req = requisito(texto="Experiencia en rubro gastronómico", eliminatorio=False,
                    categoria_descarte="RUBRO")
    pid = post(req)
    radar_store.update_post(pid, texto="Oferta sintética sin cita literal del requisito")
    for decision in (True, False):
        response = marcar(client, pid, req["nombre"], decision)
        assert response.status_code == 200
        saved = radar_store.get_post(pid)
        actual = saved["analisis"]["requisitos"][0]
        # invariante: no cambia aprobación ni envío; el descarte archiva y permite volver.
        assert saved["estado"] == ("IGNORADA" if decision else "ANALIZADA") and saved["aprobado_at"] is None
        assert actual["estado"] == "BRECHA_REAL" and actual["approved"] is False
        stub.enqueue_send.assert_not_awaited()
        if os.getenv("INVARIANT_CHECK") == "1":
            return
        assert actual["eliminatorio"] is decision
        assert actual["eliminatorio_usuario"] is decision
        assert bool(saved["analisis"].get("no_apto")) is decision
        aprendidos = [requisito(eliminatorio=decision)]
        learning.aplicar_aprendizajes_a_requisitos(aprendidos)
        assert aprendidos[0]["eliminatorio_usuario"] is decision
        assert bool(descartes.motivos_descarte({"requisitos": aprendidos}, RESTRICCIONES)) is decision
    # La API relee el archivo configurado en core.descartes en cada llamada.
    descartes.RESTRICCIONES_FILE.write_text("{}", encoding="utf-8")
    assert marcar(client, pid, req["nombre"], True).status_code == 200
    assert "no_apto" not in radar_store.get_post(pid)["analisis"]
