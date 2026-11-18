"""Contrato HTTP del perfil con archivos sintéticos y sin lifespan ni proveedores."""
import hashlib
import json
import os
from datetime import date, timedelta
from urllib.parse import quote
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

import server
from core import acceso_remoto, aprendizajes, paths, perfil_areas, perfil_candidato


ARCHIVOS = {
    "experiencia": "experiencia.txt", "personales": "personales.txt",
    "restricciones": "restricciones.json",
    "anios_por_area_creador": "anios_por_area_creador.json", "voz_email": "voz_email.md",
}
AVISO = "Los años por área se recalculan con IA en el próximo CV."
CONFLICTO = "El archivo cambió desde que lo abriste. Recargá antes de guardar."
BASE = "/api/candidato/perfil/"


def huella(contenido):
    return hashlib.sha256(contenido).hexdigest()


def estado(raiz):
    return {p.relative_to(raiz).as_posix(): p.read_bytes()
            for p in raiz.rglob("*") if p.is_file()}


@pytest.fixture
def entorno(tmp_path, monkeypatch):
    candidato = tmp_path / "candidato"
    candidato.mkdir()
    datos = {
        "experiencia.txt": "## Empresa Test\r\n### Analista | 01/2018 - 01/2025\r\n",
        "personales.txt": "Nombre: Persona Test, ñ\r\n",
        "restricciones.json": '{ "reubicacion": false, "extra": {"ñ": 1} }\r\n',
        "anios_por_area_creador.json": '{"anios_por_area": []}',
        "voz_email.md": "  Hola, mañana.\r\n\r\n",
        "aprendizajes_candidato.json": '{"docker": {"estado": "TRANSFERIBLE"}}',
        "otro.bin": "otro archivo",
    }
    for nombre, texto in datos.items():
        (candidato / nombre).write_bytes(texto.encode("utf-8"))
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    monkeypatch.setattr(aprendizajes, "APRENDIZAJES_FILE", candidato / "aprendizajes_candidato.json")
    bridge = Mock(side_effect=AssertionError("El perfil no debe invocar IA"))
    monkeypatch.setattr(perfil_areas, "AntigravityBridge", bridge)
    monkeypatch.setattr(server, "AntigravityBridge", bridge)
    client = TestClient(server.app, raise_server_exceptions=False)
    # invariante: wrapper actual de aprendizajes, leído desde el temporal explícito.
    response = client.get("/api/candidato/aprendizajes")
    assert response.status_code == 200
    assert response.json() == {"aprendizajes": {"docker": {"estado": "TRANSFERIBLE"}}}
    yield client, candidato, bridge
    bridge.assert_not_called()
    client.close()


def test_get_aprendizajes_invariante(entorno):
    client, candidato, _ = entorno
    # invariante: GET no modifica datos y conserva el wrapper exacto.
    antes = estado(candidato.parent)
    assert client.get("/api/candidato/aprendizajes").json() == {
        "aprendizajes": {"docker": {"estado": "TRANSFERIBLE"}}}
    assert estado(candidato.parent) == antes
    if os.environ.get("INVARIANT_CHECK") == "1":
        return


@pytest.mark.parametrize("clave,archivo", ARCHIVOS.items())
def test_get_cinco_claves(entorno, clave, archivo):
    client, candidato, _ = entorno
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    antes = estado(candidato.parent)
    contenido = (candidato / archivo).read_bytes()
    response = client.get(BASE + clave)
    assert response.status_code == 200
    assert response.json() == {"clave": clave, "archivo": archivo,
                               "contenido": contenido.decode("utf-8"),
                               "version": huella(contenido), "existe": True}
    assert estado(candidato.parent) == antes
    (candidato / archivo).unlink()
    assert client.get(BASE + clave).json() == {"clave": clave, "archivo": archivo,
        "contenido": "", "version": huella(b""), "existe": False}
    for method in (client.get, client.put):
        kwargs = {"json": {"contenido": "nuevo", "version": huella(b"")}} if method == client.put else {}
        assert method(BASE + "desconocida", **kwargs).status_code == 404


@pytest.mark.parametrize("clave,contenido", [
    ("personales", "  Nuevo: ñ\r\n\r\n"),
    ("restricciones", ' { "reubicacion": true, "extra": {"ñ": 2} }\r\n'),
    ("anios_por_area_creador", '[ {"area":"soporte", "anios":"7"} ]\r\n'),
    ("voz_email", "ñ" * 200000),
], ids=["personales", "restricciones", "areas", "limite-texto"])
def test_put_y_backup(entorno, monkeypatch, clave, contenido):
    client, candidato, _ = entorno
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    antes = estado(candidato)
    destino = candidato / ARCHIVOS[clave]
    replace = os.replace
    observados = []

    def comprobar_backup(origen, target, *args, **kwargs):
        if target == destino:
            copias = list((candidato.parent / "backups").iterdir())
            assert len(copias) == 1
            assert estado(copias[0]) == antes
            assert destino.read_bytes() == antes[destino.name]
            observados.append(True)
        return replace(origen, target, *args, **kwargs)

    monkeypatch.setattr(os, "replace", comprobar_backup)
    response = client.put(BASE + clave, json={"contenido": contenido,
                                               "version": huella(antes[destino.name])})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data == {"ok": True, "version": huella(contenido.encode("utf-8")),
                    "backup": data["backup"], "aviso": AVISO if clave == "personales" else None}
    assert data["backup"].startswith("candidato-")
    assert estado(candidato.parent / "backups" / data["backup"]) == antes
    assert observados == [True]
    assert estado(candidato) == {**antes, destino.name: contenido.encode("utf-8")}


INVALIDOS = [
    ("personales", " ", None), ("voz_email", "ñ" * 200001, None),
    ("restricciones", "{roto", "No es un JSON válido: "),
    ("restricciones", '{"reubicacion":"no"}', "Restricciones inválidas"),
    ("restricciones", json.dumps({"edad": {"actual": 36, "al": str(date.today() + timedelta(days=1))}}),
     "Restricciones inválidas"),
    *[("anios_por_area_creador", json.dumps(valor), "Área inválida") for valor in (
        [{"area": "soporte", "anios": "siete"}], [{"area": "soporte", "anios": "8"}],
        [{"area": "soporte", "anios": "1"}, {"area": "SOPORTE", "anios": "2"}],
        [{"area": f"área {i}", "anios": "1"} for i in range(9)],
    )],
]


def test_put_409_422_sin_efectos(entorno, monkeypatch):
    client, candidato, _ = entorno
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    antes = estado(candidato.parent)
    def prohibir_backup(*args):
        raise AssertionError("Un rechazo no puede crear backup")
    monkeypatch.setattr(perfil_candidato, "crear_backup", prohibir_backup)
    response = client.put(BASE + "personales", json={"contenido": "nuevo", "version": "vieja"})
    assert response.status_code == 409
    assert response.json() == {"detail": CONFLICTO}
    for clave, contenido, prefijo in INVALIDOS:
        version = huella((candidato / ARCHIVOS[clave]).read_bytes())
        response = client.put(BASE + clave, json={"contenido": contenido, "version": version})
        assert response.status_code == 422, response.text
        assert isinstance(response.json()["detail"], str)
        if prefijo:
            assert response.json()["detail"].startswith(prefijo)
    version = huella((candidato / "personales.txt").read_bytes())
    for body in ({}, {"contenido": 1, "version": version}, {"contenido": "nuevo", "version": 1},
                 {"contenido": None, "version": version}, {"contenido": [], "version": version}):
        assert client.put(BASE + "personales", json=body).status_code == 422
    assert estado(candidato.parent) == antes


@pytest.mark.parametrize("clave", ["experiencia", "personales", "voz_email"])
def test_aviso(entorno, clave):
    client, candidato, _ = entorno
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    original = (candidato / ARCHIVOS[clave]).read_bytes()
    for contenido, aviso in ((original.decode("utf-8"), None),
                             ("Cambio de perfil\r\n", AVISO if clave != "voz_email" else None)):
        response = client.put(BASE + clave, json={"contenido": contenido, "version": huella(original)})
        assert response.status_code == 200
        assert response.json()["aviso"] == aviso


def test_preview_cache_y_override(entorno):
    client, candidato, bridge = entorno
    # invariante: el lector existente no tiene caché y no usa IA.
    assert perfil_areas.anios_por_area_en_cache(candidato) == []
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    antes = estado(candidato.parent)
    assert client.get("/api/candidato/anios-por-area").json() == {"areas": [], "vigente": False}
    assert estado(candidato.parent) == antes
    cache = {"hash": huella(b"\0".join([perfil_areas.VERSION_CRITERIO,
        (candidato / "experiencia.txt").read_bytes(), (candidato / "personales.txt").read_bytes()])),
        "anios_por_area": [{"area": "soporte técnico", "anios": "2"}]}
    (candidato / "anios_por_area.json").write_bytes(json.dumps(cache).encode())
    (candidato / "anios_por_area_creador.json").write_bytes(json.dumps({"anios_por_area": [
        {"area": "Soporte", "anios": "7", "reemplaza": ["soporte"]}]}).encode())
    antes = estado(candidato.parent)
    response = client.get("/api/candidato/anios-por-area")
    assert response.status_code == 200
    assert response.json() == {"areas": [{"area": "Soporte", "anios": "7"}], "vigente": True}
    assert estado(candidato.parent) == antes
    (candidato / "personales.txt").write_bytes(b"otro perfil")
    assert client.get("/api/candidato/anios-por-area").json() == {"areas": [], "vigente": False}
    bridge.assert_not_called()


@pytest.mark.parametrize("clave", ["docker", "ci/cd", "100%", "¿qué?", "con espacio", "inglés"])
def test_delete_aprendizaje(entorno, clave):
    client, candidato, _ = entorno
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    datos = {clave: {"estado": "TRANSFERIBLE"}, "conservar": {"estado": "COINCIDENCIA"}}
    aprendizajes.APRENDIZAJES_FILE.write_bytes(json.dumps(datos).encode())
    antes = estado(candidato)
    response = client.delete("/api/candidato/aprendizajes/" + quote(clave, safe=""))
    assert response.status_code == 200, response.text
    resultado = response.json()
    assert resultado == {"ok": True, "backup": resultado["backup"]}
    # Solo el archivo de aprendizajes, no toda su carpeta (S1)
    nombre = aprendizajes.APRENDIZAJES_FILE.name
    assert estado(candidato.parent / "backups" / resultado["backup"]) == {nombre: antes[nombre]}
    assert client.get("/api/candidato/aprendizajes").json() == {"aprendizajes": {"conservar": datos["conservar"]}}
    antes = estado(candidato.parent)
    assert client.delete("/api/candidato/aprendizajes/" + quote(clave, safe="")).status_code == 404
    assert estado(candidato.parent) == antes


def test_ruta_cambia_en_runtime(entorno, monkeypatch, tmp_path):
    client, candidato, _ = entorno
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    assert client.get(BASE + "personales").json()["contenido"].startswith("Nombre:")
    otra = tmp_path / "otra" / "candidato"
    otra.mkdir(parents=True)
    (otra / "personales.txt").write_bytes(b"otro\r\n")
    (otra / "aprendizajes.json").write_bytes(b'{"otro": {}}')
    monkeypatch.setattr(paths, "DATA_DIR", otra.parent)
    monkeypatch.setattr(aprendizajes, "APRENDIZAJES_FILE", otra / "aprendizajes.json")
    assert client.get(BASE + "personales").json()["contenido"] == "otro\r\n"
    response = client.put(BASE + "personales", json={"contenido": "nuevo", "version": huella(b"otro\r\n")})
    assert response.status_code == 200
    assert (otra.parent / "backups" / response.json()["backup"] / "personales.txt").read_bytes() == b"otro\r\n"
    assert client.delete("/api/candidato/aprendizajes/otro").status_code == 200
    assert json.loads((candidato / "aprendizajes_candidato.json").read_bytes()) == {"docker": {"estado": "TRANSFERIBLE"}}


@pytest.mark.parametrize("headers", [{"host": "remoto.example"}, {"origin": "https://ajeno.example"},
                                    {"cf-ray": "tunnel"}, {"sec-fetch-site": "cross-site"}])
def test_mutaciones_guard_local(entorno, headers):
    client, candidato, _ = entorno
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    antes = estado(candidato.parent)
    response = client.put(BASE + "personales", headers=headers,
        json={"contenido": "nuevo", "version": huella((candidato / "personales.txt").read_bytes())})
    assert response.status_code == 403
    assert client.delete("/api/candidato/aprendizajes/docker", headers=headers).status_code == 403
    assert estado(candidato.parent) == antes


def test_fallo_io_es_500(entorno, monkeypatch):
    client, candidato, _ = entorno
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    antes = estado(candidato.parent)
    def fallar(*args):
        raise OSError("respaldo inaccesible")
    monkeypatch.setattr(perfil_candidato, "crear_backup", fallar)
    assert client.put(BASE + "personales", json={"contenido": "nuevo",
        "version": huella((candidato / "personales.txt").read_bytes())}).status_code == 500
    assert client.delete("/api/candidato/aprendizajes/docker").status_code == 500
    assert estado(candidato.parent) == antes


@pytest.mark.parametrize("emparejado", [False, True])
def test_cliente_remoto(entorno, monkeypatch, emparejado):
    _, candidato, _ = entorno
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    secreto = candidato.parent / "acceso.key"
    secreto.write_bytes(b"clave-sintetica")
    monkeypatch.setattr(acceso_remoto, "ARCHIVO_CLAVE", secreto)
    client = TestClient(server.app, raise_server_exceptions=False,
                        client=("198.51.100.2", 12345), base_url="https://portal.example")
    if emparejado:
        client.cookies.set(acceso_remoto.COOKIE, "clave-sintetica")
    antes = estado(candidato.parent)
    headers = {"origin": "https://portal.example", "cf-ray": "tunnel"}
    response = client.put(BASE + "personales", headers=headers,
        json={"contenido": "nuevo", "version": huella((candidato / "personales.txt").read_bytes())})
    assert response.status_code == (200 if emparejado else 403)
    assert client.delete("/api/candidato/aprendizajes/docker", headers=headers).status_code == (
        200 if emparejado else 403)
    if not emparejado:
        assert estado(candidato.parent) == antes
    else:
        # Una cookie válida tampoco habilita solicitudes de otro origen.
        antes = estado(candidato.parent)
        assert client.delete("/api/candidato/aprendizajes/inexistente",
                             headers={"origin": "https://ajeno.example"}).status_code == 403
        assert estado(candidato.parent) == antes
    client.close()
