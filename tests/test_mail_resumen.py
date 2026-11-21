"""El resumen del mail es una sola oración «Soy <rol> con +N años de experiencia en A, B y C.»
(ejemplos reales del )."""
import pytest

from core import email_composer as ec

ORIGINALES = [
    "Soy Analista Funcional Técnico, con más de 7 años de experiencia en análisis funcional. Para potenciar las "
    "células técnicas, centralicé métricas de más de 90 aplicaciones con dashboards en PowerBI y consultas SQL.",
    "Soy Analista Funcional Técnico, con 7 años de experiencia en soporte e incidentes. Ante fallas en sistemas "
    "críticos, lideré el diagnóstico funcional con pgAdmin y DBeaver para restablecer la operatoria.",
    "Soy Analista Funcional Técnico, con más de 7 años de experiencia en análisis funcional. Para evitar desvíos de "
    "plazos, coordiné traspasos en Jira con criterios de aceptación aprobados.",
]
BUENOS = [
    "Soy Analista Funcional Técnico con +7 años de experiencia en células técnicas, centralización de métricas, "
    "generación de dashboards en PowerBI y consultas SQL.",
    "Soy Analista Funcional Técnico con +7 años de experiencia en soporte de incidentes, sistemas críticos y "
    "diagnóstico funcional con SQL.",
]


def _cuerpo(resumen):
    return f"Hola,\n\nVi la búsqueda y me interesa.\n\n{resumen}\n\nAdjunto mi CV.\n\nSaludos,\nAna"


@pytest.mark.parametrize("resumen", ORIGINALES)
def test_resumen_en_dos_oraciones_se_rechaza(resumen):
    assert ec.problemas_de_resumen(_cuerpo(resumen))


@pytest.mark.parametrize("resumen", BUENOS)
def test_resumen_de_una_oracion_con_frentes_pasa(resumen):
    assert ec.problemas_de_resumen(_cuerpo(resumen)) == []


def test_menos_de_tres_frentes_se_rechaza():
    assert ec.problemas_de_resumen(_cuerpo("Soy Analista Funcional Técnico con +7 años de experiencia en SQL."))


def test_sin_resumen_no_se_inventa_un_problema():
    assert ec.problemas_de_resumen(_cuerpo("Probé consultas con SQL.")) == []


def test_ingles():
    assert ec.problemas_de_resumen(
        _cuerpo("I'm a Functional Analyst with +7 years of experience in incident support, critical systems and SQL."),
        ec.EN) == []


def test_prompt_pide_la_forma_exacta_con_mas_n():
    post = {"texto": "Buscamos analista", "triage": {"puesto": "Analista", "email_destino": "rrhh@example.com"}}
    perfil = {"nombre": "Ana", "rol": "Analista Funcional Técnico", "anios_experiencia": 15,
              "anios_por_area": [{"area": "Soporte e incidentes", "anios": "7"}]}
    p = ec.build_composer_prompt(post, {"titulo": "Analista", "dolores_empresa": ["Incidentes"]}, perfil, voz={})
    assert "UNA SOLA ORACIÓN" in p and "con +N años de experiencia en [frente 1]" in p
    assert "se escriben +7" in p and "Soporte e incidentes: 7 años" in p
    assert "«Para…»" in p
