"""CV completo sobre la base aprobada del candidato (creador, 09/11).

La base (`experiencia.txt`) es la verdad: títulos de rol, empresas y fechas se copian de ahí. El CV
por defecto no tiene límite de páginas; el de 1 página es opcional y agrupa por empresa.
"""
import sqlite3
from datetime import date
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import core.database as database
import server
from core import cv_guardas
from core.cv_guardas import COMPLETO, UNA_PAGINA, corregir_encabezados
from core.profile_parser import calcular_anios_experiencia, parse_candidate_profile, parse_roles
from core.writer import WriterAgent

BASE = """# Experiencia profesional

## Freelance
### Product Owner Analítico | 2024 - 2026
• Relevé necesidades de clientes y las traduje en requerimientos y criterios de aceptación.
• Armé pruebas automatizadas para cada flujo de una plataforma web.

## VELORA SISTEMAS
### Ingeniero DevOps Jr. | 2022 - 2024
• Centralicé las métricas de calidad de más de 90 aplicaciones con Python y la API de Codacy.
### Analista de Soporte de Aplicaciones | 2020 - 2022
• Analicé con PagerDuty rendimiento y errores entre servicios.
### Analista Técnico Funcional | 2016 - 2020
• Investigué errores con SQL en pgAdmin y DBeaver.
### Analista Técnico Funcional
• Hice guardias on call del aplicativo.

## KORVA
### Gestor de Incidentes y Guardias ITSM | 2013 - 2016
• Escalé eventos y contacté a las guardias de Storage, Windows y DBA.
### Técnico de Monitoreo NOC | 2010 - 2013
• Monitoreé alertas con Nagios XI, OTRS y ServiceNow.

## Cobalto Sistemas
### Programador Java | 2004 - 2007
• Desarrollé en Java páginas web transaccionales para bancos.

## HABILIDADES TÉCNICAS
• **Bases de datos:** SQL, PostgreSQL.

## HABILIDADES INTERPERSONALES
• Comunicación con perfiles técnicos y de negocio.
"""

PERSONALES = """Alex Rivera
Puesto: Analista Técnico Funcional
San Isidro, Buenos Aires | +54 9 11 0000-0000 | candidato@example.test

EDUCACIÓN
• Tecnicatura en Informática (2013 - 2016)

IDIOMAS
• Español: Nativo
"""

ROLES = parse_roles(BASE)


def _cv(experiencia: str) -> str:
    return ("# ALEX RIVERA\nANALISTA TÉCNICO FUNCIONAL\nSan Isidro | candidato@example.test\n\n"
            "## PERFIL\nTrabajo en tecnología desde 2004.\n\n## EXPERIENCIA\n" + experiencia +
            "\n\n## HABILIDADES TÉCNICAS\n• **Datos:** SQL.\n\n## EDUCACIÓN\n• Tecnicatura\n\n## IDIOMAS\n• Español: Nativo\n")


CV_COMPLETO_OK = _cv("\n".join(f"### {r['rol']} | {r['empresa']} ({r['desde']} - {r['hasta']})\n• Hice algo."
                               for r in ROLES))


# --- Base del candidato -------------------------------------------------------------------------

def test_roles_por_empresa_en_orden_y_sin_secciones_reservadas():
    assert [(r["empresa"], r["rol"], r["desde"], r["hasta"]) for r in ROLES] == [
        ("Freelance", "Product Owner Analítico", "2024", "2026"),
        ("VELORA SISTEMAS", "Ingeniero DevOps Jr.", "2022", "2024"),
        ("VELORA SISTEMAS", "Analista de Soporte de Aplicaciones", "2020", "2022"),
        ("VELORA SISTEMAS", "Analista Técnico Funcional", "2016", "2020"),
        ("KORVA", "Gestor de Incidentes y Guardias ITSM", "2013", "2016"),
        ("KORVA", "Técnico de Monitoreo NOC", "2010", "2013"),
        ("Cobalto Sistemas", "Programador Java", "2004", "2007"),
    ]
    # El `###` repetido sin fechas continúa el rol anterior; habilidades no son viñetas de un rol
    assert ROLES[3]["vinetas"] == ["Investigué errores con SQL en pgAdmin y DBeaver.",
                                   "Hice guardias on call del aplicativo."]
    assert all("Comunicación" not in v for r in ROLES for v in r["vinetas"])


def test_empresas_no_incluyen_secciones_de_habilidades():
    perfil = parse_candidate_profile(BASE, PERSONALES)
    assert perfil["empresas"] == ["Freelance", "VELORA SISTEMAS", "KORVA", "Cobalto Sistemas"]
    assert len(perfil["roles"]) == 7


def test_puesto_declarado_manda_sobre_el_primer_rol():
    assert parse_candidate_profile(BASE, PERSONALES)["rol"] == "Analista Técnico Funcional"
    sin_puesto = PERSONALES.replace("Puesto: Analista Técnico Funcional\n", "")
    assert parse_candidate_profile(BASE, sin_puesto)["rol"] == "Product Owner Analítico"


def test_anios_no_cuentan_el_hueco_entre_empleos():
    # 2004-2007 (3) + 2010-2026 (16): el hueco 2007-2010 no suma; los roles contiguos se unen
    assert calcular_anios_experiencia(BASE, date(2026, 11, 9)) == 19
    contiguos = "## Empresa\n### A | 2010 - 2013\n### B | 2013 - 2016\n"
    assert calcular_anios_experiencia(contiguos, date(2026, 11, 9)) == 6


# --- Guarda de veracidad ------------------------------------------------------------------------

def test_cv_completo_fiel_no_cambia():
    cv, cambios, problemas = corregir_encabezados(CV_COMPLETO_OK, ROLES, COMPLETO)
    assert (cv, cambios, problemas) == (CV_COMPLETO_OK, [], [])


def test_titulo_inventado_y_fechas_se_corrigen_con_la_base():
    cv_in = CV_COMPLETO_OK.replace(
        "### Analista de Soporte de Aplicaciones | VELORA SISTEMAS (2020 - 2022)",
        "### Ingeniero de Observabilidad Senior | VELORA SISTEMAS (2021 - 2022)").replace(
        "### Programador Java | Cobalto Sistemas (2004 - 2007)", "### Programador | Cobalto Sistemas | 2004 - 2010")
    cv, cambios, problemas = corregir_encabezados(cv_in, ROLES, COMPLETO)
    assert "### Analista de Soporte de Aplicaciones | VELORA SISTEMAS (2020 - 2022)" in cv
    assert "### Programador Java | Cobalto Sistemas (2004 - 2007)" in cv
    assert "Ingeniero de Observabilidad" not in cv and len(cambios) == 2 and problemas == []


def test_rol_faltante_y_encabezado_sin_respaldo_son_problemas():
    experiencia = "\n".join(
        f"### {r['rol']} | {r['empresa']} ({r['desde']} - {r['hasta']})\n• Hice algo."
        for r in ROLES if r["rol"] != "Técnico de Monitoreo NOC")
    cv_in = _cv(experiencia + "\n### Consultor SAP | Globant (2010 - 2010)\n• Inventado.")
    _, _, problemas = corregir_encabezados(cv_in, ROLES, COMPLETO)
    assert any("Técnico de Monitoreo NOC | KORVA" in p for p in problemas)
    assert any("sin respaldo" in p and "Globant" in p for p in problemas)


def test_titulo_y_fechas_reales_con_otra_empresa_se_corrige_la_empresa():
    cv_in = CV_COMPLETO_OK.replace("### Programador Java | Cobalto Sistemas (2004 - 2007)",
                                   "### Programador Java | Globant (2004 - 2007)")
    cv, _, problemas = corregir_encabezados(cv_in, ROLES, COMPLETO)
    assert "### Programador Java | Cobalto Sistemas (2004 - 2007)" in cv and problemas == []


def test_mayusculas_de_la_empresa_se_respetan():
    cv_in = CV_COMPLETO_OK.replace("| VELORA SISTEMAS (2022 - 2024)", "| Velora Sistemas (2022 - 2024)")
    cv, cambios, _ = corregir_encabezados(cv_in, ROLES, COMPLETO)
    assert cv == cv_in and cambios == []


def test_una_pagina_agrupa_por_empresa_con_titulo_real_y_rango_completo():
    cv_in = _cv("### Analista de Soporte de Aplicaciones | VELORA SISTEMAS (2020 - 2022)\n• a\n"
                "### Product Owner Analítico | Freelance (2024 - 2026)\n• b\n"
                "### Analista de Monitoreo | KORVA (2010 - 2016)\n• c\n"
                "### Programador Java | Cobalto Sistemas (2004 - 2007)\n• d")
    cv, cambios, problemas = corregir_encabezados(cv_in, ROLES, UNA_PAGINA)
    assert problemas == []
    assert "### Analista de Soporte de Aplicaciones | VELORA SISTEMAS (2016 - 2024)" in cv
    # Título inventado en KORVA → el rol real más reciente de esa empresa
    assert "### Gestor de Incidentes y Guardias ITSM | KORVA (2010 - 2016)" in cv
    _, _, faltan = corregir_encabezados(cv_in.replace("### Programador Java | Cobalto Sistemas (2004 - 2007)\n• d", ""),
                                        ROLES, UNA_PAGINA)
    assert faltan == ["Falta en EXPERIENCIA: Cobalto Sistemas"]


# --- Redactor -----------------------------------------------------------------------------------

def _analisis(formato=None):
    a = {"puesto": "Analista Funcional", "empresa": "Acme", "dolores_empresa": ["Incidentes"],
         "requisitos": [{"nombre": "SQL", "estado": "MATCH_DIRECTO", "approved": True}]}
    if formato:
        a["formato_cv"] = formato
    return a


@pytest.mark.asyncio
async def test_prompt_completo_lleva_roles_y_no_recorta(monkeypatch):
    largo = CV_COMPLETO_OK.replace("• Hice algo.", "• Hice algo. " + "Detalle técnico concreto. " * 40)
    assert len(largo) > 5000
    bridge = AsyncMock()
    bridge.run_prompt.return_value = {"cv_markdown": largo, "pitch": "p",
                                      "pitches_multicanal": {"inmail": "i", "email": "e", "web_ats": "w"}}
    writer = WriterAgent(bridge=bridge)
    res = await writer.generate_cv_and_pitch(_analisis(COMPLETO), profile={"experiencia": BASE, "personales": PERSONALES})
    prompt = bridge.run_prompt.call_args.args[0]
    assert "ROLES DEL CANDIDATO" in prompt
    assert "### Técnico de Monitoreo NOC | KORVA (2010 - 2013)" in prompt
    assert "1 PÁGINA A4" not in prompt.upper()
    assert res["formato_cv"] == COMPLETO
    assert len(res["cv_markdown"]) > 5000  # sin ensure_cv_length
    assert res["veracidad"]["problemas"] == []


@pytest.mark.asyncio
async def test_prompt_una_pagina_agrupa_por_empresa():
    bridge = AsyncMock()
    bridge.run_prompt.return_value = {"cv_markdown": CV_COMPLETO_OK, "pitch": "p",
                                      "pitches_multicanal": {"inmail": "i", "email": "e", "web_ats": "w"}}
    writer = WriterAgent(bridge=bridge)
    await writer.generate_cv_and_pitch(_analisis(UNA_PAGINA), profile={"experiencia": BASE, "personales": PERSONALES})
    prompt = bridge.run_prompt.call_args.args[0]
    assert "EMPRESAS DEL CANDIDATO" in prompt
    assert "1 PÁGINA A4" in prompt.upper()
    assert "<uno de: Ingeniero DevOps Jr. / Analista de Soporte de Aplicaciones / Analista Técnico Funcional> | VELORA SISTEMAS (2016 - 2024)" in prompt


@pytest.mark.parametrize("formato,encabezados", [(COMPLETO, 7), (UNA_PAGINA, 4)])
def test_fallback_un_encabezado_por_rol_o_por_empresa(formato, encabezados):
    res = WriterAgent(bridge=AsyncMock())._deterministic_fallback(
        "Analista Funcional", "Acme", _analisis(formato), {"experiencia": BASE, "personales": PERSONALES})
    cv = res["cv_markdown"]
    assert sum(l.startswith("### ") for l in cv.splitlines()) == encabezados
    _, _, problemas = corregir_encabezados(cv, ROLES, formato)
    assert problemas == []


# --- Postulación, endpoint y dictamen -----------------------------------------------------------

def test_postulacion_nueva_es_completa_y_el_endpoint_cambia_el_formato(monkeypatch):
    pid = database.create_postulacion("Analista", "Acme", "Oferta", status="READY")
    assert database.get_postulacion(pid)["formato_cv"] == COMPLETO
    llamadas = []

    async def recalcular(postulacion_id):
        llamadas.append(server._formato_cv_de(postulacion_id))

    monkeypatch.setattr(server, "recalculate_cv_task", recalcular)
    client = TestClient(server.app)
    assert client.post(f"/api/ofertas/{pid}/recalculate-cv", json={"formato": UNA_PAGINA}).status_code == 200
    assert database.get_postulacion(pid)["formato_cv"] == UNA_PAGINA
    assert llamadas == [UNA_PAGINA]
    assert client.post(f"/api/ofertas/{pid}/recalculate-cv", json={"formato": "dos_paginas"}).status_code == 422
    # Sin cuerpo sigue funcionando como antes y no cambia el formato
    assert client.post(f"/api/ofertas/{pid}/recalculate-cv").status_code == 200
    assert database.get_postulacion(pid)["formato_cv"] == UNA_PAGINA


def test_migracion_marca_como_una_pagina_los_cv_existentes(tmp_path):
    db = tmp_path / "vieja.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE postulaciones (id INTEGER PRIMARY KEY AUTOINCREMENT, titulo TEXT, empresa TEXT, "
                 "contenido_oferta TEXT, url TEXT, status TEXT, cv_markdown TEXT)")
    conn.execute("INSERT INTO postulaciones (titulo, cv_markdown) VALUES ('con cv', '# CV')")
    conn.execute("INSERT INTO postulaciones (titulo, cv_markdown) VALUES ('sin cv', '')")
    conn.commit()
    conn.close()
    database.init_db(db)
    conn = sqlite3.connect(db)
    filas = dict(conn.execute("SELECT titulo, formato_cv FROM postulaciones").fetchall())
    conn.close()
    assert filas == {"con cv": UNA_PAGINA, "sin cv": COMPLETO}


def test_dictamen_final_bloquea_rol_faltante(monkeypatch):
    monkeypatch.setattr(server, "_perfil_o_vacio", lambda: {"roles": ROLES})
    cv = CV_COMPLETO_OK.replace("### Programador Java | Cobalto Sistemas (2004 - 2007)\n• Hice algo.", "")
    assert server._problemas_de_veracidad(cv, COMPLETO) == ["Falta en EXPERIENCIA: Programador Java | Cobalto Sistemas"]
    assert server._problemas_de_veracidad(CV_COMPLETO_OK, COMPLETO) == []


def test_cv_completo_pide_habilidades_interpersonales_y_el_de_una_pagina_no():
    from core.writer import secciones_cv
    assert "HABILIDADES INTERPERSONALES" in secciones_cv("es", COMPLETO)
    assert "SOFT SKILLS" in secciones_cv("en", COMPLETO)
    assert "INTERPERSONALES" not in secciones_cv("es", UNA_PAGINA)


def test_perfil_sin_requisitos_de_estudios_copiados_de_la_oferta():
    cv = _cv("### Programador Java | Cobalto Sistemas (2004 - 2007)\n• a").replace(
        "Trabajo en tecnología desde 2004.",
        "Trabajo en tecnología desde 2004. Tengo estudios en Sistemas o afines.")
    nuevo, cambios = cv_guardas.corregir_perfil(cv, areas=[])
    assert "afines" not in nuevo and "desde 2004" in nuevo and cambios


def test_habilidades_sin_requisitos_de_estudios_de_la_oferta():
    cv = _cv("### Programador Java | Cobalto Sistemas (2004 - 2007)\n• a").replace(
        "• **Datos:** SQL.", "• **Análisis:** relevamiento, Propuesta de mejora continua, Estudios en Sistemas o afines.")
    nuevo, cambios = cv_guardas.quitar_requisitos_de_habilidades(cv)
    assert "• **Análisis:** relevamiento, Propuesta de mejora continua." in nuevo
    assert cambios == ["se quitó de habilidades «Estudios en Sistemas o afines»"]


@pytest.mark.parametrize("prompt_name", ["WRITER_PROMPT", "WRITER_PROMPT_COMPLETO"])
def test_prompts_sin_caracter_conservador(prompt_name):
    """Creador, 09/11: la ausencia de evidencia no es evidencia de ausencia; lo transferible entra con los
    términos de la oferta, pero títulos, empresas, fechas, años y cifras nunca se inventan."""
    import core.prompts_evaluacion as pe
    prompt = getattr(pe, prompt_name)
    assert prompt.count("* TRANSFERENCIA DE CONOCIMIENTOS (sin carácter conservador)") == 1
    assert "mismos términos" in prompt
    assert "NUNCA inventes títulos de rol, empresas, fechas, años de experiencia" in prompt
    for conservador in ("capacidad análoga, nunca como experiencia directa", "capacidades equivalentes comprobadas",
                        "Tampoco agregues turnos", "PERFIL: solo hechos de la base"):
        assert conservador not in prompt
