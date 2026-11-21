"""Tests de Z3 (Feature radar-presupuesto): aviso de pausa de búsquedas en el panel.

Verifica:
- Clave `busquedas_pausa` en `/api/radar/status`.
- Endpoint `GET /api/radar/captura-bloqueo`.
- Función `textoPausaBusquedas` exportada en `window.Radar._util`.
- Elemento `#radarBusquedasPausadas` en `static/index.html`.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

import server
from core import paths, radar_store
from core.radar import RadarLoop
from tests.test_radar_ui import _node, requiere_node


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


def test_status_sin_pausa(client):
    r = client.get("/api/radar/status")
    assert r.status_code == 200
    data = r.json()
    assert "busquedas_pausa" in data
    assert data["busquedas_pausa"] is None


def test_status_con_pausa_y_captura(client, debug_dir):
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
    png_dir = debug_dir / "bloqueo"
    png_dir.mkdir(parents=True, exist_ok=True)
    png_file = png_dir / "screenshot.png"
    png_file.write_bytes(b"\x89PNG\r\n\x1a\nfake-screenshot")

    data = client.get("/api/radar/status").json()
    assert "busquedas_pausa" in data
    pausa = data["busquedas_pausa"]
    assert pausa is not None
    assert "hasta" in pausa
    assert pausa["horas"] == 6
    assert pausa["bloqueos_24h"] == 1
    assert pausa["captura"] == "/api/radar/captura-bloqueo"

    # sin el png -> captura is None
    png_file.unlink()
    data_sin = client.get("/api/radar/status").json()
    assert "busquedas_pausa" in data_sin
    pausa_sin = data_sin["busquedas_pausa"]
    assert pausa_sin is not None
    assert pausa_sin["captura"] is None
    assert pausa_sin["horas"] == 6
    assert pausa_sin["bloqueos_24h"] == 1


def test_captura_png_y_404(client, debug_dir):
    # Sin el png -> 404
    r_sin = client.get("/api/radar/captura-bloqueo")
    assert r_sin.status_code == 404

    # Con el png -> 200 e image/png
    png_dir = debug_dir / "bloqueo"
    png_dir.mkdir(parents=True, exist_ok=True)
    png_file = png_dir / "screenshot.png"
    fake_png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    png_file.write_bytes(fake_png)

    r_con = client.get("/api/radar/captura-bloqueo")
    assert r_con.status_code == 200
    assert "image/png" in r_con.headers.get("content-type", "")
    assert r_con.content == fake_png


@requiere_node
def test_texto_pausa():
    out = _node("""
const pad = (n) => String(n).padStart(2, '0');
const d = new Date();
const hasta = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T18:30:00`;
const fn = U.textoPausaBusquedas;
if (typeof fn !== 'function') {
  throw new Error("textoPausaBusquedas no está exportada en window.Radar._util");
}
console.log(JSON.stringify({
  nulo: fn(null),
  seis: fn({ hasta, horas: 6 }),
  doce: fn({ hasta, horas: 12 }),
}));
""")
    assert out["nulo"] == ""
    t6 = out["seis"]
    assert "LinkedIn pausó las búsquedas" in t6
    assert "18:30" in t6
    assert "El feed sigue" in t6
    assert "pausa de" not in t6
    t12 = out["doce"]
    assert "(pausa de 12 h)" in t12


def test_aviso_en_index():
    soup = BeautifulSoup(Path("static/index.html").read_text(encoding="utf-8"), "html.parser")
    vista = soup.find(id="vista-radar")
    assert vista is not None
    aviso = vista.find(id="radarBusquedasPausadas")
    assert aviso is not None, "Falta #radarBusquedasPausadas dentro de #vista-radar"
    assert aviso.has_attr("hidden"), "#radarBusquedasPausadas debe iniciar oculto con hidden"
    texto = aviso.find(id="radarBusquedasPausadasTexto")
    assert texto is not None, "Falta #radarBusquedasPausadasTexto dentro de #radarBusquedasPausadas"
    assert texto.name == "span"
    enlace = aviso.find(id="radarBusquedasPausadasCaptura")
    assert enlace is not None, "Falta #radarBusquedasPausadasCaptura dentro de #radarBusquedasPausadas"
    assert enlace.name == "a"
    assert enlace.get("href") == "/api/radar/captura-bloqueo"
    assert enlace.get("target") == "_blank"
    rel = enlace.get("rel")
    rel_tokens = rel if isinstance(rel, list) else (rel or "").split()
    assert "noopener" in rel_tokens
