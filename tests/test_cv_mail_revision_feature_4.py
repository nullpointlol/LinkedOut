"""Revisión 4 de la feature cv-mail-calidad: años de experiencia solo del propio candidato."""
from datetime import date

import pytest

from core.profile_parser import calcular_anios_experiencia

HOY = date(2026, 11, 8)


def test_experiencia_de_terceros_no_cuenta():
    historial = "## Empresa\n### Analista | 01/2018 - 01/2025\n• Capacité a operadores con más de 20 años de experiencia."
    assert calcular_anios_experiencia(historial, hoy=HOY) == 7


def test_antiguedad_de_otras_cosas_no_cuenta():
    historial = "## Empresa\n### Analista | 01/2018 - 01/2025\n• Migré registros con más de 20 años de antigüedad."
    assert calcular_anios_experiencia(historial, hoy=HOY) == 7


def test_a_cargo_de_terceros_no_cuenta():
    historial = "## Empresa\n### Analista | 01/2018 - 01/2025\n• Tengo a cargo operadores con más de 20 años de experiencia."
    assert calcular_anios_experiencia(historial, hoy=HOY) == 7


def test_terceros_en_perfil_no_cuentan():
    texto = ("## PERFIL\nLideré técnicos con más de 20 años de experiencia. Soy responsable de operadores con "
             "más de 15 años de experiencia.\n## EXPERIENCIA\n### Analista | 01/2018 - 01/2025")
    assert calcular_anios_experiencia(texto, hoy=HOY) == 7


def test_declaracion_en_perfil_no_reemplaza_las_fechas():
    texto = "## PERFIL\nAnalista funcional con más de 7 años de experiencia en operaciones.\n## EXPERIENCIA\n### Analista | 2011 - 2026"
    assert calcular_anios_experiencia(texto, hoy=HOY) == 15


def test_declaracion_en_primera_persona_no_reemplaza_las_fechas():
    texto = "## EXPERIENCIA\n### Analista | 01/2018 - 01/2025\nCuento con 9 años de experiencia en soporte."
    assert calcular_anios_experiencia(texto, hoy=HOY) == 7



def test_anios_por_area_del_perfil_llegan_al_prompt():
    from core import email_composer as ec
    perfil = {"nombre": "Ana", "rol": "Analista Funcional Técnico", "anios_experiencia": 15,
              "anios_por_area": [{"area": "Trabajo total", "anios": "más de 15"},
                                 {"area": "Análisis funcional", "anios": "más de 7"},
                                 {"area": "Monitoreo e ITSM", "anios": "7"}]}
    post = {"texto": "Buscamos analista", "triage": {"puesto": "Analista", "email_destino": "rrhh@example.com"}}
    prompt = ec.build_composer_prompt(post, {"titulo": "Analista", "dolores_empresa": ["Incidentes"]}, perfil, voz={})
    assert "Análisis funcional: más de 7 años" in prompt and "Monitoreo e ITSM: 7 años" in prompt
    assert "con 15 años de experiencia" not in prompt


def test_sin_anios_por_area_usa_el_calculo():
    from core import email_composer as ec
    post = {"texto": "x", "triage": {"puesto": "QA", "email_destino": "a@example.com"}}
    prompt = ec.build_composer_prompt(post, {"titulo": "QA"}, {"nombre": "Ana", "rol": "QA", "anios_experiencia": 7}, voz={})
    assert "con +7 años de experiencia" in prompt  # formato «+N» pedido por el usuario



@pytest.mark.parametrize("frase,esperado", [
    ("Trabajo en una consultora con 20 años de experiencia en selección.", 7),
    ("Soy ingeniero de software con 8 años de experiencia.", 7),
    ("Analista de sistemas con 10 años de experiencia.", 7),
    ("Soy responsable de operadores con 15 años de experiencia.", 7),
])
def test_sujeto_de_la_cifra(frase, esperado):
    historial = "## Empresa\n### Analista | 01/2018 - 01/2025\n" + frase
    assert calcular_anios_experiencia(historial, hoy=HOY) == esperado
