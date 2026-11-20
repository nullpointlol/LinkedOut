"""Estructura de cargos del CV: cada cargo real con su título, sus fechas y solo sus viñetas; las
reglas por empresa viven en el perfil (`estructura_cv.json`); el título unificado solo con «segun_oferta» y cuando
algún cargo no suma. Candidato inventado."""
import pytest

from core import cv_maestro as m
from core import estructura_cv as e

MAESTRO = """# Experiencia

## Freelance
### Consultor de Procesos | 2024 - 2026
• Relevé procesos de facturación y armé requerimientos con SQL para el equipo de desarrollo.

## Acme Datos
### Ingeniero de Plataforma | 2021 - 2024
• Mantuve pipelines de Jenkins y GitLab para 30 proyectos.
### Analista de Soporte | 2019 - 2021
• Resolví 20 incidentes críticos por mes en servidores Linux con Jira.
• Documenté 40 servidores y sus errores recurrentes.
### Analista Funcional | 2016 - 2019
• Analicé con SQL 100 tickets de facturación por mes.
• Probé servicios web con SoapUI antes de cada proceso masivo.
• Armé consultas SQL para 15 compañeros.

## Initech
### Jefe de Guardia | 2012 - 2015
• Escalé 30 eventos por turno a las guardias de bases de datos.
### Técnico de Redes | 2010 - 2012
• Monitoreé alertas de 400 servidores con Nagios.

## Globex
### Programador PHP | 2006 - 2008
• Desarrollé en PHP páginas de reservas para hoteles, con un framework propio y pruebas manuales de cada flujo.

## HABILIDADES TÉCNICAS
• **Datos:** SQL, Jira, SoapUI.
"""
PERSONALES = "Ana Prueba\nana.prueba@example.org\n\nEDUCACIÓN\n• Tecnicatura - Instituto Test - 2013\n\nIDIOMAS\n• Inglés: C1\n"
ESTRUCTURA = e.desde_dato({"cargos_relevantes_entre": 3, "empresas": [
    {"empresa": "Acme Datos", "cargos": "todos"},
    {"empresa": "Initech", "cargos": "segun_oferta", "titulo_unificado": "Analista de Operaciones",
     "titulo_unificado_en": "Operations Analyst", "vinetas_unificado": 2},
    {"empresa": "Globex", "cargos": "todos", "vinetas_max_por_cargo": 1, "largo_max_vineta": 90}]})


def _analisis(*nombres):
    return {"puesto": "Analista", "requisitos": [{"nombre": n, "estado": "MATCH_DIRECTO", "approved": True}
                                               for n in nombres]}


def _borrador(analisis, estructura=ESTRUCTURA):
    return m.seleccionar(MAESTRO, PERSONALES, analisis, titulo="ANALISTA", estructura=estructura)


def _encabezados(b):
    return [bl.encabezado for bl in b.experiencia]


def test_cada_cargo_con_su_titulo_sus_fechas_y_solo_sus_vinetas():
    b = _borrador(_analisis("SQL", "facturación"))
    acme = [bl for bl in b.experiencia if bl.empresa == "Acme Datos"]
    assert [bl.encabezado for bl in acme] == ["### Ingeniero de Plataforma | Acme Datos (2021 - 2024)",
                                              "### Analista de Soporte | Acme Datos (2019 - 2021)",
                                              "### Analista Funcional | Acme Datos (2016 - 2019)"]
    for bl in acme:
        assert bl.vinetas and all(l.original in bl.originales for l in bl.vinetas)
    assert "### Ingeniero de Plataforma | Acme Datos (2016 - 2024)" not in _encabezados(b)  # nunca el período entero
    assert m.control_estructura(b, MAESTRO, ESTRUCTURA) == []


def test_segun_oferta_unifica_cuando_un_cargo_no_suma():
    b = _borrador(_analisis("SQL", "facturación"))  # Initech no tiene nada de SQL ni facturación
    initech = [bl for bl in b.experiencia if bl.empresa == "Initech"]
    assert [bl.encabezado for bl in initech] == ["### Analista de Operaciones | Initech (2010 - 2015)"]
    assert 1 <= len(initech[0].vinetas) <= 2 and initech[0].unificado
    assert m.control_estructura(b, MAESTRO, ESTRUCTURA) == []


def test_segun_oferta_separa_cuando_todos_los_cargos_suman():
    b = _borrador(_analisis("guardias", "eventos", "monitoreo", "alertas", "servidores"))
    initech = [bl.encabezado for bl in b.experiencia if bl.empresa == "Initech"]
    assert initech == ["### Jefe de Guardia | Initech (2012 - 2015)",
                       "### Técnico de Redes | Initech (2010 - 2012)"]


def test_el_titulo_unificado_va_en_ingles_en_un_cv_en_ingles():
    b = m.seleccionar(MAESTRO, PERSONALES, _analisis("SQL"), idioma="en", titulo="ANALYST", estructura=ESTRUCTURA)
    assert "### Operations Analyst | Initech (2010 - 2015)" in _encabezados(b)


def test_topes_del_perfil_para_sintetizar():
    b = _borrador(_analisis("PHP", "hoteles", "SQL"))
    globex = next(bl for bl in b.experiencia if bl.empresa == "Globex")
    assert len(globex.vinetas) == 1 and globex.maximo == 1 and globex.largo_max == 90
    assert "sintetizá cada viñeta en hasta 90 caracteres" in m._lineas_para_ia(b)
    assert any("máximo 90" in h.motivo for h in m.largos(b))  # la del maestro tiene más de 90: hay que sintetizarla


def test_sin_archivo_todas_las_empresas_muestran_todos_sus_cargos():
    b = _borrador(_analisis("SQL"), estructura=None)
    assert len(b.experiencia) == 7 and all(bl.vinetas for bl in b.experiencia)
    assert m.control_estructura(b, MAESTRO, None) == []


@pytest.mark.parametrize("cambio,motivo", [
    ("periodo_entero", "no es un cargo real"),
    ("falta_cargo", "faltan los cargos"),
    ("vineta_ajena", "viñetas de otro cargo"),
    ("unificado_sin_regla", "no es un cargo real"),
    ("sin_vinetas", "no tiene viñetas"),
])
def test_el_control_detecta_cada_violacion_de_la_estructura(cambio, motivo):
    b = _borrador(_analisis("SQL", "facturación"))
    acme = [bl for bl in b.experiencia if bl.empresa == "Acme Datos"]
    if cambio == "periodo_entero":  # el defecto de la v2: un cargo con el período de toda la empresa
        acme[0].encabezado = "### Ingeniero de Plataforma | Acme Datos (2016 - 2024)"
    elif cambio == "falta_cargo":
        b.experiencia.remove(acme[1])
    elif cambio == "vineta_ajena":
        acme[0].vinetas.append(acme[2].vinetas[0])
    elif cambio == "unificado_sin_regla":
        acme[0].encabezado = "### Analista de Operaciones | Acme Datos (2016 - 2024)"
    else:
        acme[1].vinetas.clear()
    assert any(motivo in h.motivo for h in m.control_estructura(b, MAESTRO, ESTRUCTURA))


@pytest.mark.parametrize("dato,error", [
    ({"empresas": [{"empresa": "KORVA", "cargos": "agrupar"}]}, "«todos» o «segun_oferta»"),
    ({"empresas": [{"empresa": "KORVA", "cargos": "segun_oferta"}]}, "titulo_unificado"),
    ({"empresas": [{"empresa": "KORVA"}, {"empresa": "korva"}]}, "repetida"),
    ({"empresas": [{"empresa": "KORVA", "vinetas_max_por_cargo": -1}]}, "entero"),
    ({"cargos_relevantes_entre": 0, "empresas": []}, "mayor que 0"),
])
def test_validar_rechaza_estructuras_invalidas(dato, error):
    assert any(error in x for x in e.validar(dato))
    with pytest.raises(e.EstructuraInvalida):
        e.desde_dato(dato)


@pytest.mark.parametrize("nombre", ["Trato cordial, empatía y proactividad", "Orden, responsabilidad y compromiso",
                                    "Comunicación y orientación al servicio", "Strong communication skills"])
def test_los_requisitos_blandos_no_se_exigen_ni_se_incorporan(nombre):
    obligatorias, habilitados = m.terminos_del_matching(_analisis("SQL", nombre))
    assert obligatorias == ["SQL"] and habilitados == ["SQL"]


def test_mi_perfil_valida_el_archivo_de_cargos(tmp_path):
    import json
    from core import perfil_candidato
    perfil_candidato._validar("estructura_cv", json.dumps({"empresas": [{"empresa": "Initech", "cargos": "todos"}]}),
                              tmp_path)
    with pytest.raises(perfil_candidato.PerfilError, match="Estructura del CV inválida"):
        perfil_candidato._validar("estructura_cv", json.dumps({"empresas": [{"empresa": "Initech", "cargos": "x"}]}),
                                  tmp_path)


# El orden de recorte de la página (mínimos y habilidades) está en tests/test_cv_relevancia.py
