"""Todo lo que es MATCH o TRANSFERIBLE aparece en el CV: en HABILIDADES TÉCNICAS o integrado en la
viñeta donde encaja, aunque el maestro no lo nombre. Réplica del 11257 con una candidata inventada."""
import pytest

from core import cv_maestro as m
from tests.test_cv_relevancia import ESTRUCTURA, MAESTRO, PERSONALES

MONITOREO = {"puesto": "Analista de Monitoreo", "requisitos": [
    {"nombre": "Monitoreo de alertas", "tipo": "RESPONSABILIDAD", "estado": "MATCH_DIRECTO", "approved": True},
    {"nombre": "Grafana, Dynatrace, Elastic", "tipo": "REQUISITO_EXCLUYENTE", "estado": "MATCH_DIRECTO",
     "approved": True, "justificacion": "Herramientas de monitoreo confirmadas por la candidata."},
    {"nombre": "SQL Server Management Studio", "tipo": "REQUISITO_EXCLUYENTE", "estado": "TRANSFERIBLE",
     "approved": True, "equivalencia": "PostgreSQL"},
    {"nombre": "Bases de datos no relacionales", "tipo": "REQUISITO_DESEABLE", "estado": "MATCH_DIRECTO",
     "approved": True},
    {"nombre": "Postman", "tipo": "REQUISITO_EXCLUYENTE", "estado": "MATCH_DIRECTO", "approved": True},
    {"nombre": "Oratoria", "tipo": "REQUISITO_EXCLUYENTE", "estado": "MATCH_DIRECTO", "approved": True},
    {"nombre": "Kibana", "tipo": "REQUISITO_DESEABLE", "estado": "BRECHA_REAL", "approved": False},
]}
EXIGIDOS = ["Grafana", "Dynatrace", "Elastic", "SQL Server Management Studio"]


def _borrador():
    return m.seleccionar(MAESTRO, PERSONALES, MONITOREO, titulo="X", estructura=ESTRUCTURA, renglones=200)


def _habilidades(b):
    return "\n".join(l.texto for l in b.habilidades)


def test_lo_que_falta_va_a_habilidades_tecnicas_y_no_a_una_vineta_ni_solo_al_perfil():
    b = _borrador()
    b.perfil.texto = "Analista con Grafana, Dynatrace y Elastic."  # en el perfil no alcanza
    agregados = m.asegurar_cobertura(b, MONITOREO)
    assert agregados == EXIGIDOS
    for t in EXIGIDOS:
        assert t in _habilidades(b)
    assert "Kibana" not in b.markdown() and "Oratoria" not in _habilidades(b)
    assert m.asegurar_cobertura(b, MONITOREO) == []  # ya está todo: no repite


def test_lo_que_ya_esta_en_una_vineta_no_se_repite_en_habilidades():
    b = _borrador()
    vineta = b.experiencia[0].vinetas[0]
    vineta.texto += " Usé Grafana y Dynatrace."
    m.asegurar_cobertura(b, MONITOREO)
    assert "Grafana" not in _habilidades(b) and "Elastic" in _habilidades(b)


def test_va_al_grupo_de_habilidades_donde_encaja():
    b = _borrador()
    m.asegurar_cobertura(b, MONITOREO, maestro=MAESTRO)
    bases = next(l.texto for l in b.habilidades if l.texto.startswith("**Bases de datos:**"))
    assert "SQL Server Management Studio" in bases


def test_el_control_de_cobertura_marca_lo_que_falta():
    b = _borrador()
    hallazgos = m.control_cobertura(b, MONITOREO)
    assert {h.motivo.split("«")[1].split("»")[0] for h in hallazgos} == set(EXIGIDOS)
    m.asegurar_cobertura(b, MONITOREO)
    assert m.control_cobertura(b, MONITOREO) == []


def test_un_termino_de_la_oferta_integrado_en_otra_vineta_no_es_hallazgo():
    """El 16/11 el usuario permitió integrarlo con naturalidad en la viñeta donde encaja."""
    assert not hasattr(m, "control_evidencia")


class _Flujo(m.FlujoCV):
    def __init__(self, **kw):
        super().__init__(maestro=MAESTRO, personales=PERSONALES, analisis=MONITOREO, oferta="Oferta de prueba",
                         titulo="X", estructura=ESTRUCTURA, **kw)

    async def paginas(self, md):
        return 1


@pytest.mark.asyncio
async def test_el_cv_aprobado_tiene_cada_herramienta_aunque_la_ia_no_la_ponga(monkeypatch):
    from tests.cv_final_prueba import es_seleccion, puntajes_falsos
    pedidos = []

    async def ia(prompt, **kw):
        pedidos.append(prompt)
        if es_seleccion(prompt):
            return puntajes_falsos(prompt)  # sin evidencia para ningún requisito
        return {"perfil": "Analista de monitoreo con Elastic.", "lineas": [], "keywords_cv": {}}

    flujo = _Flujo(ia=ia)

    async def auditar(borrador, keywords, maestro):
        auditar.cv = borrador.markdown()
        return {"approved": True}, m.control_cobertura(borrador, MONITOREO)
    monkeypatch.setattr(flujo, "auditar", auditar)
    resultado = await flujo.generar()
    habilidades = resultado.markdown.split("## HABILIDADES TÉCNICAS")[1]
    for t in EXIGIDOS:
        assert t in habilidades
    seleccion = next(p for p in pedidos if es_seleccion(p))
    assert "no lo pongas" not in seleccion
    ajuste = next(p for p in pedidos if p.startswith("Ajustá este CV"))
    assert "no agregues términos de la oferta en líneas donde no están" not in ajuste


@pytest.mark.asyncio
async def test_si_la_correccion_saca_una_herramienta_vuelve_a_habilidades(monkeypatch):
    import re
    from tests.cv_final_prueba import es_seleccion, puntajes_falsos

    async def ia(prompt, **kw):
        if es_seleccion(prompt):
            return puntajes_falsos(prompt)
        if prompt.startswith("Una auditoría independiente rechazó"):  # la corrección se lleva Grafana
            sid, texto = next((i, t) for i, t in re.findall(r"^(S\d+): (.*)$", prompt, re.M) if "Grafana" in t)
            return {"perfil": "", "lineas": [{"id": sid, "texto": texto.replace(", Grafana", "")}]}
        return {"perfil": "Analista de monitoreo.", "lineas": [], "keywords_cv": {}}

    flujo = _Flujo(ia=ia)
    auditorias = iter([[m.Hallazgo("P1", "estilo")], []])

    async def auditar(borrador, keywords, maestro):
        return {"approved": True}, next(auditorias)
    monkeypatch.setattr(flujo, "auditar", auditar)
    monkeypatch.setattr(m, "_ticket", lambda **kw: 1)
    resultado = await flujo.generar()
    assert resultado.correcciones == 1
    assert "Grafana" in resultado.markdown.split("## HABILIDADES TÉCNICAS")[1]


def test_el_analisis_pide_una_etiqueta_por_herramienta_y_no_confunde_evaluar_con_usar():
    from core.prompts_evaluacion import STRATEGIST_PROMPT
    assert "UNA ETIQUETA POR HERRAMIENTA NOMBRADA" in STRATEGIST_PROMPT
    assert "Evaluar o comparar una herramienta" in STRATEGIST_PROMPT


@pytest.mark.asyncio
async def test_al_recortar_no_se_saca_una_linea_de_habilidades_que_lleva_lo_exigido():
    """1197: sacar esa línea y volver a ponerla con `asegurar_cobertura` gastaba las compilaciones y el CV no
    entraba en 1 página."""
    b = _borrador()
    b.habilidades = [m.Linea("S1", "**Soporte:** Linux, Grafana.", ""),
                     m.Linea("S2", "**Bases de datos:** SQL, Dynatrace, Elastic, SQL Server Management Studio.", ""),
                     m.Linea("S3", "**Integraciones:** REST, Postman.", "")]
    for bl in b.experiencia:  # sin viñetas sobre el mínimo: lo próximo serían las habilidades
        while len(bl.vinetas) > bl.minimo:
            bl.reserva.insert(0, bl.vinetas.pop())
    vinetas = sum(len(bl.vinetas) for bl in b.experiencia)
    flujo = _Flujo()

    largo = len(b.markdown())

    async def paginas(md):  # entra en 1 página si sale una viñeta o algo de habilidades
        flujo.renglones_sobrantes = 1
        return 1 if sum(len(bl.vinetas) for bl in b.experiencia) < vinetas or len(md) < largo else 2
    flujo.paginas = paginas
    await flujo._a_una_pagina(b, [])
    assert m.control_cobertura(b, MONITOREO) == []
    assert len(b.habilidades) == 3
    assert all(t in b.markdown() for t in ("Grafana", "Postman"))


def test_las_lineas_de_habilidades_no_pasan_el_largo_maximo_y_sale_primero_lo_afin():
    """1197: «S1: habilidades de 179 caracteres (máximo 170)»."""
    from core import pautas_cv
    b = _borrador()
    largo = "**Soporte:** Linux, Windows, mesa de ayuda, Jira, ServiceNow, Active Directory, Zabbix, Nagios, Tivoli."
    b.habilidades = [m.Linea("S1", largo, largo), m.Linea("S2", "**Integraciones:** REST, SOAP, SoapUI, Postman.", ""),
                     m.Linea("S3", "**DevOps:** Jenkins, GitLab, SonarQube, Docker, Kubernetes, Terraform.", "")]
    muchos = dict(MONITOREO, requisitos=MONITOREO["requisitos"] + [
        {"nombre": n, "estado": "MATCH_DIRECTO", "approved": True, "tipo": "REQUISITO_EXCLUYENTE",
         "justificacion": "soporte linux windows jira"}
        for n in ("Prometheus", "Splunk", "New Relic", "Datadog", "PagerDuty", "Opsgenie", "Kibana Lens")])
    m.asegurar_cobertura(b, muchos, maestro=MAESTRO)
    assert m.control_cobertura(b, muchos) == []
    assert all(len(l.texto) <= pautas_cv.LARGO_MAX_HABILIDAD for l in b.habilidades), [len(l.texto) for l in b.habilidades]
    assert "Postman" in b.markdown()  # exigido: no sale


def test_el_relleno_sin_de_tambien_se_saca_del_termino():
    from core.cobertura_requisitos import grupos
    assert grupos("Formatos JSON, HTML, XML") == [["JSON"], ["HTML"], ["XML"]]
    assert grupos("Programación Python o .NET") == [["Python", ".NET"]]


def test_valores_y_actitudes_no_son_keywords_ni_van_a_la_seleccion():
    """10937 (YPF): «Excelencia y profesionalismo» y «Visión integral de procesos» quedaron como keywords
    obligatorias y el CV no aprobó. Valores y actitudes no se exigen; herramientas y conocimientos sí."""
    valores = ["Excelencia y profesionalismo", "Foco en usuario y valor", "Visión integral de procesos",
               "Impulso del cambio continuo", "Mentalidad de crecimiento", "Pasión por la tecnología"]
    tecnicos = ["ServiceNow y plataformas ITSM", "Metodologías de mejora continua", "Aprendizaje ágil y GenAI",
                "Automatización de procesos", "Innovación e Inteligencia Artificial"]
    assert all(m.es_requisito_blando(v) for v in valores)
    assert not any(m.es_requisito_blando(t) for t in tecnicos)
    analisis = {"requisitos": [{"nombre": n, "estado": "MATCH_DIRECTO", "approved": True} for n in valores + tecnicos]}
    obligatorias, habilitados = m.terminos_del_matching(analisis)
    assert set(obligatorias) == set(tecnicos) == set(habilitados)
    assert set(m.requisitos_ids(analisis).values()) == set(tecnicos)


def test_una_tilde_no_parte_la_palabra_de_una_keyword():
    """«técnica» se partía en «t» + «cnica» (10937)."""
    from core.auditor import evaluate_ats_compliance, palabras_de_keyword
    assert "técnica" in palabras_de_keyword("Estandarización y documentación técnica")[0]
    assert "cnica" not in palabras_de_keyword("Estandarización y documentación técnica")[0]
    cv = "## HABILIDADES TÉCNICAS\n• documentación técnica de procesos.\n"
    assert evaluate_ats_compliance(cv, ["Estandarización y documentación técnica"], [])["missing_keywords"] == []
