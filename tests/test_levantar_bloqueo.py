"""Tests de contrato de store y API para la feature levantar-bloqueo.

Verifica:
- radar_store.levantar_bloqueo()
- POST /api/radar/levantar-bloqueo
- Invariante de presupuesto tras levantar bloqueo
"""
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import server
from core import paths, radar_store
from core.radar import RadarLoop
from tests.test_radar_loop import make_radar


@pytest.fixture
def debug_dir(tmp_path, monkeypatch):
    d = tmp_path / "radar_debug"
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path, raising=False)
    monkeypatch.setattr("core.radar_api.DATA_DIR", tmp_path, raising=False)
    return d


@pytest.fixture
def radar(monkeypatch, debug_dir):
    stub = AsyncMock(spec=RadarLoop)
    stub._debug_dir = debug_dir
    stub.debug_dir = debug_dir
    monkeypatch.setattr(server, "radar_loop", stub)
    return stub


@pytest.fixture
def client(radar):
    return TestClient(server.app)


def _registrar(query="q", fuente="busqueda", motivo="bloqueo", min_atras=10):
    t = (datetime.now(timezone.utc) - timedelta(minutes=min_atras)).isoformat()
    return radar_store.registrar_lectura(
        query=query,
        fuente=fuente,
        inicio=t,
        fin=t,
        scrolls=0,
        motivo_fin=motivo,
        pantallas=[],
        parseados=0,
        guardados=0,
        destinos={},
    )


def test_store_levanta_solo_bloqueos_de_busqueda():
    # 3 lecturas de búsqueda bloqueo
    for i in range(3):
        _registrar(query=f"b_{i}", fuente="busqueda", motivo="bloqueo", min_atras=10 * (i + 1))
    # 1 de búsqueda max_posts
    _registrar(query="b_max", fuente="busqueda", motivo="max_posts", min_atras=40)
    # 1 de fuente="feed" con motivo_fin="bloqueo"
    _registrar(query="feed", fuente="feed", motivo="bloqueo", min_atras=50)

    total_antes = len(radar_store.listar_lecturas(limite=50))
    assert total_antes == 5

    levantadas = radar_store.levantar_bloqueo()
    assert levantadas == 3

    lecturas = radar_store.listar_lecturas(limite=50)
    assert len(lecturas) == 5

    bloqueo_levantado = [l for l in lecturas if l["fuente"] != "feed" and l["motivo_fin"] == "bloqueo_levantado"]
    assert len(bloqueo_levantado) == 3

    busqueda_max = [l for l in lecturas if l["fuente"] != "feed" and l["motivo_fin"] == "max_posts"]
    assert len(busqueda_max) == 1

    feed_bloqueo = [l for l in lecturas if l["fuente"] == "feed" and l["motivo_fin"] == "bloqueo"]
    assert len(feed_bloqueo) == 1


def test_store_sin_bloqueos_devuelve_cero():
    _registrar(query="b_max", fuente="busqueda", motivo="max_posts", min_atras=10)
    _registrar(query="feed", fuente="feed", motivo="bloqueo", min_atras=20)
    assert radar_store.levantar_bloqueo() == 0


def test_api_levanta_y_quita_la_pausa(client, debug_dir):
    inicio_1h = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    radar_store.registrar_lectura(
        query='"cv a"',
        fuente="busqueda",
        inicio=inicio_1h,
        fin=inicio_1h,
        scrolls=0,
        motivo_fin="bloqueo",
        pantallas=[],
        parseados=0,
        guardados=0,
        destinos={},
    )
    # Con un bloqueo de búsqueda hace 1 h, /api/radar/status tiene busquedas_pausa no nulo
    data_antes = client.get("/api/radar/status").json()
    assert data_antes.get("busquedas_pausa") is not None

    # Después de POST /api/radar/levantar-bloqueo
    r = client.post("/api/radar/levantar-bloqueo")
    assert r.status_code == 200
    res = r.json()
    assert res.get("levantadas") == 1
    assert res.get("busquedas_pausa") is None

    # Y /status también da None
    data_despues = client.get("/api/radar/status").json()
    assert data_despues.get("busquedas_pausa") is None


@pytest.mark.presupuesto_real
def test_presupuesto_no_cambia(tmp_path):
    now_local = datetime.combine(date.today(), dtime(12, 0, 0))
    radar = make_radar(tmp_path, now=lambda: now_local)
    ahora_utc = now_local.astimezone(timezone.utc)  # el reloj del Radar, no la hora real del test
    for i in range(6):
        inicio_dt = ahora_utc - timedelta(minutes=5 * (i + 1))
        motivo = "bloqueo" if i < 2 else "max_posts"
        radar_store.registrar_lectura(
            query=f"q{i}",
            fuente="busqueda",
            inicio=inicio_dt.isoformat(),
            fin=inicio_dt.isoformat(),
            scrolls=1,
            motivo_fin=motivo,
            pantallas=[],
            parseados=1,
            guardados=1,
            destinos={},
        )
    assert radar._presupuesto_busquedas() == 0
    radar_store.levantar_bloqueo()
    assert radar._presupuesto_busquedas() == 0
