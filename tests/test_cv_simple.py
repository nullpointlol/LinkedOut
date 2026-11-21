"""CV simple para puestos simples: la IA de la selección decide por oferta; el formato sale del perfil
(estructura_cv.cv_simple): todas las empresas con 1 viñeta, habilidades de las secciones elegidas, sin llenar la página
y en lenguaje simple. Candidato inventado."""
import pytest

from core import cv_maestro as m
from core import estructura_cv as e

MAESTRO = """# Experiencia

## Freelance
### Consultor | 2025 - 2026
• Atendí consultas de clientes por WhatsApp y organicé sus pedidos.
• Configuré pipelines de CI/CD en Jenkins para 3 productos.

## Acme
### Analista de Soporte | 2021 - 2025
• Resolví 35 incidentes críticos por mes en servidores Linux con Jira.
• Atendí usuarios por mesa de ayuda y coordiné con otras áreas.
• Documenté 40 servidores y sus errores recurrentes.

## HABILIDADES TÉCNICAS
• **DevOps:** Jenkins, GitLab, SonarQube.
• **Ofimática:** Excel, Google Workspace.

## HABILIDADES INTERPERSONALES
• Comunicación clara con clientes y equipos.
• Organización de tareas y seguimiento de pendientes.
• Calma bajo presión.
"""
PERSONALES = "Ana Prueba\nana.prueba@example.org\n\nEDUCACIÓN\n• Tecnicatura - Instituto Test - 2013\n\nIDIOMAS\n• Inglés: C1\n"
SIMPLE = {"vinetas_por_cargo": 1, "secciones_habilidades": ["habilidades interpersonales"], "lineas_habilidades": 2,
          "llenar_pagina": False, "titulo_habilidades": "HABILIDADES", "titulo_habilidades_en": "SKILLS"}
ESTRUCTURA = e.desde_dato({"empresas": [], "cv_simple": SIMPLE})
ANALISIS = {"puesto": "Asistente de atención al cliente",
            "requisitos": [{"nombre": "Atención al cliente", "estado": "MATCH_DIRECTO", "approved": True}]}


def _puntajes(tipo, extra=True):
    lineas = tuple(m._de_seccion(m._secciones(MAESTRO), "habilidades interpersonales")) if extra else ()
    _, vinetas, items = m.pedido_seleccion(MAESTRO, ANALISIS, "", extra_habilidades=lineas)
    def valor(t):
        return 9 if ("Atendí" in t or "Comunicación" in t or "Organización" in t) else 2
    return m.puntajes_de({"vinetas": {i: valor(t) for i, t in vinetas.items()},
                          "habilidades": {i: valor(t) for i, t in items.items()},
                          "tipo_cv": tipo, "motivo_tipo": "No pide conocimientos técnicos"}, vinetas, items, ANALISIS)


def _borrador(tipo, estructura=ESTRUCTURA):
    return m.seleccionar(MAESTRO, PERSONALES, ANALISIS, titulo="X", estructura=estructura, renglones=200,
                         puntajes=_puntajes(tipo))


def test_la_ia_dice_el_tipo_de_cv_y_por_que():
    p = _puntajes("simple")
    assert p.tipo_cv == "simple" and p.motivo_tipo == "No pide conocimientos técnicos"
    _, vinetas, items = m.pedido_seleccion(MAESTRO, ANALISIS, "")
    assert m.puntajes_de({"vinetas": {i: 5 for i in vinetas}, "habilidades": {i: 5 for i in items},
                          "tipo_cv": "raro"}, vinetas, items).tipo_cv == "completo"
    assert "TIPO DE CV" in m.pedido_seleccion(MAESTRO, ANALISIS, "", simple=ESTRUCTURA.cv_simple)[0]
    # Con el CV general desactivado (o ausente) no se pregunta el tipo
    assert "TIPO DE CV" not in m.pedido_seleccion(MAESTRO, ANALISIS, "")[0]
    assert e.desde_dato({"empresas": [], "cv_simple": {**SIMPLE, "activo": False}}).cv_simple is None


def test_un_cv_simple_lleva_todas_las_empresas_con_su_mejor_vineta_y_habilidades_interpersonales():
    b = _borrador("simple")
    assert b.simple and len(b.experiencia) == 2
    assert [[l.original for l in bl.vinetas] for bl in b.experiencia] == [
        ["Atendí consultas de clientes por WhatsApp y organicé sus pedidos."],
        ["Atendí usuarios por mesa de ayuda y coordiné con otras áreas."]]
    assert [l.texto for l in b.habilidades] == ["Comunicación clara con clientes y equipos.",
                                               "Organización de tareas y seguimiento de pendientes."]
    md = b.markdown()
    assert "## HABILIDADES\n" in md and "Jenkins" not in md and "CI/CD" not in md


def test_un_cv_completo_sigue_igual():
    b = _borrador("completo")
    assert not b.simple and "## HABILIDADES TÉCNICAS" in b.markdown()
    assert sum(len(bl.vinetas) for bl in b.experiencia) > 2


def test_sin_cv_simple_en_el_perfil_va_completo():
    assert not _borrador("simple", estructura=e.desde_dato({"empresas": []})).simple


def test_el_ajuste_de_un_cv_simple_pide_lenguaje_simple():
    b = _borrador("simple")
    pedido = m.pedido_ajuste(b, "oferta", [], [])
    assert "CV SIMPLE" in pedido and "sin nombres de herramientas" in pedido
    assert "CV SIMPLE" not in m.pedido_ajuste(_borrador("completo"), "oferta", [], [])


@pytest.mark.parametrize("dato,error", [
    ({**SIMPLE, "vinetas_por_cargo": 0}, "vinetas_por_cargo"),
    ({k: v for k, v in SIMPLE.items() if k != "llenar_pagina"}, "falta «llenar_pagina»"),
    ({**SIMPLE, "secciones_habilidades": []}, "secciones_habilidades"),
])
def test_cv_simple_del_perfil_se_valida(dato, error):
    assert any(error in x for x in e.validar({"empresas": [], "cv_simple": dato}))


def _flujo(monkeypatch, tipo, titular):
    from tests.cv_final_prueba import es_seleccion
    estructura = e.desde_dato({"empresas": [], "cv_simple": {**SIMPLE, "titular": titular}})

    async def ia(prompt, **kw):
        if es_seleccion(prompt):
            lineas = tuple(m._de_seccion(m._secciones(MAESTRO), "habilidades interpersonales"))
            _, vinetas, items = m.pedido_seleccion(MAESTRO, ANALISIS, "", extra_habilidades=lineas)
            return {"vinetas": {i: 7 for i in vinetas}, "habilidades": {i: 7 for i in items}, "tipo_cv": tipo}
        return {"perfil": "Atención de clientes con organización y seguimiento.", "lineas": [], "keywords_cv": {}}

    class Flujo(m.FlujoCV):
        async def paginas(self, md):
            return 1

    flujo = Flujo(maestro=MAESTRO, personales=PERSONALES, analisis=ANALISIS, oferta="", titulo="ANALISTA DE SOPORTE",
                  estructura=estructura, ia=ia)

    async def auditar(borrador, keywords, maestro):
        return {"approved": True}, []
    monkeypatch.setattr(flujo, "auditar", auditar)
    monkeypatch.setattr(m, "_ticket", lambda **kw: 1)
    return flujo


@pytest.mark.asyncio
@pytest.mark.parametrize("tipo,titular,esperado", [
    ("simple", "Atención al cliente y administración", "ATENCIÓN AL CLIENTE Y ADMINISTRACIÓN"),
    ("simple", "", None),
    ("completo", "Atención al cliente", "ANALISTA DE SOPORTE"),
])
async def test_el_titular_de_un_cv_simple_sale_del_perfil_y_no_el_rol_tecnico(monkeypatch, tipo, titular, esperado):
    flujo = _flujo(monkeypatch, tipo, titular)
    md = (await flujo.generar()).markdown
    lineas = md.splitlines()
    assert lineas[0] == "# ANA PRUEBA"
    if esperado:
        assert lineas[1] == esperado
    else:
        assert "ANALISTA DE SOPORTE" not in md and lineas[1].startswith("ana.prueba@")


def test_el_titular_es_opcional_y_de_texto():
    assert not e.validar({"empresas": [], "cv_simple": SIMPLE})
    assert any("titular" in x for x in e.validar({"empresas": [], "cv_simple": {**SIMPLE, "titular": 3}}))


def test_un_cv_simple_no_menciona_anios_en_el_perfil():
    areas = [{"area": "soporte", "anios": "17"}]
    b = m.seleccionar(MAESTRO, PERSONALES, ANALISIS, titulo="X", estructura=ESTRUCTURA, renglones=200,
                      puntajes=_puntajes("simple"), areas=areas)
    assert m.seleccionar(MAESTRO, PERSONALES, ANALISIS, titulo="X", estructura=ESTRUCTURA, renglones=200,
                         puntajes=_puntajes("completo"), areas=areas).areas == areas
    assert b.areas == [] and "No pongas cantidad de años" in m.pedido_ajuste(b, "oferta", [], [])
    b.perfil.texto = "Atiendo clientes y organizo pedidos, algo que hago dentro de mis 17 años de experiencia."
    assert any("no menciona años" in h.motivo for h in m.control_perfil(b, []))
    b.perfil.texto = "Atiendo clientes y organizo pedidos con calma y seguimiento."
    assert not any("años" in h.motivo for h in m.control_perfil(b, []))


# ---------------------------------------------------------------- CV general: un cargo por empresa

MAESTRO_VARIOS = MAESTRO.replace("## Acme\n", "## Acme\n### Operador | 2019 - 2021\n• Atendí llamadas de clientes y "
                                               "registré cada pedido.\n\n")
UNO_POR_EMPRESA = e.desde_dato({"empresas": [], "cv_simple": {**SIMPLE, "un_cargo_por_empresa": True}})


def _puntajes_cargos(cargos, tipo="simple"):
    _, vinetas, items = m.pedido_seleccion(MAESTRO_VARIOS, ANALISIS, "", simple=UNO_POR_EMPRESA.cv_simple)
    return m.puntajes_de({"vinetas": {i: (9 if "Atend" in t else 2) for i, t in vinetas.items()},
                          "habilidades": {i: 5 for i in items}, "tipo_cv": tipo, "cargos": cargos},
                         vinetas, items, ANALISIS)


def _borrador_general(cargos, tipo="simple"):
    return m.seleccionar(MAESTRO_VARIOS, PERSONALES, ANALISIS, titulo="X", estructura=UNO_POR_EMPRESA, renglones=200,
                         puntajes=_puntajes_cargos(cargos, tipo))


def test_el_pedido_pide_un_cargo_por_empresa_solo_si_el_perfil_lo_activa():
    assert "CARGO POR EMPRESA" in m.pedido_seleccion(MAESTRO, ANALISIS, "", simple=UNO_POR_EMPRESA.cv_simple)[0]
    assert "CARGO POR EMPRESA" not in m.pedido_seleccion(MAESTRO, ANALISIS, "", simple=ESTRUCTURA.cv_simple)[0]


def test_un_cv_general_lleva_un_solo_cargo_por_empresa_con_el_periodo_y_su_mejor_vineta():
    b = _borrador_general({"Acme": "Analista de soporte y atención", "Freelance": "Consultor"})
    assert [bl.encabezado for bl in b.experiencia] == [
        "### Consultor | Freelance (2025 - 2026)", "### Analista de soporte y atención | Acme (2019 - 2025)"]
    assert all(len(bl.vinetas) == 1 for bl in b.experiencia)
    assert b.experiencia[1].vinetas[0].original.startswith("Atendí")
    assert not [h for h in m.control_estructura(b, MAESTRO_VARIOS, UNO_POR_EMPRESA) if "cargo" in h.motivo]


def test_un_cargo_inventado_vuelve_al_cargo_real_mas_reciente():
    b = _borrador_general({"Acme": "Repositor de góndola", "Freelance": ""})
    assert [bl.encabezado for bl in b.experiencia] == [
        "### Consultor | Freelance (2025 - 2026)", "### Analista de Soporte | Acme (2019 - 2025)"]


def test_un_cv_completo_no_agrupa_los_cargos():
    b = _borrador_general({"Acme": "Analista de soporte"}, tipo="completo")
    assert len([bl for bl in b.experiencia if bl.empresa == "Acme"]) == 2


def test_un_cargo_agregado_a_mano_en_el_cv_general_se_marca():
    b = _borrador_general({"Acme": "Analista de soporte y atención"})
    b.experiencia[1].encabezado = "### Analista de Soporte | Acme (2021 - 2025)"
    assert any("un solo cargo" in h.motivo for h in m.control_estructura(b, MAESTRO_VARIOS, UNO_POR_EMPRESA))


@pytest.mark.parametrize("texto,queda", [
    ("Atendí por teléfono, en inglés (C1), a equipos de Estados Unidos.",
     "Atendí por teléfono, en inglés, a equipos de Estados Unidos."),
    ("Analista con inglés C1 y foco en clientes.", "Analista con inglés y foco en clientes."),
    ("Analyst with C1 English and client focus.", "Analyst with English and client focus."),
])
def test_el_nivel_de_idioma_va_solo_en_idiomas(texto, queda):
    b = _borrador("completo")
    b.perfil.texto = texto
    h = [x for x in m.control_idiomas(b) if x.linea == "P1"]
    assert h and texto.replace(h[0].senal["frase"], h[0].senal["reescritura"]) == queda
    b.idiomas = ["Inglés: Avanzado"]  # el perfil lo dice sin nivel MCER: igual va solo en IDIOMAS
    assert [x for x in m.control_idiomas(b) if x.linea == "P1"]
    b.perfil.texto = "Analista con inglés y foco en clientes."
    assert not m.control_idiomas(b)
    assert "Va una sola vez" in m.pedido_ajuste(b, "oferta", [], [])


def test_el_pdf_de_un_cv_sin_titular_no_inventa_un_puesto():
    from core.pdf_generator import generate_cv_pdf_filename
    assert generate_cv_pdf_filename("", "Ana Prueba") == "CV - Ana Prueba.pdf"
    assert generate_cv_pdf_filename("Analista de Datos", "Ana Prueba").startswith("CV - Ana Prueba - ")
