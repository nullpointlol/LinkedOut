"""Tests del endpoint y armado de GET /api/radar/procesos (Task 4: Historial de procesos)."""
import json
from unittest.mock import AsyncMock
import pytest
from fastapi.testclient import TestClient

import server
from core import radar_store
from core.radar import RadarLoop


def _nuevo_post(n: int, fuente: str = "busqueda", estado: str = "NUEVO", prioridad: int = 0, gen_prioridad: int = 0, triage: dict = None, progreso: dict = None, analisis: dict = None, error: str = None, intentos: int = None) -> int:
    pid = radar_store.insert_post_if_new(
        {"urn": f"urn:li:activity:{n}", "texto": f"Búsqueda laboral {n}", "emails": [f"hr{n}@ejemplo.com"]},
        query="feed" if fuente == "feed" else "busqueda",
        fuente=fuente,
    )
    cambios = {"estado": estado, "prioridad": prioridad, "gen_prioridad": gen_prioridad}
    if triage is not None:
        cambios["triage"] = triage
    if progreso is not None:
        cambios["progreso"] = progreso
    if analisis is not None:
        cambios["analisis"] = analisis
    if error is not None:
        cambios["error"] = error
    if intentos is not None:
        cambios["intentos"] = intentos
    radar_store.update_post(pid, **cambios)
    return pid


@pytest.fixture
def radar_stub(monkeypatch):
    stub = AsyncMock(spec=RadarLoop)
    stub.generando_ahora = None
    stub.en_vuelo = set()
    monkeypatch.setattr(server, "radar_loop", stub)
    return stub


@pytest.fixture
def client(radar_stub):
    return TestClient(server.app)


def test_procesos_orden_exacto_del_worker(client, radar_stub):
    """GENERANDO con gen_prioridad 1 (id 1), 2 (id 2), 2 (id 3) y generando_ahora = 2:
    en_curso[0]["post_id"] == 2, [i["post_id"] for i in cola_generacion] == [3, 1], posiciones [1, 2].
    """
    id1 = _nuevo_post(1, estado="GENERANDO", gen_prioridad=1, triage={"puesto": "P1", "empresa": "E1"})
    id2 = _nuevo_post(2, estado="GENERANDO", gen_prioridad=2, triage={"puesto": "P2", "empresa": "E2"})
    id3 = _nuevo_post(3, estado="GENERANDO", gen_prioridad=2, triage={"puesto": "P3", "empresa": "E3"})

    radar_stub.generando_ahora = id2
    radar_stub.en_vuelo = set()

    res = client.get("/api/radar/procesos")
    assert res.status_code == 200
    data = res.json()

    assert len(data["en_curso"]) >= 1
    assert data["en_curso"][0]["post_id"] == id2
    assert data["en_curso"][0]["tipo"] == "generacion"
    assert data["en_curso"][0]["posicion"] is None

    cola_gen = data["cola_generacion"]
    assert [i["post_id"] for i in cola_gen] == [id3, id1]
    assert [i["posicion"] for i in cola_gen] == [1, 2]
    assert cola_gen[0]["tipo"] == "generacion"
    assert cola_gen[1]["tipo"] == "generacion"


def test_cola_analisis_coincide_con_siguiente_trabajo(client, monkeypatch):
    """NUEVO/OFERTA en feed, búsqueda y priorizados:
    el orden de cola_analisis es igual a los ids que devuelve _siguiente_trabajo
    llamado repetidamente sobre un RadarLoop real (marcando cada uno como en vuelo).
    """
    radar = RadarLoop(
        harvester_factory=lambda: None,
        triage_fn=AsyncMock(),
        analyze_fn=AsyncMock(),
        pipeline_fn=AsyncMock(),
        compose_fn=AsyncMock(),
        send_fn=AsyncMock(),
        broadcast=AsyncMock(),
    )
    monkeypatch.setattr(server, "radar_loop", radar)

    # Creamos un mix de posts con distintos niveles:
    # id1: búsqueda NUEVO normal (nivel 2, tipo 0)
    # id2: feed NUEVO normal (nivel 1, tipo 0)
    # id3: búsqueda OFERTA normal (nivel 2, tipo 1)
    # id4: feed OFERTA normal (nivel 1, tipo 1)
    # id5: priorizado NUEVO (nivel 0, tipo 0)
    # id6: priorizado OFERTA (nivel 0, tipo 1)
    p_b_nuevo = _nuevo_post(10, fuente="busqueda", estado="NUEVO", prioridad=0)
    p_f_nuevo = _nuevo_post(11, fuente="feed", estado="NUEVO", prioridad=0)
    p_b_oferta = _nuevo_post(12, fuente="busqueda", estado="OFERTA", prioridad=0)
    p_f_oferta = _nuevo_post(13, fuente="feed", estado="OFERTA", prioridad=0)
    p_p_nuevo = _nuevo_post(14, fuente="busqueda", estado="NUEVO", prioridad=1)
    p_p_oferta = _nuevo_post(15, fuente="feed", estado="OFERTA", prioridad=1)

    # Llamamos repetidamente a _siguiente_trabajo marcando en vuelo
    ids_worker = []
    radar_sim = RadarLoop(
        harvester_factory=lambda: None,
        triage_fn=AsyncMock(),
        analyze_fn=AsyncMock(),
        pipeline_fn=AsyncMock(),
        compose_fn=AsyncMock(),
        send_fn=AsyncMock(),
        broadcast=AsyncMock(),
    )
    while True:
        trabajo = radar_sim._siguiente_trabajo(todo=True)
        if trabajo is None:
            break
        ids = [p["id"] for p in trabajo[1]]
        radar_sim._en_vuelo.update(ids)
        ids_worker.extend(ids)

    res = client.get("/api/radar/procesos")
    assert res.status_code == 200
    data = res.json()

    ids_cola = [i["post_id"] for i in data["cola_analisis"]]
    assert ids_cola == ids_worker


def test_procesos_terminados_ultimos_10_con_duracion(client, radar_stub):
    """12 posts con progreso.fin → 10, el más reciente primero, duracion_s = suma de pasos."""
    ids = []
    for i in range(1, 13):
        minutos = 10 + i
        prog = {
            "inicio": f"2026-11-04T10:{minutos:02d}:00",
            "fin": f"2026-11-04T10:{minutos:02d}:30",
            "resultado": "LISTO",
            "pasos": [
                {"paso": "preparar", "duracion_s": 5.0},
                {"paso": "redactar", "duracion_s": 10.0 + i},
            ],
        }
        pid = _nuevo_post(
            20 + i,
            estado="LISTO",
            progreso=prog,
            triage={"puesto": f"Puesto {i}", "empresa": f"Empresa {i}"},
        )
        ids.append((pid, prog["fin"], 15.0 + i))

    res = client.get("/api/radar/procesos")
    assert res.status_code == 200
    data = res.json()

    terminados = data["terminados"]
    assert len(terminados) == 10

    # Orden: más reciente primero (los ids 12 a 3)
    esperados = list(reversed(ids))[:10]
    for actual, (esperado_id, esperado_fin, esperada_duracion) in zip(terminados, esperados):
        assert actual["post_id"] == esperado_id
        assert actual["terminado_at"] == esperado_fin
        assert actual["duracion_s"] == round(esperada_duracion, 1)
        assert actual["resultado"] == "LISTO"
        assert actual["tipo"] == "generacion"


def test_procesos_topes_y_backlog(client, radar_stub):
    """Contadores del día y backlog.quedan."""
    radar_store.update_radar_state(
        tope_posts_dia=600,
        tope_envios_dia=40,
        backlog_activo=True,
        contadores_dia={"leidos_busquedas": 120, "con_email": 50, "match": 14, "listos": 5, "enviados": 3,
                        "postulados": 1},
    )

    # Creamos 2 posts que califiquen para backlog_pendiente:
    # ANALIZADA, sin postulacion_id, sin email_body
    pid1 = _nuevo_post(40, estado="ANALIZADA", analisis={"match_score": 85}, triage={"puesto": "B1", "empresa": "EB1"})
    pid2 = _nuevo_post(41, estado="ANALIZADA", analisis={"match_score": 90}, triage={"puesto": "B2", "empresa": "EB2"})
    from tests.test_radar_workers import TEXTO_SUFICIENTE
    for pid in (pid1, pid2):
        radar_store.update_post(pid, texto=TEXTO_SUFICIENTE)

    res = client.get("/api/radar/procesos")
    assert res.status_code == 200
    data = res.json()

    topes = data["topes"]
    assert topes == {
        "leidos_busquedas": 120,
        "leidos_feed": 0,
        "enviados": 3,
        "tope_envios": 40,
        "postulados": 1,
        "ofertas": 14,
    }

    backlog = data["backlog"]
    assert backlog["activo"] is True
    assert backlog["quedan"] == 2
    assert len(backlog["items"]) == 2
    # Mayor match_score primero por orden_backlog
    assert backlog["items"][0]["post_id"] == pid2
    assert backlog["items"][1]["post_id"] == pid1


def test_procesos_tolera_progreso_roto(client, radar_stub):
    """progreso ilegible → 200 y progreso is None."""
    # Insertar un post con progreso ilegible directo en SQLite
    conn = radar_store._connect()
    try:
        conn.execute(
            "INSERT INTO linkedin_posts (id, urn, texto, query, fuente, estado, progreso) "
            "VALUES (999, 'urn:li:activity:999', 'Texto test', 'feed', 'feed', 'GENERANDO', '{roto: invalid json')"
        )
        conn.commit()
    finally:
        conn.close()

    radar_stub.generando_ahora = 999
    res = client.get("/api/radar/procesos")
    assert res.status_code == 200
    data = res.json()
    assert data["en_curso"][0]["post_id"] == 999
    assert data["en_curso"][0]["progreso"] is None


def test_cola_status_sin_actividad_ficticia(client, radar_stub):
    """Con GENERANDO en cola y nadie procesando: actual=None y eta_s=None."""
    pid1 = _nuevo_post(101, estado="GENERANDO", triage={"puesto": "Dev 1"})
    _nuevo_post(102, estado="GENERANDO", triage={"puesto": "Dev 2"})

    radar_stub.generando_ahora = None
    r1 = client.get("/api/radar/status")
    assert r1.status_code == 200
    cola1 = r1.json()["cola"]
    assert cola1["generando"] == 2
    assert cola1["actual"] is None
    assert cola1["eta_s"] is None

    # Cuando el worker efectivamente procesa pid1
    radar_stub.generando_ahora = pid1
    r2 = client.get("/api/radar/status")
    assert r2.status_code == 200
    cola2 = r2.json()["cola"]
    assert cola2["generando"] == 2
    assert cola2["actual"] is not None
    assert cola2["actual"]["post_id"] == pid1


def test_backlog_orden_mezclado_analizada_y_error(client, radar_stub):
    """Backlog mezcla ANALIZADA y ERROR ordenadas por match_score DESCENDENTE (las ERROR no van primero)."""
    from core.radar import orden_backlog

    pid_a1 = _nuevo_post(201, estado="ANALIZADA", analisis={"match_score": 80}, triage={"puesto": "A1"})
    pid_e1 = _nuevo_post(202, estado="ERROR", error="Falló la generación: timeout", intentos=1, analisis={"match_score": 95}, triage={"puesto": "E1"})
    pid_a2 = _nuevo_post(203, estado="ANALIZADA", analisis={"match_score": 85}, triage={"puesto": "A2"})
    pid_e2 = _nuevo_post(204, estado="ERROR", error="Falló la generación: cuota agotada", intentos=0, analisis={"match_score": 78}, triage={"puesto": "E2"})
    pid_sin_score = _nuevo_post(205, estado="ERROR", error="Falló la generación: err", intentos=0, analisis={"match_score": 76}, triage={"puesto": "SinScore"})
    pid_cerrada = _nuevo_post(206, estado="ANALIZADA", analisis={"match_score": 99, "oferta_cerrada": True}, triage={"puesto": "Cerrada"})
    from tests.test_radar_workers import TEXTO_SUFICIENTE
    for pid in (pid_a1, pid_e1, pid_a2, pid_e2, pid_sin_score, pid_cerrada):
        radar_store.update_post(pid, texto=TEXTO_SUFICIENTE)

    res = client.get("/api/radar/procesos")
    assert res.status_code == 200
    data = res.json()

    backlog = data["backlog"]
    items = backlog["items"]
    # Solo las cinco ofertas abiertas con captura suficiente son elegibles.
    assert len(items) == 5

    # Mismo contacto y rol: desempata el score; una cerrada nunca entra al backlog.
    orden_ids = [i["post_id"] for i in items]
    assert orden_ids == [pid_e1, pid_a2, pid_a1, pid_e2, pid_sin_score]
    assert pid_cerrada not in orden_ids
    assert items[0]["estado"] == "ERROR"
    assert items[1]["estado"] == "ANALIZADA"
    assert items[2]["estado"] == "ANALIZADA"
    assert items[3]["estado"] == "ERROR"

    # Coincide exactamente con el orden real que devuelve backlog_pendiente y orden_backlog
    pendientes = [p["id"] for p in radar_store.backlog_pendiente()]
    assert pendientes == [pid_e1, pid_a2, pid_a1, pid_e2, pid_sin_score]

    # El worker tomaría pid_e1 primero
    assert radar_store.tomar_backlog(pid_e1) is True
