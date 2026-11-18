"""GET /api/candidato/busquedas: las búsquedas configuradas y, solo si se midieron, sus resultados (12/11)."""
import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import core.paths
import server
from core import radar_resultados, radar_store


@pytest.fixture
def cliente(tmp_path, monkeypatch):
    monkeypatch.setattr(core.paths, "DATA_DIR", tmp_path)
    (tmp_path / "candidato").mkdir()

    def escribir(contenido):
        texto = contenido if isinstance(contenido, str) else json.dumps(contenido)
        (tmp_path / "candidato" / "busquedas.json").write_text(texto, encoding="utf-8")
    return TestClient(server.app, raise_server_exceptions=False), escribir


BUSQUEDAS = {"busquedas": [
    {"nombre": "Análisis", "activa": True, "consulta": '"cv a" funcional'},
    {"nombre": "Night", "activa": False, "consulta": '"night shift"'},
]}


def _lectura(query, parseados):
    ahora = datetime.now(timezone.utc).isoformat()
    radar_store.registrar_lectura(query=query, fuente="busqueda", inicio=ahora, fin=ahora, scrolls=1,
                                  motivo_fin="max_scrolls", pantallas=[], parseados=parseados, guardados=0,
                                  destinos={}, con_mail=1)


def test_con_datos_trae_los_promedios(cliente):
    client, escribir = cliente
    escribir(BUSQUEDAS)
    _lectura('"cv a" funcional', 10)
    _lectura('"cv a" funcional', 20)
    r = client.get("/api/candidato/busquedas")
    assert r.status_code == 200
    datos = r.json()
    assert datos["errores"] == []
    analisis, night = datos["busquedas"]
    assert (analisis["nombre"], analisis["activa"]) == ("Análisis", True) and "idioma" not in analisis
    assert analisis["consulta"] == '"cv a" funcional'
    assert analisis["resultados"]["lecturas"] == 2 and analisis["resultados"]["dias"] == 14
    assert analisis["resultados"]["promedio"]["leidos"] == 15.0
    assert analisis["resultados"]["promedio"]["con_mail"] == 1.0
    assert night["activa"] is False and "resultados" not in night


def test_sin_datos_no_aparece_la_clave(cliente):
    client, escribir = cliente
    escribir(BUSQUEDAS)
    _lectura("otra consulta", 10)
    datos = client.get("/api/candidato/busquedas").json()
    assert [b["nombre"] for b in datos["busquedas"]] == ["Análisis", "Night"]
    assert all("resultados" not in b for b in datos["busquedas"])


def test_sin_archivo_lista_vacia(cliente):
    client, _ = cliente
    datos = client.get("/api/candidato/busquedas").json()
    # Portales (14/11): también los portales que se pueden marcar y la versión del archivo (vacío: la de b"")
    from core.reglas_portales import PORTALES
    assert datos == {"busquedas": [], "errores": [], "portales": list(PORTALES),
                     "version": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"}


def test_archivo_roto_o_busqueda_invalida_avisan(cliente):
    client, escribir = cliente
    escribir("{no es json")
    datos = client.get("/api/candidato/busquedas").json()
    assert datos["busquedas"] == [] and len(datos["errores"]) == 1
    escribir({"busquedas": [BUSQUEDAS["busquedas"][0], {"nombre": "Mal", "activa": True,
                                                         "consulta": "a or b"}]})
    datos = client.get("/api/candidato/busquedas").json()
    assert [b["nombre"] for b in datos["busquedas"]] == ["Análisis"]
    assert any("«Mal»" in e for e in datos["errores"])


def test_si_la_base_falla_no_inventa_numeros(cliente, monkeypatch):
    client, escribir = cliente
    escribir(BUSQUEDAS)
    _lectura('"cv a" funcional', 10)

    def falla(*a, **k):
        raise RuntimeError("base caída")
    monkeypatch.setattr(radar_resultados, "resultados_por_consulta", falla)
    datos = client.get("/api/candidato/busquedas").json()
    assert len(datos["busquedas"]) == 2 and all("resultados" not in b for b in datos["busquedas"])
