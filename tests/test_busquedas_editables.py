"""Rediseño E6: las búsquedas del Radar se editan desde Mi perfil.

PUT /api/candidato/busquedas reemplaza la lista «busquedas» de busquedas.json: con la versión del archivo (409 si
cambió), un respaldo antes de escribir y la misma validación que usa el Radar (422 con los errores en castellano).
Las demás claves del archivo y de cada búsqueda quedan. GET suma «crudas»: la lista tal cual está en el archivo
(también las búsquedas con errores, para poder corregirlas).

Medidas: mismas búsquedas leídas antes y después de un guardado sin cambios; lo editado es lo que lee el Radar;
nada se escribe si la versión es vieja o el contenido es inválido; siempre hay respaldo.
"""
import json
import shutil

import pytest
from fastapi.testclient import TestClient

import core.paths
import server
from core import radar_consultas

ARCHIVO = {"nota": "conservar", "busquedas": [
    {"nombre": "Frontend", "activa": True, "bloques": [
        [{"texto": "buscamos", "exacta": True}, {"texto": "estamos buscando", "exacta": True}],
        [{"texto": "react"}, {"texto": "frontend"}],
        [{"texto": "pasantía", "excluir": True}]],
     "portales": ["jooble"], "palabras": "desarrolladora frontend", "extra": "fila"},
    {"nombre": "Backend", "activa": False, "consulta": "\"buscamos\" node"},
    {"nombre": "Rota", "activa": True, "bloques": [[]]},
]}


@pytest.fixture(autouse=True)
def perfil(tmp_path, monkeypatch):
    candidato = tmp_path / "datos" / "candidato"
    shutil.copytree(core.paths.DATA_DIR / "candidato", candidato)
    monkeypatch.setattr(core.paths, "DATA_DIR", tmp_path / "datos")
    (candidato / "busquedas.json").write_text(json.dumps(ARCHIVO, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return candidato


@pytest.fixture
def cliente():
    return TestClient(server.app)


def _leer(perfil):
    return json.loads((perfil / "busquedas.json").read_text(encoding="utf-8"))


def test_get_trae_las_crudas_en_orden_tambien_las_que_tienen_errores(cliente):
    dato = cliente.get("/api/candidato/busquedas").json()
    assert dato["crudas"] == ARCHIVO["busquedas"]
    assert [b["nombre"] for b in dato["busquedas"]] == ["Frontend", "Backend"]  # «Rota» no la lee el Radar
    assert any("Rota" in e for e in dato["errores"])


def test_guardar_sin_cambios_deja_las_mismas_busquedas(cliente, perfil):
    antes = cliente.get("/api/candidato/busquedas").json()
    consultas_antes = radar_consultas.cargar_busquedas()
    portales_antes = radar_consultas.portales_de_busquedas()
    r = cliente.put("/api/candidato/busquedas", json={"busquedas": antes["crudas"], "version": antes["version"]})
    # «Rota» es inválida: un guardado sin cambios la rechaza igual que antes (no se escribe nada)
    assert r.status_code == 422 and "Rota" in r.json()["detail"]
    sin_rota = [b for b in antes["crudas"] if b["nombre"] != "Rota"]
    r = cliente.put("/api/candidato/busquedas", json={"busquedas": sin_rota, "version": antes["version"]})
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert cuerpo["ok"] is True and cuerpo["backup"] and cuerpo["version"] != antes["version"]
    assert radar_consultas.cargar_busquedas() == consultas_antes
    assert radar_consultas.portales_de_busquedas() == {k: v for k, v in portales_antes.items() if k != "Rota"}
    assert _leer(perfil)["nota"] == "conservar" and _leer(perfil)["busquedas"][0]["extra"] == "fila"
    despues = cliente.get("/api/candidato/busquedas").json()
    assert despues["version"] == cuerpo["version"] and despues["crudas"] == sin_rota


def test_lo_editado_es_lo_que_lee_el_radar(cliente, perfil):
    dato = cliente.get("/api/candidato/busquedas").json()
    frontend, backend = dato["crudas"][0], dato["crudas"][1]
    nueva = {"nombre": "Diseño UX", "activa": True, "bloques": [[{"texto": "diseñadora ux"}, {"texto": "ux writer", "exacta": True}]]}
    frontend = {**frontend, "nombre": "Front", "bloques": frontend["bloques"][:2]}
    backend = {**backend, "activa": True}
    r = cliente.put("/api/candidato/busquedas", json={"busquedas": [nueva, backend, frontend], "version": dato["version"]})
    assert r.status_code == 200, r.text
    leidas = radar_consultas.cargar_busquedas()
    assert [b["nombre"] for b in leidas] == ["Diseño UX", "Backend", "Front"]
    assert leidas[0]["consulta"] == '((diseñadora AND ux) OR "ux writer")'
    assert leidas[1]["activa"] is True
    assert leidas[2]["consulta"] == '("buscamos" OR "estamos buscando") (react OR frontend)'
    assert _leer(perfil)["busquedas"][2]["portales"] == ["jooble"]


def test_version_vieja_409_y_no_escribe(cliente, perfil):
    antes = (perfil / "busquedas.json").read_bytes()
    r = cliente.put("/api/candidato/busquedas", json={"busquedas": [], "version": "vieja"})
    assert r.status_code == 409
    assert (perfil / "busquedas.json").read_bytes() == antes


@pytest.mark.parametrize("busquedas, pedazo", [
    ([{"nombre": "A", "activa": True, "bloques": [[]]}], "bloque 1 está vacío"),
    ([{"nombre": "A", "activa": True, "consulta": "x"}, {"nombre": "a", "activa": True, "consulta": "y"}], "repetido"),
    ([{"nombre": "A", "activa": True, "bloques": [[{"texto": t} for t in "abcdefg"]]}], "operadores"),
    ([{"nombre": "A", "activa": "sí", "consulta": "x"}], "«activa» tiene que ser true o false"),
    ([{"nombre": "A", "activa": True, "consulta": "x", "portales": ["jooble"], "palabras": ""}], "faltan las palabras"),
])
def test_contenido_invalido_422_con_el_error_y_no_escribe(cliente, perfil, busquedas, pedazo):
    antes = (perfil / "busquedas.json").read_bytes()
    version = cliente.get("/api/candidato/busquedas").json()["version"]
    r = cliente.put("/api/candidato/busquedas", json={"busquedas": busquedas, "version": version})
    assert r.status_code == 422 and pedazo in r.json()["detail"], r.text
    assert (perfil / "busquedas.json").read_bytes() == antes


def test_sin_archivo_se_crea_con_la_version_de_vacio(cliente, perfil):
    (perfil / "busquedas.json").unlink()
    dato = cliente.get("/api/candidato/busquedas").json()
    assert dato["crudas"] == []
    nueva = [{"nombre": "Primera", "activa": True, "bloques": [[{"texto": "buscamos", "exacta": True}]]}]
    r = cliente.put("/api/candidato/busquedas", json={"busquedas": nueva, "version": dato["version"]})
    assert r.status_code == 200, r.text
    assert _leer(perfil) == {"busquedas": nueva}
    assert radar_consultas.cargar_busquedas() == [{"nombre": "Primera", "activa": True, "consulta": '"buscamos"'}]


def test_la_lista_tiene_que_ser_una_lista(cliente):
    version = cliente.get("/api/candidato/busquedas").json()["version"]
    assert cliente.put("/api/candidato/busquedas", json={"busquedas": {"a": 1}, "version": version}).status_code == 422
