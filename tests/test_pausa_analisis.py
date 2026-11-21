"""Pausar análisis: la cosecha sigue, pero no se clasifica ni se analiza nada."""
from core import radar_store
from core.radar import RadarLoop


def _loop():
    return RadarLoop(harvester_factory=None, triage_fn=None, analyze_fn=None, pipeline_fn=None,
                     compose_fn=None, send_fn=None, broadcast=None)


def test_con_analisis_pausado_no_toma_trabajo():
    radar_store.insert_post_if_new({"urn": "urn:li:activity:pausa-1", "texto": "Buscamos QA. CV a rrhh@acme.test",
                                    "emails": ["rrhh@acme.test"], "links": []}, "qa", "busqueda")
    loop = _loop()
    assert loop._siguiente_trabajo(todo=True) is not None
    radar_store.update_radar_state(analisis_pausado=True)
    assert loop._siguiente_trabajo(todo=True) is None
    radar_store.update_radar_state(analisis_pausado=False)
    assert loop._siguiente_trabajo(todo=True) is not None


def test_endpoint_pausa_y_reanuda(monkeypatch):
    from fastapi.testclient import TestClient
    import server
    client = TestClient(server.app)
    r = client.post("/api/radar/analisis", json={"pausada": True})
    assert r.status_code == 200 and r.json()["analisis_pausado"] is True
    r = client.post("/api/radar/analisis", json={"pausada": False})
    assert r.status_code == 200 and r.json()["analisis_pausado"] is False
