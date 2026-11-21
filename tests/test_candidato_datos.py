"""Datos del candidato que usa el motor: viven en <DATA_DIR>/candidato y se validan igual que en Mi perfil."""
import json
import os

import pytest

from core import candidato_datos, paths


STACK = {
    "coincidencias": [
        {"nombre": "Cobol", "palabras": ["cobol"], "justificacion": "Mantenimiento de lotes en Cobol"},
    ],
    "transferibles": [
        {"nombre": "Fortran", "palabras": ["fortran"], "equivalente": "Lotes en Cobol"},
    ],
    "habilidades_base": {"gestion": ["Kanban de prueba"], "tecnicas": ["Cobol", "JCL"]},
}
ROLES = {
    "roles_objetivo": ["domador de leones", "=equilibrista"],
    "areas": [
        {"area": "circo", "titulos": ["domador", "malabarista"], "sinonimos": ["\\bpista\\b"],
         "nombres": ["circo"], "perfil_actual": True},
        {"area": "magia", "titulos": ["mago"], "perfil_actual": False},
    ],
}


@pytest.fixture
def carpeta(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    base = tmp_path / "candidato"
    base.mkdir()
    return base


def escribir(base, nombre, dato):
    ruta = base / nombre
    ruta.write_text(json.dumps(dato) if not isinstance(dato, str) else dato, encoding="utf-8")
    # Fuerza otra marca de tiempo: el caché se invalida por mtime y tamaño.
    st = ruta.stat()
    os.utime(ruta, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    return ruta


def test_sin_archivos_todo_vacio(carpeta):
    assert candidato_datos.stack() == {
        "coincidencias": [], "transferibles": [], "habilidades_base": {"gestion": [], "tecnicas": []}}
    assert candidato_datos.roles() == {"roles_objetivo": [], "areas": []}
    assert candidato_datos.puesto() == ""


def test_lee_y_normaliza(carpeta):
    escribir(carpeta, "stack.json", STACK)
    escribir(carpeta, "roles_objetivo.json", ROLES)
    (carpeta / "personales.txt").write_text("Nombre: Ana Prueba\nPuesto: Domadora de Leones\n", encoding="utf-8")
    assert candidato_datos.stack() == STACK
    roles = candidato_datos.roles()
    assert roles["roles_objetivo"] == ["domador de leones", "=equilibrista"]
    # Los opcionales se completan: nombres = [área], sin sinónimos, perfil actual por defecto.
    assert roles["areas"][1] == {"area": "magia", "titulos": ["mago"], "sinonimos": [],
                                 "nombres": ["magia"], "perfil_actual": False}
    assert candidato_datos.puesto() == "Domadora de Leones"


def test_cache_se_invalida_al_cambiar(carpeta):
    escribir(carpeta, "stack.json", STACK)
    assert candidato_datos.stack()["coincidencias"][0]["nombre"] == "Cobol"
    otro = json.loads(json.dumps(STACK))
    otro["coincidencias"][0]["nombre"] = "Pascal"
    escribir(carpeta, "stack.json", otro)
    assert candidato_datos.stack()["coincidencias"][0]["nombre"] == "Pascal"


@pytest.mark.parametrize("contenido", ["{no es json", "[]", json.dumps({"coincidencias": "x"})])
def test_archivo_invalido_no_inventa(carpeta, contenido):
    escribir(carpeta, "stack.json", contenido)
    assert candidato_datos.stack()["coincidencias"] == []


@pytest.mark.parametrize("dato, error", [
    ([], "objeto"),
    ({"otra": []}, "otra"),
    ({"coincidencias": [{"nombre": "X", "palabras": [], "justificacion": "J"}]}, "palabras"),
    ({"coincidencias": [{"nombre": " ", "palabras": ["x"], "justificacion": "J"}]}, "nombre"),
    ({"coincidencias": [{"nombre": "X", "palabras": ["x"]}]}, "justificacion"),
    ({"coincidencias": [{"nombre": "X", "palabras": ["x"], "justificacion": "J"},
                        {"nombre": "x", "palabras": ["y"], "justificacion": "J"}]}, "repetid"),
    ({"transferibles": [{"nombre": "X", "palabras": ["x"]}]}, "equivalente"),
    ({"habilidades_base": {"gestion": ["a", "A"]}}, "repetid"),
    ({"habilidades_base": {"otra": ["a"]}}, "otra"),
    ({"coincidencias": [{"nombre": "X" * 301, "palabras": ["x"], "justificacion": "J"}]}, "300"),
])
def test_validar_stack(dato, error):
    errores = candidato_datos.validar_stack(dato)
    assert errores and any(error in e for e in errores), errores


@pytest.mark.parametrize("dato, error", [
    ({"roles_objetivo": "x"}, "roles_objetivo"),
    ({"roles_objetivo": ["="]}, "roles_objetivo"),
    ({"areas": [{"area": "a", "titulos": []}]}, "titulos"),
    ({"areas": [{"area": "a", "titulos": ["t"]}, {"area": "A", "titulos": ["u"]}]}, "repetid"),
    ({"areas": [{"area": "a", "titulos": ["t"], "perfil_actual": "si"}]}, "perfil_actual"),
    ({"areas": [{"area": "a", "titulos": ["t"], "sinonimos": ["(abierto"]}]}, "sinónimo"),
    ({"nota": 3}, "nota"),
])
def test_validar_roles(dato, error):
    errores = candidato_datos.validar_roles(dato)
    assert errores and any(error in e for e in errores), errores


def test_validos_y_nota():
    assert candidato_datos.validar_stack({**STACK, "nota": "libre"}) == []
    assert candidato_datos.validar_roles({**ROLES, "nota": "libre"}) == []
    assert candidato_datos.validar_stack({}) == []
    assert candidato_datos.validar_roles({}) == []


def test_coincide_frase():
    assert candidato_datos.coincide_frase("domador de leones senior", ["domador de leones"])
    assert not candidato_datos.coincide_frase("domadores de leones", ["domador de leones"])
    # «=» exige el título completo.
    assert candidato_datos.coincide_frase("equilibrista", ["=equilibrista"])
    assert not candidato_datos.coincide_frase("equilibrista junior", ["=equilibrista"])
    assert not candidato_datos.coincide_frase("", ["x"])
