import os
import pytest
from core import aprendizajes as learning


@pytest.fixture
def clean_aprendizajes(tmp_path, monkeypatch):
    test_file = tmp_path / "aprendizajes_test.json"
    monkeypatch.setattr(learning, "APRENDIZAJES_FILE", test_file)
    return test_file


def test_tres_estados_y_misma_clave(clean_aprendizajes):
    # invariante: _clave normaliza espacios, tildes y mayúsculas; MATCH_DIRECTO persiste
    assert learning._clave("  Inglés Técnico  ") == "ingles tecnico"
    assert learning._clave("HÍBRIDO EN CÓRDOBA") == "hibrido en cordoba"
    m = learning.registrar_aprendizaje("Inglés", "MATCH_DIRECTO")
    assert m["estado"] == "MATCH_DIRECTO"
    assert learning.obtener_aprendizajes()["inglés"]["estado"] == "MATCH_DIRECTO"
    if os.getenv("INVARIANT_CHECK") == "1":
        return

    # defecto: persistencia y roundtrip de los tres estados (incluido BRECHA_REAL sin eliminatorio)
    b = learning.registrar_aprendizaje("Docker", "BRECHA_REAL")
    assert b["estado"] == "BRECHA_REAL"
    aprendidos = learning.obtener_aprendizajes()
    assert "docker" in aprendidos
    assert aprendidos["docker"]["estado"] == "BRECHA_REAL"

    t = learning.registrar_aprendizaje("Docker", "TRANSFERIBLE")
    assert t["estado"] == "TRANSFERIBLE"
    assert learning.obtener_aprendizajes()["docker"]["estado"] == "TRANSFERIBLE"

    m2 = learning.registrar_aprendizaje("Docker", "MATCH_DIRECTO")
    assert m2["estado"] == "MATCH_DIRECTO"
    assert learning.obtener_aprendizajes()["docker"]["estado"] == "MATCH_DIRECTO"

    # Vuelta a BRECHA_REAL se conserva
    b2 = learning.registrar_aprendizaje("Docker", "BRECHA_REAL")
    assert b2["estado"] == "BRECHA_REAL"
    assert learning.obtener_aprendizajes()["docker"]["estado"] == "BRECHA_REAL"

    # Comparación _clave y sin duplicados semánticos
    learning.registrar_aprendizaje("INGLÉS", "TRANSFERIBLE")
    aprendidos_ingles = learning.obtener_aprendizajes()
    claves_ingles = [k for k in aprendidos_ingles if learning._clave(k) == "ingles"]
    assert len(claves_ingles) == 1

    # Decisiones previas preservadas al llamar sin eliminatorio
    learning.registrar_aprendizaje("Linux", "BRECHA_REAL", eliminatorio=False)
    assert learning.obtener_aprendizajes()["linux"]["eliminatorio"] is False

    learning.registrar_aprendizaje("Linux", "TRANSFERIBLE")  # sin eliminatorio
    assert learning.obtener_aprendizajes()["linux"]["eliminatorio"] is False
    assert learning.obtener_aprendizajes()["linux"]["estado"] == "TRANSFERIBLE"

    learning.registrar_aprendizaje("Linux", "BRECHA_REAL")  # cicla a brecha sin eliminatorio
    assert learning.obtener_aprendizajes()["linux"]["eliminatorio"] is False
    assert learning.obtener_aprendizajes()["linux"]["estado"] == "BRECHA_REAL"


def test_estado_igual_es_aprendido(clean_aprendizajes):
    # invariante: aplicar a lista vacía o tipo inválido devuelve 0 sin error
    assert learning.aplicar_aprendizajes_a_requisitos([]) == 0
    assert learning.aplicar_aprendizajes_a_requisitos(None) == 0
    if os.getenv("INVARIANT_CHECK") == "1":
        return

    # Registrar los 3 estados
    learning.registrar_aprendizaje("Python", "MATCH_DIRECTO")
    learning.registrar_aprendizaje("Docker", "TRANSFERIBLE")
    learning.registrar_aprendizaje("Kubernetes", "BRECHA_REAL")

    # Requisitos que ya coinciden en estado
    reqs = [
        {"nombre": "Python", "tipo": "REQUISITO_EXCLUYENTE", "estado": "MATCH_DIRECTO", "approved": False},
        {"nombre": "Docker", "tipo": "RESPONSABILIDAD", "estado": "TRANSFERIBLE", "approved": False},
        {"nombre": "Kubernetes", "tipo": "REQUISITO_EXCLUYENTE", "estado": "BRECHA_REAL", "approved": True},
    ]

    # Primera aplicación: debe marcar aprendido=True siempre, approved=(estado!='BRECHA_REAL')
    cambiados = learning.aplicar_aprendizajes_a_requisitos(reqs)
    assert cambiados == 3
    assert reqs[0]["estado"] == "MATCH_DIRECTO"
    assert reqs[0]["approved"] is True
    assert reqs[0]["aprendido"] is True

    assert reqs[1]["estado"] == "TRANSFERIBLE"
    assert reqs[1]["approved"] is True
    assert reqs[1]["aprendido"] is True

    assert reqs[2]["estado"] == "BRECHA_REAL"
    assert reqs[2]["approved"] is False
    assert reqs[2]["aprendido"] is True

    # Segunda aplicación idéntica: devuelve 0 (idempotencia)
    cambiados_segunda = learning.aplicar_aprendizajes_a_requisitos(reqs)
    assert cambiados_segunda == 0


def test_veto_y_prompt(clean_aprendizajes):
    # invariante: prompt vacío si no hay aprendizajes registrados
    assert learning.aprendizajes_para_prompt() == ""
    if os.getenv("INVARIANT_CHECK") == "1":
        return

    # Registrar brecha real sin eliminatorio y con eliminatorio false/true
    learning.registrar_aprendizaje("Kubernetes", "BRECHA_REAL")
    learning.registrar_aprendizaje("Gastronomía", "BRECHA_REAL", eliminatorio=False)
    learning.registrar_aprendizaje("Guardias 24/7", "BRECHA_REAL", eliminatorio=True)

    prompt = learning.aprendizajes_para_prompt()
    # Prompt debe listar también brechas sin decisión de eliminatorio
    assert "- Kubernetes: BRECHA_REAL" in prompt
    assert "- Gastronomía: BRECHA_REAL; eliminatorio: false" in prompt
    assert "- Guardias 24/7: BRECHA_REAL; eliminatorio: true" in prompt
    assert "False explícito veta el descarte" in prompt

    # Veto de descarte: false explícito limpia categoria_descarte y fija eliminatorio=False
    req_veto = [
        {"nombre": "Gastronomía", "estado": "BRECHA_REAL", "eliminatorio": True, "categoria_descarte": "RUBRO"}
    ]
    learning.aplicar_aprendizajes_a_requisitos(req_veto)
    assert req_veto[0]["eliminatorio"] is False
    assert req_veto[0]["eliminatorio_usuario"] is False
    assert "categoria_descarte" not in req_veto[0]

    # True en eliminatorio no degrada un MATCH_DIRECTO ni un TRANSFERIBLE
    learning.registrar_aprendizaje("AWS", "MATCH_DIRECTO", eliminatorio=True)
    req_match = [
        {"nombre": "AWS", "estado": "MATCH_DIRECTO", "approved": True}
    ]
    learning.aplicar_aprendizajes_a_requisitos(req_match)
    assert req_match[0]["estado"] == "MATCH_DIRECTO"
    assert req_match[0]["approved"] is True
    assert req_match[0]["aprendido"] is True
    assert req_match[0]["eliminatorio"] is True
    assert req_match[0]["eliminatorio_usuario"] is True
