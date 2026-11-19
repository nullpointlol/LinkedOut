"""Años pedidos en rango y requisitos en inglés (creador, 19/11).

Caso real: «2 a 4 años (ideal 3+) en IT Operations, Support, SysAdmin, DevOps» quedó como brecha fuerte
con «Pide 4 años; el perfil tiene 1 en DevOps»: tomaba el máximo del rango y no reconocía «Support»
como el área de soporte del perfil.
"""
import pytest

from core.afinidad_reglas import _extraer_anios, aplicar_reglas


@pytest.mark.parametrize("texto,esperado", [
    ("2 a 4 años de experiencia", 2),
    ("2-4 años", 2),
    ("2 – 4 años", 2),
    ("3 to 5 years of experience", 3),
    ("between 2 and 4 years", 2),
    ("entre 3 y 6 años", 3),
    ("5 años", 5),
    ("más de 4 años", 4),
    ("3+ years", 3),
])
def test_rango_de_anios_toma_el_minimo(texto, esperado):
    assert _extraer_anios(texto) == esperado


AREAS = [
    {"area": "soporte e incidentes", "anios": "7"},
    {"area": "devops", "anios": "1"},
]


def _req(texto, estado="MATCH_DIRECTO"):
    return {"nombre": "Experiencia en IT Operations y DevOps", "tipo": "REQUISITO_EXCLUYENTE",
            "estado": estado, "texto": texto, "approved": True}


def test_support_en_ingles_cuenta_los_anios_de_soporte():
    req = _req("Nivel: 2 a 4 años de experiencia (ideal 3+) en IT Operations, Support, SysAdmin, DevOps.")
    salida = aplicar_reglas([req], puesto="AI-Native Operations Engineer", areas=AREAS, base_candidato="")
    assert salida[0]["estado"] == "MATCH_DIRECTO", salida[0].get("justificacion")
    assert salida[0].get("dura") != "anios"


def test_si_ningun_area_alcanza_sigue_siendo_brecha():
    req = _req("At least 10 years in IT Support or DevOps.")
    salida = aplicar_reglas([req], puesto="Senior SRE", areas=AREAS, base_candidato="")
    assert salida[0]["estado"] == "BRECHA_REAL"
    assert salida[0]["dura"] == "anios"
    assert "10" in salida[0]["justificacion"]


def test_requisito_en_espanol_no_cambia():
    req = _req("3 años en soporte de aplicaciones")
    salida = aplicar_reglas([req], puesto="Analista de soporte", areas=AREAS, base_candidato="")
    assert salida[0]["estado"] == "MATCH_DIRECTO"
