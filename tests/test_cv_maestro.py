"""CV desde el maestro: selección por código del CV maestro, una llamada de ajuste en JSON por línea con las pautas
de una sola fuente, auditoría independiente, una sola corrección y ticket si falla. Candidato inventado (Sam Ortega) y
IA simulada: nunca el perfil real ni AGY."""
import re
from unittest.mock import AsyncMock

import pytest

from core import cv_maestro as cm
from core import pautas_cv
from tests.cv_final_prueba import es_seleccion, puntajes_falsos

MAESTRO = """# Experiencia profesional

## Acme Datos
### Analista Funcional | 2020 - 2024
• Relevé requerimientos con usuarios y los traduje en historias de usuario y criterios de aceptación.
• Escribí consultas SQL para validar datos entre la base y el aplicativo en cada pase a producción.
• Probé servicios REST con Postman antes de cada despliegue.
• Coordiné reuniones semanales con el equipo de diseño gráfico.
### Analista Jr. | 2018 - 2020
• Atendí incidentes de usuarios y los documenté en Jira hasta el cierre.

## Globex
### Operador de Monitoreo | 2014 - 2018
• Monitoreé alertas de infraestructura con ServiceNow y escalé cada evento según su severidad.
• Armé en Excel una planilla que ordenaba las guardias del mes.

## HABILIDADES TÉCNICAS
• **Bases de datos:** SQL, PostgreSQL.
• **Integraciones:** REST, Postman, SOAP.
• **Gestión:** Jira, ServiceNow.
"""
PERSONALES = """Sam Ortega
Puesto: Analista Funcional

Villa Test, Buenos Aires | +54 9 11 0000-0000 | sam.ortega@example.org

EDUCACIÓN
Tecnicatura en Sistemas - Instituto Test - 2010 - 2013

IDIOMAS
• Inglés: intermedio
• Español: nativo
"""
ANALISIS = {
    "puesto": "Analista de Datos", "empresa": "Initech", "idioma_cv": "es",
    "requisitos": [
        {"nombre": "SQL", "estado": "MATCH_DIRECTO", "approved": True, "tipo": "REQUISITO_EXCLUYENTE"},
        {"nombre": "APIs REST", "estado": "MATCH_DIRECTO", "approved": True, "tipo": "REQUISITO_EXCLUYENTE"},
        {"nombre": "Power BI", "estado": "TRANSFERIBLE", "approved": True, "tipo": "REQUISITO_DESEABLE"},
        {"nombre": "Kubernetes", "estado": "BRECHA_REAL", "approved": False, "tipo": "REQUISITO_DESEABLE"},
    ],
}


def _borrador(lineas=40):
    return cm.seleccionar(MAESTRO, PERSONALES, ANALISIS, idioma="es", titulo="ANALISTA DE DATOS", renglones=lineas)


# ---------------------------------------------------------------- selección por código

def test_la_seleccion_mantiene_cada_cargo_real_con_sus_fechas():
    # cada cargo con su título y sus fechas, nunca uno con el período de toda la empresa
    b = _borrador()
    encabezados = [bloque.encabezado for bloque in b.experiencia]
    assert encabezados == ["### Analista Funcional | Acme Datos (2020 - 2024)", "### Analista Jr. | Acme Datos (2018 - 2020)",
                           "### Operador de Monitoreo | Globex (2014 - 2018)"]
    assert all(bloque.vinetas for bloque in b.experiencia)


def test_la_seleccion_prioriza_lo_que_pide_la_oferta_y_deja_afuera_lo_que_no_suma():
    minimo = _borrador(lineas=0)  # una viñeta por empresa, con el lugar del perfil reservado
    minimo.perfil.texto = "x" * (pautas_cv.LARGO_MAX_PERFIL - 40)
    b = _borrador(lineas=cm.renglones_de(minimo) + 1)  # lugar para 1 viñeta corta más
    acme = [l.texto for l in b.experiencia[0].vinetas]
    assert any("SQL" in t for t in acme) and any("REST" in t for t in acme)
    assert not any("diseño gráfico" in t for t in acme)


def test_las_vinetas_son_el_texto_del_maestro_sin_cambios():
    b = _borrador()
    textos_maestro = {l.strip().lstrip("• ").strip() for l in MAESTRO.splitlines() if l.strip().startswith("•")}
    for bloque in b.experiencia:
        for l in bloque.vinetas:
            assert l.texto in textos_maestro and l.texto == l.original


def test_el_markdown_tiene_las_secciones_y_los_datos_del_perfil():
    md = _borrador().markdown()
    assert md.startswith("# SAM ORTEGA\nANALISTA DE DATOS\n")
    for seccion in ("## PERFIL", "## EXPERIENCIA", "## HABILIDADES TÉCNICAS", "## EDUCACIÓN", "## IDIOMAS"):
        assert seccion in md
    assert "Tecnicatura en Sistemas" in md and "Inglés: intermedio" in md


def test_los_terminos_habilitados_salen_del_matching_dinamico():
    obligatorias, habilitados = cm.terminos_del_matching(ANALISIS)
    assert obligatorias == ["SQL", "APIs REST"]
    assert "Power BI" in habilitados and "Kubernetes" not in habilitados


# ---------------------------------------------------------------- ajuste en JSON por línea

def test_el_ajuste_solo_cambia_las_lineas_que_existen():
    b = _borrador()
    ids = [l.id for l in b.editables()]
    cambios = {"perfil": "Analista con foco en datos.", "lineas": [
        {"id": ids[1], "texto": "Texto nuevo para la viñeta."}, {"id": "V999", "texto": "no existe"}]}
    aplicados = cm.aplicar(b, cambios)
    assert aplicados == {ids[1]} | {"P1"}
    assert b.por_id(ids[1]).texto == "Texto nuevo para la viñeta." and b.perfil.texto == "Analista con foco en datos."


def test_las_pautas_del_pedido_salen_de_las_reglas_de_la_auditoria():
    texto = pautas_cv.pautas(["SQL", "APIs REST"], idioma="es")
    from core.prompts_evaluacion import SIGNS_OF_AI_WRITING_WORDS
    assert all(p in texto for p in list(SIGNS_OF_AI_WRITING_WORDS)[:20])
    assert "SQL" in texto and "APIs REST" in texto
    assert str(pautas_cv.LARGO_MAX_VINETA) in texto and str(pautas_cv.LARGO_MAX_PERFIL) in texto


# ---------------------------------------------------------------- auditoría independiente

def test_una_cifra_que_no_esta_en_el_maestro_es_un_invento():
    b = _borrador()
    vineta = b.experiencia[0].vinetas[0]
    vineta.texto = vineta.original + " Reduje los errores un 40%."
    hallazgos = cm.inventos(b, MAESTRO)
    assert [h.linea for h in hallazgos] == [vineta.id] and "40" in hallazgos[0].motivo


def test_los_hallazgos_del_auditor_se_ubican_en_sus_lineas():
    b = _borrador()
    vineta = b.experiencia[0].vinetas[0]
    vineta.texto = "Lideré una iniciativa robusta y sinérgica que mejoró la experiencia del cliente."
    reporte = {"approved": False, "anti_ai": {"detected_words": ["robusta", "sinérgica"], "senales_llm": []},
               "ats": {"missing_keywords": ["APIs REST"], "keyword_coverage": 0.5}, "hallazgos_unificados": {"lista_observaciones": ["x"]}}
    hallazgos = cm.ubicar(b, reporte)
    assert {h.linea for h in hallazgos if "robusta" in h.motivo} == {vineta.id}
    assert any(h.linea is None and "APIs REST" in h.motivo for h in hallazgos)


@pytest.mark.asyncio
async def test_aprobado_a_la_primera_no_corrige(monkeypatch, tmp_path):
    flujo = _flujo(monkeypatch, tmp_path, auditorias=[[]])
    resultado = await flujo.generar()
    assert resultado.aprobado and resultado.correcciones == 0
    assert flujo.ia.await_count == 2  # la selección con IA y una sola llamada de ajuste
    assert resultado.reporte["seleccion"] == "ia"


@pytest.mark.asyncio
async def test_una_sola_correccion_con_las_lineas_que_fallaron(monkeypatch, tmp_path):
    flujo = _flujo(monkeypatch, tmp_path, auditorias=[[cm.Hallazgo("V1", "impacto vago")], []])
    resultado = await flujo.generar()
    assert resultado.aprobado and resultado.correcciones == 1
    pedido_correccion = flujo.ia.await_args_list[2].args[0]  # selección, ajuste, corrección
    assert "V1" in pedido_correccion and "impacto vago" in pedido_correccion


@pytest.mark.asyncio
async def test_si_la_correccion_no_alcanza_abre_un_ticket_y_falla_con_el_motivo(monkeypatch, tmp_path):
    flujo = _flujo(monkeypatch, tmp_path, auditorias=[[cm.Hallazgo("V1", "impacto vago")]] * 2)
    tickets = []
    monkeypatch.setattr(cm, "_ticket", lambda **kw: tickets.append(kw) or 1)
    with pytest.raises(cm.CVNoAprobado, match="impacto vago"):
        await flujo.generar()
    assert flujo.ia.await_count == 3  # selección + ajuste + una sola corrección
    assert tickets and "V1" in tickets[0]["mensaje"] and tickets[0]["datos"]["lineas"]


def _flujo(monkeypatch, tmp_path, auditorias):
    """Flujo con la IA, la auditoría y el contador de páginas simulados."""
    ajuste = {"perfil": "Analista de datos con SQL y APIs REST.", "lineas": [], "keywords_cv": {}}
    ia = AsyncMock(side_effect=lambda prompt, **kw: puntajes_falsos(prompt) if es_seleccion(prompt) else ajuste)
    secuencia = iter(auditorias)

    async def auditar(borrador, keywords, maestro):
        return ({"approved": True}, next(secuencia))

    flujo = cm.FlujoCV(maestro=MAESTRO, personales=PERSONALES, analisis=ANALISIS, oferta="Buscamos analista de datos.",
                       titulo="ANALISTA DE DATOS", idioma="es", ia=ia)
    monkeypatch.setattr(flujo, "auditar", auditar)
    monkeypatch.setattr(flujo, "paginas", AsyncMock(return_value=1))
    return flujo


@pytest.mark.asyncio
async def test_la_traduccion_del_maestro_queda_pendiente_de_aprobacion():
    traducido = MAESTRO.replace("Analista Funcional", "Functional Analyst")
    ia = AsyncMock(return_value={"maestro_en": traducido})
    texto = await cm.traducir_maestro(MAESTRO, PERSONALES, ia)
    assert cm.maestro_en_pendiente(texto) and "Functional Analyst" in texto
    assert "Tecnicatura en Sistemas" in ia.await_args.args[0]  # educación e idiomas van a traducirse también


def test_las_cifras_de_un_nombre_habilitado_no_son_inventos_pero_las_cantidades_si():
    b = _borrador()
    vineta = b.experiencia[0].vinetas[0]
    vineta.texto = vineta.original + " Usé Office 365 y di soporte Tier 2."
    terminos = "Office 365, Exchange o Google Workspace\n4 years of experience providing Tier 2 Technical Support"
    assert cm.inventos(b, MAESTRO, PERSONALES, terminos) == []
    vineta.texto = vineta.original + " Sumé 4 años de soporte."
    assert [h.linea for h in cm.inventos(b, MAESTRO, PERSONALES, terminos)] == [vineta.id]


def test_las_pautas_traen_lo_que_rechaza_la_lente_de_estilo_y_el_nucleo_de_cada_keyword():
    from core.prompts_evaluacion import PATRONES_SENALES_IA
    texto = pautas_cv.pautas(["Medición de resultados y eficiencia"], idioma="es", cubiertas=["SQL"])
    assert all(p in texto for p in PATRONES_SENALES_IA)
    assert "Medición, resultados, eficiencia" in texto and "Ya cubiertas" in texto and "SQL" in texto


def test_una_vineta_que_remite_a_la_anterior_nunca_queda_sola():
    maestro = MAESTRO.replace("• Armé en Excel una planilla que ordenaba las guardias del mes.",
                              "• Armé en Excel una planilla que ordenaba las guardias del mes.\n"
                              "• Mantuve esas guardias con SQL y APIs REST durante cada pase a producción.")
    b = cm.seleccionar(maestro, PERSONALES, ANALISIS, idioma="es", titulo="X", renglones=200)
    globex = [l.texto for l in b.experiencia[2].vinetas]
    assert globex.index("Mantuve esas guardias con SQL y APIs REST durante cada pase a producción.") == \
        globex.index("Armé en Excel una planilla que ordenaba las guardias del mes.") + 1
    minimo = cm.seleccionar(maestro, PERSONALES, ANALISIS, idioma="es", titulo="X", renglones=0)
    assert not minimo.experiencia[2].vinetas[0].texto.startswith("Mantuve esas")


def test_el_ajuste_conserva_la_negrita_de_la_etiqueta_de_habilidades():
    b = _borrador()
    s1 = b.habilidades[0]
    cm.aplicar(b, {"lineas": [{"id": s1.id, "texto": "**Bases de datos:** SQL, PostgreSQL y consultas de validación."}]})
    assert s1.texto.startswith("**Bases de datos:**")
    cm.aplicar(b, {"lineas": [{"id": s1.id, "texto": "• SQL y PostgreSQL."}]})
    assert s1.texto == "SQL y PostgreSQL."


def test_el_titulo_de_un_cv_en_ingles_es_el_rol_real_traducido():
    maestro_en = MAESTRO.replace("Analista Funcional", "Functional Analyst").replace("Analista Jr.", "Junior Analyst") \
        .replace("Operador de Monitoreo", "Monitoring Operator")
    assert cm.titulo_en_ingles("ANALISTA FUNCIONAL", MAESTRO, maestro_en) == "Functional Analyst"
    assert cm.titulo_en_ingles("Líder de Datos", MAESTRO, maestro_en) is None


def test_educacion_terciario_siempre_cursos_si_la_oferta_los_pide_y_secundario_solo_sin_terciario():
    items = ["Tecnicatura en Sistemas - Instituto Test - 2010 - 2013", "Curso de COBOL - Acme - 2008",
             "Curso de SQL avanzado - Acme - 2015", "Bachiller en Ciencias - Escuela Test - 2007"]
    assert cm.seleccionar_educacion(items, ANALISIS) == [items[0], items[2]]
    assert cm.seleccionar_educacion([items[1], items[3]], ANALISIS) == [items[3]]


def test_un_rol_relevante_lleva_dos_vinetas_y_uno_que_no_suma_una():
    # 1 viñeta solo en el cargo que no suma para la oferta (tests/test_cv_relevancia.py)
    b = _borrador(lineas=0)
    acme, jr, globex = b.experiencia  # Acme (2020-2024) suma por SQL y REST; el Jr. y Globex no
    assert acme.relevante and len(acme.vinetas) == acme.minimo == 2
    assert not jr.relevante and not globex.relevante
    assert len(jr.vinetas) == len(globex.vinetas) == 1


def _perfil(texto, areas=None):
    b = cm.seleccionar(MAESTRO, PERSONALES, ANALISIS, idioma="es", titulo="ANALISTA DE DATOS",
                       areas=areas if areas is not None else [{"area": "análisis de datos", "anios": "más de 5"}])
    b.perfil.texto = texto
    return [h.motivo for h in cm.control_perfil(b, ["SQL", "APIs REST", "Power BI"])]


def test_el_perfil_es_un_resumen_alineado_con_anios_del_area_y_fuertes():
    assert _perfil("Analista de datos con más de 5 años de experiencia en análisis de datos. Experiencia en SQL y APIs REST.") == []
    assert any("viñeta más" in m for m in _perfil("Implementé consultas SQL y APIs REST como analista hace más de 5 años."))
    assert any("años del área" in m for m in _perfil("Analista de datos. Experiencia en SQL y APIs REST."))
    assert any("no son los del área" in m for m in _perfil("Analista de datos con 9 años en análisis de datos, SQL y APIs REST."))
    assert any("fuertes" in m for m in _perfil("Analista de datos con más de 5 años en análisis de datos."))
    assert any("título" in m for m in _perfil("Experiencia en SQL y APIs REST desde hace más de 5 años en integraciones.", areas=[]))


@pytest.mark.parametrize("texto", [
    "Soy analista de datos con más de 5 años en análisis de datos y SQL y APIs REST.",
    "Gestiono pedidos y atiendo consultas. Analista de datos con más de 5 años con SQL y APIs REST.",
    "Analista de datos con más de 5 años en análisis de datos. Me desempeño con SQL y APIs REST.",
    "Analista de datos con más de 5 años en análisis de datos; mi fuerte es SQL y APIs REST.",
])
def test_el_perfil_en_primera_persona_se_rechaza(texto):
    assert any("3ª persona" in m for m in _perfil(texto))


@pytest.mark.parametrize("texto", [
    "Analista de datos con más de 5 años en análisis de datos. Desarrollo de reportes con SQL y APIs REST.",
    "Analista de datos con más de 5 años en análisis de datos. Trabajo en equipo con SQL y APIs REST.",
])
def test_un_sustantivo_que_parece_verbo_no_es_primera_persona(texto):
    assert not any("3ª persona" in m for m in _perfil(texto))


def test_las_pautas_del_perfil_traen_los_anios_y_el_titulo():
    texto = pautas_cv.pautas([], titulo="ANALISTA DE DATOS", areas=[{"area": "análisis de datos", "anios": "más de 5"}],
                             fuertes=["SQL"])
    assert "ANALISTA DE DATOS" in texto and "análisis de datos: más de 5 años" in texto and "SQL" in texto


@pytest.mark.asyncio
async def test_la_pagina_se_llena_antes_del_ajuste_y_despues_solo_se_saca(monkeypatch, tmp_path):
    flujo = _flujo(monkeypatch, tmp_path, auditorias=[[cm.Hallazgo("V1", "impacto vago")], []])
    llamadas = []
    original = flujo._a_una_pagina

    async def registrar(b, keywords, sumar=False):
        llamadas.append((sumar, flujo.ia.await_count))
        await original(b, keywords, sumar)

    monkeypatch.setattr(flujo, "_a_una_pagina", registrar)
    await flujo.generar()
    assert llamadas == [(True, 1), (False, 2), (False, 3)]  # suma solo antes del ajuste (después de la selección)


def test_un_nivel_de_idioma_de_la_oferta_es_un_invento():
    b = _borrador()
    vineta = b.experiencia[1].vinetas[0]
    vineta.texto = vineta.original + " Atendí en inglés B2+ a equipos remotos."
    hallazgos = cm.inventos(b, MAESTRO, PERSONALES, "English B2+ / C1")
    assert [h.linea for h in hallazgos] == [vineta.id] and "B2+" in hallazgos[0].motivo
    assert cm.inventos(b, MAESTRO, PERSONALES.replace("intermedio", "B2"), "English B2+ / C1") == []


def test_una_vineta_que_continua_otra_etapa_no_se_elige():
    maestro = MAESTRO.replace("• Atendí incidentes de usuarios y los documenté en Jira hasta el cierre.",
                              "• Atendí incidentes de usuarios y los documenté en Jira hasta el cierre.\n"
                              "• Seguí con SQL y APIs REST, ahora con incidentes críticos en lugar de los de la etapa anterior.")
    b = cm.seleccionar(maestro, PERSONALES, ANALISIS, idioma="es", titulo="X", renglones=200)
    assert not any("etapa anterior" in l.texto for bl in b.experiencia for l in bl.vinetas + bl.reserva)


@pytest.mark.asyncio
async def test_el_maestro_en_ingles_se_traduce_de_nuevo_si_cambio_el_castellano(tmp_path):
    (tmp_path / "experiencia.txt").write_text(MAESTRO, encoding="utf-8")
    (tmp_path / "personales.txt").write_text(PERSONALES, encoding="utf-8")
    ia = AsyncMock(return_value={"maestro_en": MAESTRO.replace("Analista Funcional", "Functional Analyst")})
    texto, nuevo = await cm.asegurar_maestro_en(tmp_path, ia)
    assert nuevo and cm.maestro_en_pendiente(texto)
    _, nuevo = await cm.asegurar_maestro_en(tmp_path, ia)
    assert not nuevo and ia.await_count == 1  # vigente: no se traduce otra vez
    (tmp_path / "personales.txt").write_text(PERSONALES.replace("intermedio", "C1"), encoding="utf-8")
    _, nuevo = await cm.asegurar_maestro_en(tmp_path, ia)
    assert nuevo and ia.await_count == 2  # cambió el idioma del castellano: se traduce de nuevo


def test_una_vineta_larga_del_maestro_vale_tal_cual_pero_la_ia_no_la_alarga():
    b = _borrador()
    v = b.experiencia[0].vinetas[0]
    v.original = v.texto = "Relevé " + "requerimientos con usuarios " * 8  # 231 caracteres en el maestro
    assert cm.largos(b) == []
    v.texto = v.original + "y más texto agregado."
    assert [h.linea for h in cm.largos(b)] == [v.id]


@pytest.mark.asyncio
async def test_el_cv_ajustado_se_entrega_antes_de_auditar(monkeypatch, tmp_path):
    flujo = _flujo(monkeypatch, tmp_path, auditorias=[[]])
    orden = []
    flujo.al_ajustar = lambda md: orden.append(("ajustado", "## EXPERIENCIA" in md))
    auditar = flujo.auditar

    async def auditar_y_anotar(*a):
        orden.append(("auditoria", True))
        return await auditar(*a)

    monkeypatch.setattr(flujo, "auditar", auditar_y_anotar)
    await flujo.generar()
    assert orden == [("ajustado", True), ("auditoria", True)]


# ---------------------------------------------------------------- 16/11: idioma mezclado y título en mayúsculas

def test_un_cv_en_ingles_no_lleva_castellano():
    b = _borrador()
    b.idioma = cm.EN
    b.perfil.texto = "Data analyst with 5 years of experience. Experience in documentación técnica y equipos."
    # (el borrador de prueba tiene viñetas en castellano: se mira solo el perfil)
    hallazgos = [h for h in cm.control_mezcla_idioma(b, MAESTRO) if h.linea == "P1"]
    assert len(hallazgos) == 1 and "«documentación»" in hallazgos[0].motivo and "«y»" in hallazgos[0].motivo
    b.perfil.texto = "Data analyst with 5 years of experience. Experience in technical documentation and teams."
    assert not [h for h in cm.control_mezcla_idioma(b, MAESTRO) if h.linea == "P1"]
    b.idioma = "es"
    b.perfil.texto = "Analista con experiencia en documentación técnica y equipos."
    assert cm.control_mezcla_idioma(b, MAESTRO) == []


def test_un_nombre_con_tilde_del_maestro_no_es_castellano():
    b = _borrador()
    b.idioma = cm.EN
    b.perfil.texto = "Data analyst with clients in Bogotá."
    assert not [h for h in cm.control_mezcla_idioma(b, MAESTRO + "\n• Supported clients in Bogotá.") if h.linea == "P1"]


@pytest.mark.parametrize("perfil,esperado", [
    ("ANALISTA TÉCNICO FUNCIONAL con 7 años de experiencia.", "Analista técnico funcional con 7 años de experiencia."),
    ("Perfil sólido. ANALISTA TÉCNICO FUNCIONAL con 7 años.", "Perfil sólido. Analista técnico funcional con 7 años."),
    ("Experiencia como analista técnico funcional.", "Experiencia como analista técnico funcional."),
])
def test_el_titulo_en_mayusculas_va_como_texto_en_el_perfil(perfil, esperado):
    assert cm.restaurar_siglas(perfil, "ANALISTA TÉCNICO FUNCIONAL", set()) == esperado


def test_el_titulo_en_mayusculas_conserva_sus_siglas():
    assert cm.restaurar_siglas("TÉCNICO DE SOPORTE IT con 5 años.", "TÉCNICO DE SOPORTE IT", {"IT"}) == "Técnico de soporte IT con 5 años."
