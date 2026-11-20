"""Todo lo que es MATCH o TRANSFERIBLE aparece en el CV (oferta 11257: el CV no nombraba Grafana ni
Dynatrace y quedó LISTO con 99 %). Candidata inventada."""
from core import cobertura_requisitos as c
from core.idioma import EN


def _req(nombre, estado="MATCH_DIRECTO", tipo="REQUISITO_EXCLUYENTE", **kw):
    return {"nombre": nombre, "estado": estado, "tipo": tipo, "approved": estado != "BRECHA_REAL", **kw}


CV = """# LUCÍA FERRARO
ANALISTA DE MONITOREO

## PERFIL
Analista con experiencia en monitoreo y herramientas como Elastic.

## EXPERIENCIA
### Operadora | Initech (2019 - 2024)
• Monitoreé alertas de 300 servidores con Nagios y Python.

## HABILIDADES TÉCNICAS
• **Integraciones:** REST, SOAP, Postman, APIs.

## EDUCACIÓN
• Tecnicatura - Instituto Test - 2013

## IDIOMAS
• Inglés: C1
"""


def test_un_requisito_compuesto_exige_cada_herramienta():
    faltan = c.faltantes(CV, [_req("Grafana, Dynatrace, Elastic")])
    assert [f.termino for f in faltan] == ["Grafana", "Dynatrace"]


def test_alternativas_alcanza_una():
    assert c.faltantes(CV, [_req("Programación Python o .NET", tipo="REQUISITO_DESEABLE")]) == []
    assert c.faltantes(CV, [_req("Kafka / Airflow")])[0].termino == "Kafka"


def test_dos_herramientas_unidas_por_y_se_parten_pero_una_frase_no():
    assert [f.termino for f in c.faltantes(CV, [_req("Postman y Swagger")])] == ["Swagger"]
    assert c.faltantes(CV, [_req("Manejo y configuración de APIs")]) == []  # «APIs» está


def test_transferible_tambien_se_exige_con_el_termino_de_la_oferta():
    faltan = c.faltantes(CV, [_req("SQL Server Management Studio", "TRANSFERIBLE", equivalencia="TOAD")])
    assert [f.termino for f in faltan] == ["SQL Server Management Studio"]


def test_plural_tildes_y_relleno_no_cuentan():
    assert c.faltantes(CV, [_req("Manejo de API REST")]) == []
    assert c.faltantes(CV, [_req("Conocimientos de Postman")]) == []
    assert c.faltantes(CV, [_req("Monitoréo de alertas")]) == []


def test_ejemplos_entre_parentesis_no_se_exigen_y_los_anios_tampoco():
    assert c.faltantes(CV, [_req("Oracle (ej. PostgreSQL)")])[0].termino == "Oracle"
    assert c.faltantes(CV, [_req("Python (+3 años)")]) == []


def test_no_se_exige_brecha_responsabilidad_blanda_logistica_idioma_ni_formacion():
    reqs = [_req("Grafana", "BRECHA_REAL"), _req("Garantizar continuidad de servicios", tipo="RESPONSABILIDAD"),
            _req("Oratoria"), _req("Resolución de conflictos"), _req("Alta capacidad de análisis"),
            _req("Turnos rotativos 4x2", logistico=True), _req("Inglés avanzado"),
            _req("Título universitario en Sistemas"), _req("Certificación ITIL v4"),
            _req("Kibana", "TRANSFERIBLE", approved=False)]
    assert c.faltantes(CV, reqs) == []


def test_en_un_cv_en_ingles_se_exigen_los_nombres_propios_y_vale_el_termino_que_eligio_la_ia():
    reqs = [_req("Grafana, Dynatrace, Elastic"), _req("Bases de datos no relacionales")]
    assert [f.termino for f in c.faltantes(CV, reqs, idioma=EN)] == ["Grafana", "Dynatrace"]
    assert c.faltantes(CV, [_req("Monitoreo de dashboards")], idioma=EN,
                       equivalentes={"Monitoreo de dashboards": "alertas"}) == []


def test_exigidos_cuenta_cada_termino():
    assert len(c.exigidos([_req("Grafana, Dynatrace, Elastic"), _req("Postman"), _req("Oratoria")])) == 4


def test_nombrarla_solo_en_el_perfil_no_alcanza():
    assert "Elastic" not in c.sin_perfil(CV) and "Nagios" in c.sin_perfil(CV)
    assert [f.termino for f in c.faltantes(c.sin_perfil(CV), [_req("Elastic")])] == ["Elastic"]


def test_valores_y_frases_genericas_no_se_fuerzan_solo_los_nombres_propios():
    """Dev, 16/11 (10937, 10858): forzar «Excelencia y profesionalismo» o «Ecosistema de IA generativa» en
    Habilidades hizo fallar el CV por relleno. Se exigen herramientas, tecnologías y siglas."""
    reqs = [_req(n) for n in ("Excelencia y profesionalismo", "Foco en usuario y valor", "Aprendizaje ágil",
                              "Ecosistema de IA generativa", "Gestión y mejora de procesos",
                              "Bases de datos no relacionales", "Impulso del cambio continuo")]
    assert c.faltantes(CV, reqs) == []
    assert [f.termino for f in c.faltantes(CV, [_req("ServiceNow y plataformas ITSM")])] == ["ServiceNow", "ITSM"]
    assert [f.termino for f in c.faltantes(CV, [_req("Aprendizaje ágil y GenAI")])] == ["GenAI"]
    assert [f.termino for f in c.faltantes(CV, [_req("Enfoque Data Driven y AI First")])] == ["Data Driven", "AI First"]
    assert [f.termino for f in c.faltantes(CV, [_req("Inteligencia Artificial en procesos")])] == [
        "Inteligencia Artificial"]
