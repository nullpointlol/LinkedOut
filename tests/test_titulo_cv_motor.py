"""Contrato del motor; todas las fuentes y selecciones son sintéticas."""
import hashlib
import importlib.util
import os
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from core import perfil_areas, profile_parser
from core.completitud import REGLA_PUESTO, problemas_puesto
from core.cv_guardas import corregir_encabezados, quitar_requisitos_de_habilidades
from core.docx_generator import generate_cv_filename
from core.prompts_evaluacion import TRANSFERENCIA_DE_CONOCIMIENTOS


ROLES = (
    "Product Owner Analítico", "Ingeniero DevOps Jr.", "Analista de Soporte de Aplicaciones",
    "Analista Técnico Funcional", "Gestor de Incidentes y Guardias ITSM",
    "Técnico de Monitoreo NOC", "Programador Java",
)
NORMALIZADOS = (
    "PRODUCT OWNER ANALÍTICO", "INGENIERO DEVOPS", "ANALISTA DE SOPORTE DE APLICACIONES",
    "ANALISTA TÉCNICO FUNCIONAL", "GESTOR DE INCIDENTES",
    "TÉCNICO DE MONITOREO NOC", "PROGRAMADOR JAVA",
)
EXP = "\n\n".join(
    f"## Empresa {i}\n### {rol} | {2025-i} - {2026-i}\n• Python ServiceNow Java SQL; tareas reales."
    for i, rol in enumerate(ROLES)
)
PER = "# Alex Prueba\nVilla Ejemplo, Argentina | alex@example.org | +54 11 12345678\nPuesto: Analista Técnico Funcional\n"
AREAS = [dict(area=a, anios=n) for a, n in (
    ("Análisis técnico funcional", "8"), ("Soporte e incidentes", "8"),
    ("Monitoreo e ITSM", "más de 5"), ("Desarrollo de software", "más de 3"),
    ("Gestión de producto", "2"), ("DevOps", "2"),
)]
COLA = "\n\n## EXPERIENCIA\n" + EXP + "\n\n## HABILIDADES\nDocker, Cloud AWS o GCP, Jenkins, Linux.\n"
CV = "# Alex Prueba\nPYTHON DEVELOPER\nalex@example.org | Villa Ejemplo\n\n## PERFIL\nExperiencia comprobable.\n\nSegundo párrafo intacto.\n" + COLA
TECNOLOGIAS = "python,java,.net,c#,javascript,typescript,node,react,angular,php,go,ruby,salesforce,servicenow,sap,jde,dynamics,aws,azure,gcp,kubernetes,n8n,sql,power bi,copilot".split(",")


def _invariantes():
    # invariante: ocho títulos rechazados por la guarda base.
    for titulo in ("ANALISTA FUNCIONAL / SOPORTE TECNICO", "ANALISTA (SQL)", "CONVOCATORIA ABIERTA",
                   "BUSQUEDA ANALISTA", "DESARROLLADOR TRAINEE", "HEAD DE SISTEMAS", "PRODUCT MANAGER", "ENGINEER III"):
        assert problemas_puesto(titulo)
    # invariante: parser conserva los siete roles, empresas y fechas, incluso Jr.
    roles = profile_parser.parse_roles(EXP)
    assert tuple(r["rol"] for r in roles) == ROLES
    assert [(r["empresa"], r["desde"], r["hasta"]) for r in roles] == [
        (f"Empresa {i}", str(2025-i), str(2026-i)) for i in range(7)]
    corregido, _, _ = corregir_encabezados(CV, roles)
    assert [l for l in corregido.splitlines() if l.startswith("### ")] == [l for l in EXP.splitlines() if l.startswith("### ")]
    # invariante: transferencia literal y herramientas permitidas intactas.
    assert hashlib.sha256(TRANSFERENCIA_DE_CONOCIMIENTOS.encode()).hexdigest() == "b76b1f4279b7abc22746c27066c155f7f7d5430ec49a0ec7713f0676738953a5"
    assert quitar_requisitos_de_habilidades(CV)[0] == CV
    # invariante: regla pública y export toman el titular del documento.
    assert REGLA_PUESTO.startswith("La línea bajo el nombre")
    from server import _puesto_export
    assert _puesto_export({"cv_markdown": CV.replace("PYTHON DEVELOPER", "PROGRAMADOR JAVA"), "titulo": "Python Developer"}) == "PROGRAMADOR JAVA"
    assert "PROGRAMADOR-JAVA" in generate_cv_filename("PROGRAMADOR JAVA", "Alex Prueba")
    if os.environ.get("INVARIANT_CHECK") == "1":
        return True
    assert importlib.util.find_spec("core.titulo_cv") is not None, "Falta implementar el motor del título"
    return False


@pytest.fixture(autouse=True)
def fuentes_falsas(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(profile_parser, "DEFAULT_CANDIDATO_DIR", tmp_path)
    (tmp_path / "experiencia.txt").write_text(EXP, encoding="utf-8")
    (tmp_path / "personales.txt").write_text(PER, encoding="utf-8")
    monkeypatch.setattr(perfil_areas, "anios_por_area_en_cache", Mock(return_value=AREAS))


def _bridge(puesto="ANALISTA TÉCNICO FUNCIONAL"):
    b = Mock()
    b.run_prompt = AsyncMock(return_value={"puesto": puesto})
    return b


@pytest.mark.asyncio
@pytest.mark.parametrize("oferta,caso,titulo", [
    ("Analista Funcional SSR", 1, "ANALISTA FUNCIONAL"),
    ("Analista Funcional SR", 1, "ANALISTA FUNCIONAL"),
    ("Técnico/a Semi Senior de Sistemas y Soporte Operativo", 1, "ANALISTA DE SOPORTE DE APLICACIONES"),
    ("Analista funcional / Soporte N1", 1, "ANALISTA FUNCIONAL"),
    ("Business Technical Analyst Ssr | Rubro Financiero", 1, "BUSINESS TECHNICAL ANALYST"),
    ("Consultor Técnico / ServiceNow", 1, "CONSULTOR TÉCNICO SERVICENOW"),
    ("Product Manager LATAM", 2, "PRODUCT OWNER ANALÍTICO"),
    ("Python Developer", 3, "ANALISTA TÉCNICO FUNCIONAL"),  # coordinador 10/11: desarrollo → caso 3
    ("DevOps Engineer", 2, "INGENIERO DEVOPS"),
    ("Desarrollador .NET Full Stack", 3, "ANALISTA TÉCNICO FUNCIONAL"),
    ("AI Engineer", 3, "ANALISTA TÉCNICO FUNCIONAL"),
    ("Instructores en Informática", 3, "ANALISTA TÉCNICO FUNCIONAL"),
])
async def test_matriz_tres_casos(oferta, caso, titulo):
    if _invariantes():
        return
    from core.titulo_cv import decidir_titulo
    b = _bridge()
    d = await decidir_titulo({"analisis": {"puesto": oferta}}, experiencia=EXP, personales=PER, anios_por_area=AREAS, bridge=b)
    assert (d.caso, d.titulo) == (caso, titulo)
    # Caso 3: sin roles de desarrollo (no es el perfil actual, coordinador 10/11).
    esperados = NORMALIZADOS if caso != 3 else NORMALIZADOS[:-1]
    assert d.roles_reales == esperados
    assert [len(r.split()) for r in d.roles_reales] == [3, 2, 5, 3, 3, 4, 2][:len(esperados)]
    assert not problemas_puesto(d.titulo)
    if caso != 3:
        b.run_prompt.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("tech", TECNOLOGIAS)
async def test_normalizacion_y_tecnologias(tech):
    if _invariantes():
        return
    from core.titulo_cv import decidir_titulo
    b = _bridge()
    exp = "## Empresa X\n### Analista de Soporte | 2020 - Actualidad\n• Trabajo operativo."
    for presente in (False, True):
        d = await decidir_titulo({"titulo": f"Analista de Soporte {tech}"}, experiencia=exp + (f"\n• {tech}" if presente else ""), personales=PER, anios_por_area=[], bridge=b)
        assert d.caso == (2 if presente else 3)
        assert not problemas_puesto(d.titulo)
    # Palabras completas: Java no se infiere desde JavaScript, Go no desde negocio.
    d = await decidir_titulo({"titulo": "Java Developer"}, experiencia=EXP.replace("Java", "JavaScript"), personales=PER, anios_por_area=[], bridge=b)
    assert d.caso == 3
    d = await decidir_titulo({"titulo": "Analista de negocios"}, experiencia=exp, personales=PER, anios_por_area=[], bridge=b)
    assert d.caso == 3  # clave sin rol funcional: selector


@pytest.mark.asyncio
@pytest.mark.parametrize("anios,caso", [("4", 2), ("5", 1), ("más de 6", 1)])
async def test_cache_y_precedencia(anios, caso):
    if _invariantes():
        return
    from core.titulo_cv import decidir_titulo
    b = _bridge()
    d = await decidir_titulo({"titulo": "Analista funcional"}, anios_por_area=[{"area": "funcional", "anios": anios}], bridge=b)
    assert d.caso == caso
    b.run_prompt.assert_not_awaited()
    d = await decidir_titulo({"titulo": "Analista funcional", "analisis": {"grupo_rol": "C"}}, bridge=b)
    assert d.caso == 3
    d = await decidir_titulo({"titulo": "AI Engineer", "analisis": {"puesto": "Analista funcional"}}, bridge=b)
    assert d.caso == 1 and d.puesto_oferta == "Analista funcional"


@pytest.mark.asyncio
@pytest.mark.parametrize("respuesta", [{"puesto": "AI ENGINEER"}, {}, {"puesto": 42}, {"puesto": "HEAD DE SISTEMAS"}, TimeoutError()])
async def test_ia_lista_cerrada_y_fallback(respuesta):
    if _invariantes():
        return
    from core.titulo_cv import decidir_titulo
    b = _bridge()
    if isinstance(respuesta, Exception):
        b.run_prompt.side_effect = respuesta
    else:
        b.run_prompt.return_value = respuesta
    d = await decidir_titulo({"titulo": "AI Engineer"}, bridge=b)
    assert d.titulo == "ANALISTA TÉCNICO FUNCIONAL"
    assert b.run_prompt.await_count == 1
    assert b.run_prompt.call_args.kwargs == dict(json_mode=True, timeout_seconds=240, effort="medium", cwd=b.run_prompt.call_args.kwargs["cwd"])
    for actual in (NORMALIZADOS[0], NORMALIZADOS[3]):
        b.run_prompt.reset_mock()
        d = await decidir_titulo({"titulo": "AI Engineer"}, titulo_actual=actual, bridge=b)
        assert d.titulo == actual
        b.run_prompt.assert_not_awaited()


def test_aplicar_titulo_y_export():
    if _invariantes():
        return
    from core.titulo_cv import DecisionTitulo, aplicar_titulo
    from server import _puesto_export
    d = DecisionTitulo(2, "desarrollo", "PROGRAMADOR JAVA", NORMALIZADOS, "Python Developer")
    cv, cambio = aplicar_titulo(CV, d)
    assert cv == CV.replace("PYTHON DEVELOPER", d.titulo, 1)
    assert cambio == {"campo": "puesto objetivo", "valor": d.titulo, "tipo": "contradiccion"}
    assert aplicar_titulo(cv, d) == (cv, None)
    assert _puesto_export({"cv_markdown": cv, "titulo": "Python Developer"}) == d.titulo
    assert cv[cv.index("## PERFIL"):] == CV[CV.index("## PERFIL"):]
    assert aplicar_titulo(cv, DecisionTitulo(3, None, "", (), "")) == (cv, None)
    d3 = DecisionTitulo(3, None, NORMALIZADOS[0], NORMALIZADOS, "AI Engineer")
    assert aplicar_titulo(cv, d3) == (cv, None)
    for anterior in ("DATA ANALYST", "HEAD DE SISTEMAS", "ABOGADO LABORAL"):
        original = CV.replace("PYTHON DEVELOPER", anterior)
        assert aplicar_titulo(original, d)[0] == original.replace(anterior, d.titulo, 1)


@pytest.mark.parametrize("titulo", ["Product Manager LATAM", "No especificado", "Puesto no especificado", "Posición no especificada", ""])
def test_ats_solo_perfil_idempotente(titulo):
    if _invariantes():
        return
    from core.titulo_cv import asegurar_mencion_ats
    cv = asegurar_mencion_ats(CV, titulo)
    assert cv[cv.index("\n\nSegundo"): ] == CV[CV.index("\n\nSegundo"): ]
    assert asegurar_mencion_ats(cv, titulo) == cv
    if titulo == "Product Manager LATAM":
        assert "Experiencia comprobable. Me postulo a la posición de Product Manager LATAM." in cv
        previo = CV.replace("Segundo párrafo intacto.", "Busco PRODUCT MÁNAGER LATAM.")
        assert asegurar_mencion_ats(previo, titulo) == previo
    else:
        assert cv == CV
    for sin in ("# Alex\nAnalista funcional", "## PERFIL\n\n## EXPERIENCIA\n• Trabajo"):
        assert asegurar_mencion_ats(sin, titulo) == sin


@pytest.mark.asyncio
async def test_empates_y_clave_sin_rol():
    if _invariantes():
        return
    from core.titulo_cv import decidir_titulo
    b = _bridge()
    d = await decidir_titulo({"titulo": "Analista Funcional / Soporte"}, bridge=b)
    assert d.clave == "soporte"  # más reciente aunque funcional esté primero en A
    exp = "## Empresa X\n### Analista Funcional | 2020 - 2022"
    d = await decidir_titulo({"titulo": "Analista de Soporte"}, experiencia=exp, anios_por_area=AREAS, bridge=b)
    assert d.caso == 1 and d.titulo == "ANALISTA DE SOPORTE"
    d = await decidir_titulo({"titulo": "Product Manager"}, experiencia=exp, anios_por_area=[], bridge=b)
    assert d.caso == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("puesto,esperado", [("Médica Cirujana", "MÉDICA CIRUJANA"), ("Head de Sistemas", ""), ("", "")])
async def test_sin_roles_y_puesto_aprobado(puesto, esperado):
    if _invariantes():
        return
    from core.titulo_cv import decidir_titulo
    b = _bridge()
    d = await decidir_titulo({"titulo": "AI Engineer"}, experiencia="", personales=f"Puesto: {puesto}", anios_por_area=[], bridge=b)
    assert (d.caso, d.titulo, d.roles_reales) == (3, esperado, ())
    b.run_prompt.assert_not_awaited()
    # Sin historial tampoco se atribuye una profesión por un caché fuerte explícito.
    d = await decidir_titulo({"titulo": "Analista Funcional"}, experiencia="", personales=f"Puesto: {puesto}", anios_por_area=AREAS, bridge=b)
    assert (d.caso, d.titulo) == (3, esperado)
    b.run_prompt.assert_not_awaited()


@pytest.mark.asyncio
async def test_entrada_explicita_vacia(monkeypatch):
    if _invariantes():
        return
    from core.titulo_cv import decidir_titulo
    cache = Mock(side_effect=AssertionError("No consultar fuentes"))
    monkeypatch.setattr(perfil_areas, "anios_por_area_en_cache", cache)
    monkeypatch.setattr(Path, "read_text", Mock(side_effect=AssertionError("No leer perfil")))
    b = _bridge()
    d = await decidir_titulo({"titulo": "Analista Funcional"}, experiencia="", personales="", anios_por_area=[], bridge=b)
    assert d.titulo == "" and d.caso == 3
    cache.assert_not_called()
    b.run_prompt.assert_not_awaited()


@pytest.mark.asyncio
async def test_recencia_por_fechas():
    if _invariantes():
        return
    from core.titulo_cv import decidir_titulo
    bloques = ["## Empresa A\n### Analista de Soporte | 2010 - 2015", "## Empresa B\n### Analista Funcional | 2023 - Actualidad"]
    for exp in ("\n".join(bloques), "\n".join(reversed(bloques))):
        d = await decidir_titulo({"titulo": "Analista Funcional / Soporte"}, experiencia=exp, anios_por_area=AREAS, bridge=_bridge())
        assert d.clave == "funcional" and d.roles_reales[0] == "ANALISTA FUNCIONAL"
    # Misma recencia: orden A, no posición del rol en el texto.
    empate = "\n".join(bloques).replace("2010 - 2015", "2023 - Actualidad")
    d = await decidir_titulo({"titulo": "Analista Funcional / Soporte"}, experiencia=empate, anios_por_area=AREAS, bridge=_bridge())
    assert d.clave == "funcional"
    meses = "## Empresa A\n### Analista de Soporte | 01/2023 - 11/2024\n## Empresa B\n### Analista Funcional | 01/2023 - 02/2024"
    d = await decidir_titulo({"titulo": "Analista Funcional / Soporte"}, experiencia=meses, anios_por_area=AREAS, bridge=_bridge())
    assert d.clave == "soporte" and d.roles_reales[0] == "ANALISTA DE SOPORTE"


@pytest.mark.asyncio
@pytest.mark.parametrize("campo", ["experiencia", "personales"])
async def test_cache_no_contamina_perfil_inyectado(campo, monkeypatch):
    if _invariantes():
        return
    from core.titulo_cv import decidir_titulo
    inyectado = {campo: EXP + "\n• Otro perfil" if campo == "experiencia" else PER + "\nOtra persona"}
    d = await decidir_titulo({"titulo": "Analista Funcional"}, bridge=_bridge(), **inyectado)
    assert d.caso == 2
    d = await decidir_titulo({"titulo": "Analista Funcional"}, bridge=_bridge())
    assert d.caso == 1
    cache = Mock(side_effect=AssertionError("Años explícitos"))
    monkeypatch.setattr(perfil_areas, "anios_por_area_en_cache", cache)
    d = await decidir_titulo({"titulo": "Analista Funcional"}, experiencia=EXP, personales=PER, anios_por_area=[], bridge=_bridge())
    assert d.caso == 2


@pytest.mark.parametrize("seccion", ["PERFIL", "SUMMARY", "PROFILE"])
def test_ats_secciones_canonicas(seccion):
    if _invariantes():
        return
    from core.titulo_cv import asegurar_mencion_ats
    original = CV.replace("## PERFIL", "## " + seccion).replace("\n", "\r\n")
    nuevo = asegurar_mencion_ats(original, "AI Engineer")
    assert nuevo == original.replace("Experiencia comprobable.", "Experiencia comprobable. Me postulo a la posición de AI Engineer.", 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("entidad", ["Empresa Contratante", "Empresa 0", "Villa Ejemplo"])
async def test_empresa_ciudad_conocidas(entidad):
    if _invariantes():
        return
    from core.titulo_cv import decidir_titulo
    d = await decidir_titulo({"titulo": f"Analista Funcional {entidad}", "empresa": "Empresa Contratante"}, bridge=_bridge())
    assert d.titulo == "ANALISTA TÉCNICO FUNCIONAL"
    assert entidad.upper() not in d.titulo


@pytest.mark.asyncio
@pytest.mark.parametrize("oferta", ["Analista de Soporte .NET", "Analista Funcional / Desarrollador .NET"])
async def test_area_fuerte_con_tecnologia_ajena(oferta):
    if _invariantes():
        return
    from core.titulo_cv import decidir_titulo
    d = await decidir_titulo({"titulo": oferta}, bridge=_bridge())
    assert d.caso == 3 and d.titulo in NORMALIZADOS and ".NET" not in d.titulo


@pytest.mark.parametrize("contacto", ["alex@example.org", "+54 11 12345678", "Buenos Aires, Argentina", "Villa Ejemplo | alex@example.org", ""])
def test_header_sin_titular_preserva_contacto(contacto):
    if _invariantes():
        return
    from core.titulo_cv import DecisionTitulo, aplicar_titulo
    d = DecisionTitulo(2, "funcional", NORMALIZADOS[3], NORMALIZADOS, "")
    original = f"# Alex Prueba\n\n{contacto}\n\n## PERFIL\nTexto.\n" + COLA
    nuevo, cambio = aplicar_titulo(original, d)
    assert nuevo == original.replace("# Alex Prueba\n", f"# Alex Prueba\n{d.titulo}\n", 1)
    assert cambio and aplicar_titulo(nuevo, d) == (nuevo, None)
    assert aplicar_titulo(original.replace("# Alex Prueba", "Alex Prueba"), d) == (original.replace("# Alex Prueba", "Alex Prueba"), None)
    negrita = CV.replace("PYTHON DEVELOPER", "**ANALISTA TÉCNICO FUNCIONAL**")
    assert aplicar_titulo(negrita, d)[0] == negrita.replace("**ANALISTA TÉCNICO FUNCIONAL**", d.titulo)


@pytest.mark.asyncio
async def test_varias_areas_para_una_clave():
    if _invariantes():
        return
    from core.titulo_cv import decidir_titulo
    areas = [{"area": "Soporte técnico", "anios": "3"}, {"area": "Incidentes", "anios": "4"}]
    for lista in (areas, list(reversed(areas))):
        b = _bridge()
        d = await decidir_titulo({"titulo": "Analista de Soporte"}, anios_por_area=lista, bridge=b)
        assert d.caso == 2 and d.titulo == NORMALIZADOS[2]
        b.run_prompt.assert_not_awaited()
