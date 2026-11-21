"""API de los editores nuevos de Mi perfil: stack, roles objetivo, conocimientos ausentes y zona.

Mismo contrato que los demás: validación igual a la del motor, 409 con versión vieja y respaldo antes de guardar.
"""
import hashlib
import json

import pytest
from fastapi.testclient import TestClient

import server
from core import candidato_datos, descartes, paths

STACK = {"nota": "conservar", "coincidencias": [
    {"nombre": "Cobol", "palabras": ["cobol"], "justificacion": "Lotes nocturnos en Cobol"}],
    "transferibles": [], "habilidades_base": {"gestion": [], "tecnicas": ["Cobol"]}}
ROLES = {"roles_objetivo": ["domador de leones"],
         "areas": [{"area": "circo", "titulos": ["domador"], "perfil_actual": True}]}
AUSENTES = {"conocimientos": ["Trapecio"]}
VALIDOS = {"stack": STACK, "roles_objetivo": ROLES, "conocimientos_ausentes": AUSENTES}
ARCHIVOS = {"stack": "stack.json", "roles_objetivo": "roles_objetivo.json",
            "conocimientos_ausentes": "conocimientos_ausentes.json"}


@pytest.fixture
def entorno(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    base = tmp_path / "candidato"
    base.mkdir()
    (base / "personales.txt").write_text("Ana Prueba\nPuesto: Domadora de Leones\n", encoding="utf-8")
    client = TestClient(server.app, raise_server_exceptions=False)
    yield client, base, tmp_path / "backups"
    client.close()


def guardar(client, clave, contenido, version=None):
    if version is None:
        version = client.get(f"/api/candidato/perfil/{clave}").json()["version"]
    return client.put(f"/api/candidato/perfil/{clave}", json={"contenido": contenido, "version": version})


@pytest.mark.parametrize("clave", ARCHIVOS)
def test_ausente_se_crea_con_respaldo(entorno, clave):
    client, base, backups = entorno
    lectura = client.get(f"/api/candidato/perfil/{clave}").json()
    assert lectura == {"clave": clave, "archivo": ARCHIVOS[clave], "contenido": "",
                       "version": hashlib.sha256(b"").hexdigest(), "existe": False}
    contenido = json.dumps(VALIDOS[clave], ensure_ascii=False)
    r = guardar(client, clave, contenido)
    assert r.status_code == 200, r.text
    assert (base / ARCHIVOS[clave]).read_text(encoding="utf-8") == contenido
    assert (backups / r.json()["backup"] / "personales.txt").exists()


def test_el_motor_lee_lo_guardado(entorno):
    client, _, _ = entorno
    assert guardar(client, "stack", json.dumps(STACK)).status_code == 200
    assert guardar(client, "roles_objetivo", json.dumps(ROLES)).status_code == 200
    assert candidato_datos.stack()["coincidencias"][0]["justificacion"] == "Lotes nocturnos en Cobol"
    assert candidato_datos.roles()["roles_objetivo"] == ["domador de leones"]


@pytest.mark.parametrize("clave, contenido, fragmento", [
    ("stack", "{", "No es un JSON válido"),
    ("stack", json.dumps({"coincidencias": [{"nombre": "X", "palabras": []}]}), "Stack inválido"),
    ("roles_objetivo", json.dumps({"roles_objetivo": "domador"}), "Roles objetivo inválidos"),
    ("roles_objetivo", json.dumps({"areas": [{"area": "a", "titulos": ["t"], "sinonimos": ["(x"]}]}), "sinónimo"),
    ("conocimientos_ausentes", json.dumps({"conocimientos": ["a", "A"]}), "repetido"),
    ("conocimientos_ausentes", json.dumps(["Trapecio"]), "Conocimientos ausentes inválidos"),
])
def test_invalido_422_sin_tocar_nada(entorno, clave, contenido, fragmento):
    client, base, backups = entorno
    r = guardar(client, clave, contenido)
    assert r.status_code == 422 and fragmento in r.json()["detail"]
    assert not (base / ARCHIVOS[clave]).exists()
    assert not backups.exists()


@pytest.mark.parametrize("clave", ARCHIVOS)
def test_version_vieja_409(entorno, clave):
    client, base, backups = entorno
    vieja = client.get(f"/api/candidato/perfil/{clave}").json()["version"]
    assert guardar(client, clave, json.dumps(VALIDOS[clave])).status_code == 200
    r = guardar(client, clave, json.dumps(VALIDOS[clave]), version=vieja)
    assert r.status_code == 409
    assert len(list(backups.iterdir())) == 1


def test_localidades_en_restricciones(entorno):
    client, base, _ = entorno
    r = guardar(client, "restricciones", json.dumps({"localidades_permitidas": ["Pueblo Ejemplo"]}))
    assert r.status_code == 200, r.text
    assert descartes._validas(json.loads((base / "restricciones.json").read_text(encoding="utf-8")))
    r = guardar(client, "restricciones", json.dumps({"localidades_permitidas": "Pueblo Ejemplo"}))
    assert r.status_code == 422
