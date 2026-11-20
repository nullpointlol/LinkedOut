"""Aceptación HTTP con perfil temporal; nunca arranca el lifespan ni un proveedor."""
import hashlib
import json
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

import server
from core import aprendizajes, paths, perfil_areas

ARCHIVOS = {
    "experiencia": "experiencia.txt", "personales": "personales.txt",
    "restricciones": "restricciones.json",
    "anios_por_area_creador": "anios_por_area_creador.json", "voz_email": "voz_email.md",
}
AVISO = "Los años por área se recalculan con IA en el próximo CV."
CONFLICTO = "El archivo cambió desde que lo abriste. Recargá antes de guardar."


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def entorno(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    base = tmp_path / "candidato"
    base.mkdir()
    datos = {
        "experiencia.txt": "## Empresa\n### Soporte | 01/2018 - 01/2025\nIncidentes.\n",
        "personales.txt": "Nombre: Ana Prueba\n", "voz_email.md": "Hola, ¿cómo estás?\r\n",
        "restricciones.json": '{"base":"AMBA","reubicacion":false}',
        "anios_por_area_creador.json": '{"nota":"conservar","anios_por_area":[{"area":"Soporte","anios":"6"}]}',
        "aprendizajes_candidato.json": '{"sql":{"tag":"SQL","estado":"TRANSFERIBLE"},"python":{"tag":"Python","estado":"MATCH_DIRECTO"}}',
        "competencias_expandidas.json": '{"no":"editar"}',
    }
    for nombre, contenido in datos.items():
        (base / nombre).write_bytes(contenido.encode("utf-8"))
    (base / "subcarpeta").mkdir()
    (base / "subcarpeta" / "ignorar.txt").write_bytes(b"no copiar")
    monkeypatch.setattr(aprendizajes, "APRENDIZAJES_FILE", base / "aprendizajes_candidato.json")
    prohibido = Mock(side_effect=AssertionError("No llamar a la IA"))
    monkeypatch.setattr(perfil_areas, "AntigravityBridge", prohibido)
    client = TestClient(server.app, raise_server_exceptions=False)
    yield client, base, tmp_path / "backups"
    prohibido.assert_not_called()
    client.close()


def foto(base):
    return {p.relative_to(base).as_posix(): p.read_bytes() for p in base.rglob("*") if p.is_file()}


def guardar(client, clave, contenido):
    lectura = client.get(f"/api/candidato/perfil/{clave}")
    assert lectura.status_code == 200, lectura.text
    return client.put(f"/api/candidato/perfil/{clave}", json={
        "contenido": contenido, "version": lectura.json()["version"]})


@pytest.mark.parametrize("clave", ARCHIVOS)
def test_get_exacto_y_ausente(entorno, clave):
    client, base, _ = entorno
    archivo = base / ARCHIVOS[clave]
    raw = archivo.read_bytes()
    respuesta = client.get(f"/api/candidato/perfil/{clave}")
    assert respuesta.status_code == 200
    assert respuesta.json() == dict(clave=clave, archivo=archivo.name,
                                   contenido=raw.decode("utf-8"), version=sha(raw), existe=True)
    archivo.unlink()
    assert client.get(f"/api/candidato/perfil/{clave}").json() == dict(
        clave=clave, archivo=archivo.name, contenido="", version=sha(b""), existe=False)


def test_clave_desconocida(entorno):
    client, base, backups = entorno
    assert client.get("/api/candidato/perfil/personales").status_code == 200
    anterior = foto(base)
    assert client.get("/api/candidato/perfil/no-existe").status_code == 404
    assert client.put("/api/candidato/perfil/no-existe", json={"contenido": "x", "version": sha(b"")}).status_code == 404
    assert foto(base) == anterior and not backups.exists()


@pytest.mark.parametrize("clave,nuevo", [
    ("experiencia", "  ## Empresa\r\n### Soporte | 01/2018 - 01/2025\r\nCambio ñ\r\n"),
    ("personales", "  Nombre: Ana Pérez\r\n"), ("voz_email", " Hola ñ\r\n"),
    ("restricciones", '{ "base": "CABA", "reubicacion": false, "nota": "ñ" }\r\n'),
    ("anios_por_area_creador", '{ "nota": "ñ", "anios_por_area": [{"area":"QA", "anios":"más de 3", "reemplaza":["pruebas"]}] }\r\n'),
])
def test_put_exacto_backup_total_y_aviso(entorno, clave, nuevo):
    client, base, backups = entorno
    anterior = {p.name: p.read_bytes() for p in base.iterdir() if p.is_file()}
    respuesta = guardar(client, clave, nuevo)
    assert respuesta.status_code == 200, respuesta.text
    dato = respuesta.json()
    assert dato["ok"] is True and dato["version"] == sha(nuevo.encode("utf-8"))
    assert dato["aviso"] == (AVISO if clave in ("experiencia", "personales") else None)
    assert (base / ARCHIVOS[clave]).read_bytes() == nuevo.encode("utf-8")
    import re
    assert re.fullmatch(r"candidato-\d{8}-\d{6}(?:-(?:[2-9]|[1-9]\d+))?", dato["backup"])
    assert foto(backups / dato["backup"]) == anterior
    segunda = guardar(client, clave, nuevo)
    assert segunda.status_code == 200
    assert segunda.json()["aviso"] is None
    assert segunda.json()["backup"] != dato["backup"]
    assert (backups / segunda.json()["backup"] / ARCHIVOS[clave]).read_bytes() == nuevo.encode("utf-8")


def test_conflicto_no_muta_ni_respalda(entorno):
    client, base, backups = entorno
    r = client.get("/api/candidato/perfil/personales")
    assert r.status_code == 200
    (base / "personales.txt").write_bytes(b"Cambio externo")
    antes = foto(base)
    respuesta = client.put("/api/candidato/perfil/personales", json={"contenido": "Cambio mio", "version": r.json()["version"]})
    assert respuesta.status_code == 409 and respuesta.json()["detail"] == CONFLICTO
    assert foto(base) == antes and not backups.exists()


INVALIDOS = [
    ("restricciones", "{", "No es un JSON válido:"),
    ("restricciones", '{"reubicacion":"no"}', "Restricciones inválidas"),
    ("restricciones", json.dumps({"edad": {"actual": 36, "al": (date.today() + timedelta(days=2)).isoformat()}}), "Restricciones inválidas"),
    ("anios_por_area_creador", '{"anios_por_area":[{"area":"QA","anios":"siete"}]}', "Área inválida"),
    ("anios_por_area_creador", '[{"area":"QA","anios":"8"}]', "Área inválida"),
    ("anios_por_area_creador", '[{"area":"QA","anios":"2"},{"area":"qa","anios":"1"}]', "Área inválida"),
    ("anios_por_area_creador", json.dumps([{"area": f"Área {i}", "anios": "1"} for i in range(9)]), "Área inválida"),
    ("anios_por_area_creador", '[{"area":"QA","anios":"1","reemplaza":"QA"}]', ""),
    ("anios_por_area_creador", '{"nota":"sin lista"}', ""),
    ("experiencia", "", ""), ("personales", " \r\n\t", ""),
    ("voz_email", "x" * 200001, ""),
]


@pytest.mark.parametrize("clave,contenido,prefijo", INVALIDOS, ids=[
    "json-roto", "bool-invalido", "edad-futura", "anios-texto", "anios-sobre-total",
    "area-repetida", "nueve-areas", "reemplaza-invalido", "sin-lista", "vacio", "espacios", "limite-excedido",
])
def test_invalido_sin_efectos(entorno, clave, contenido, prefijo):
    client, base, backups = entorno
    antes = foto(base)
    respuesta = guardar(client, clave, contenido)
    assert respuesta.status_code == 422, respuesta.text
    detail = respuesta.json()["detail"]
    assert isinstance(detail, str) and detail and prefijo in detail
    if prefijo == "Área inválida":
        assert "más de N" in detail and "7" in detail
        dato = json.loads(contenido)
        assert (dato[-1]["area"] if isinstance(dato, list) else "QA") in detail
    assert foto(base) == antes and not backups.exists()


def test_texto_en_limite_y_lista_directa(entorno):
    client, _, _ = entorno
    assert guardar(client, "voz_email", "ñ" * 200000).status_code == 200
    assert guardar(client, "anios_por_area_creador", '[{"area":"QA","anios":"7"}]').status_code == 200
    ocho = [{"area": f"Área {i}", "anios": "1"} for i in range(8)]
    assert guardar(client, "anios_por_area_creador", json.dumps(ocho)).status_code == 200


def test_cache_vigente_usuario_y_no_ia(entorno):
    client, base, _ = entorno
    r = client.get("/api/candidato/anios-por-area")
    assert r.status_code == 200
    assert r.json() == {"areas": [], "vigente": False}
    huella = sha(b"\0".join([perfil_areas.VERSION_CRITERIO, (base / "experiencia.txt").read_bytes(), (base / "personales.txt").read_bytes()]))
    cache = base / "anios_por_area.json"
    cache.write_text(json.dumps({"hash": huella, "anios_por_area": [{"area": "Soporte", "anios": "2"}]}), encoding="utf-8")
    antes = cache.read_bytes()
    assert client.get("/api/candidato/anios-por-area").json() == {"areas": [{"area": "Soporte", "anios": "6"}], "vigente": True}
    assert cache.read_bytes() == antes
    assert guardar(client, "personales", "Nombre: otro").status_code == 200
    assert client.get("/api/candidato/anios-por-area").json() == {"areas": [], "vigente": False}


def test_borrar_aprendizaje_con_respaldo_y_get_igual(entorno):
    client, base, backups = entorno
    anterior = foto(base)
    esperado = json.loads((base / "aprendizajes_candidato.json").read_text(encoding="utf-8"))
    assert client.get("/api/candidato/aprendizajes").json() == {"aprendizajes": esperado}
    respuesta = client.delete("/api/candidato/aprendizajes/sql")
    assert respuesta.status_code == 200, respuesta.text
    del esperado["sql"]
    assert client.get("/api/candidato/aprendizajes").json() == {"aprendizajes": esperado}
    carpetas = list(backups.iterdir())
    # Desde S1 se respalda solo el archivo de aprendizajes, no todo DATA_DIR (con las bases)
    assert len(carpetas) == 1 and foto(carpetas[0]) == {"aprendizajes_candidato.json": anterior["aprendizajes_candidato.json"]}
    antes = foto(base), foto(backups)
    assert client.delete("/api/candidato/aprendizajes/no-existe").status_code == 404
    assert (foto(base), foto(backups)) == antes


def test_data_dir_se_resuelve_despues_de_importar(entorno, tmp_path, monkeypatch):
    client, _, _ = entorno
    assert client.get("/api/candidato/perfil/personales").status_code == 200
    otro = tmp_path / "otro"
    (otro / "candidato").mkdir(parents=True)
    (otro / "candidato" / "personales.txt").write_bytes(b"Otro perfil")
    monkeypatch.setattr(paths, "DATA_DIR", otro)
    r = client.get("/api/candidato/perfil/personales")
    assert r.status_code == 200 and r.json()["contenido"] == "Otro perfil"


def test_dos_guardados_con_la_misma_version_no_pierden_datos(entorno):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    client, base, backups = entorno
    r = client.get("/api/candidato/perfil/personales")
    assert r.status_code == 200
    version = r.json()["version"]
    viejo = (base / "personales.txt").read_bytes()
    barrera = Barrier(2)

    def enviar(texto):
        barrera.wait(timeout=10)
        return texto, client.put("/api/candidato/perfil/personales", json={"contenido": texto, "version": version})

    with ThreadPoolExecutor(max_workers=2) as pool:
        resultados = list(pool.map(enviar, ["Edición A", "Edición B"]))
    assert sorted(r.status_code for _, r in resultados) == [200, 409]
    ganador = next(texto for texto, r in resultados if r.status_code == 200)
    assert (base / "personales.txt").read_bytes() == ganador.encode("utf-8")
    carpetas = list(backups.iterdir())
    assert len(carpetas) == 1
    assert (carpetas[0] / "personales.txt").read_bytes() == viejo


def test_replace_fallido_conserva_archivo_y_no_anuncia_exito(entorno, monkeypatch):
    import os
    client, base, backups = entorno
    lectura = client.get("/api/candidato/perfil/personales")
    assert lectura.status_code == 200
    antes = foto(base)
    original = os.replace

    def falla_solo_destino(src, dst, *args, **kwargs):
        if Path(dst) == base / "personales.txt":
            raise OSError("Fallo de disco simulado")
        return original(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", falla_solo_destino)
    r = client.put("/api/candidato/perfil/personales", json={
        "contenido": "Cambio que no debe quedar parcial", "version": lectura.json()["version"]})
    assert r.status_code == 500
    assert foto(base) == antes
    copias = list(backups.iterdir())
    assert len(copias) == 1
    assert (copias[0] / "personales.txt").read_bytes() == antes["personales.txt"]
