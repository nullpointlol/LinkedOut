"""El mail se arma con los requisitos cubiertos del análisis y un control sin IA exige nombrar al menos 2."""
import pytest

from core import email_composer as ec

POST = {"texto": "Buscamos AI Solutions Engineer. Integrar LLMs en productos, APIs y PostgreSQL.",
        "triage": {"puesto": "AI Solutions Engineer", "email_destino": "rrhh@acme.test"}}
PERFIL = {"nombre": "Ana Prueba", "rol": "Analista", "anios_experiencia": 7}
FUNCIONAL = "Relevé requerimientos y definí criterios de aceptación en Jira para 4 equipos."
APIS = "Integré APIs REST con Python y PostgreSQL para 3 productos."
INCIDENTES = "Resolví 15 incidentes críticos por mes en producción."
REQUISITOS = [
    {"nombre": "APIs", "tipo": "REQUISITO_EXCLUYENTE", "estado": "MATCH_DIRECTO", "approved": True,
     "justificacion": "Integró APIs REST en varios productos."},
    {"nombre": "PostgreSQL", "tipo": "REQUISITO_EXCLUYENTE", "estado": "MATCH_DIRECTO", "approved": True,
     "justificacion": "PostgreSQL en sus habilidades."},
    {"nombre": "Integración de LLMs en productos", "tipo": "RESPONSABILIDAD", "estado": "TRANSFERIBLE",
     "approved": True, "equivalencia": "Integraciones con APIs", "justificacion": "Integró servicios externos."},
    {"nombre": "Construcción de agentes y RAG", "tipo": "REQUISITO_EXCLUYENTE", "estado": "BRECHA_REAL",
     "approved": False, "justificacion": "Sin antecedentes."},
    {"nombre": "Jornada full time", "tipo": "REQUISITO_EXCLUYENTE", "estado": "TRANSFERIBLE", "approved": True,
     "logistico": True},
]
POSTULACION = {"pitch": "", "dolores_empresa": ["Llevar soluciones con LLMs a producción"],
               "requisitos": REQUISITOS,
               "cv_markdown": f"## EXPERIENCIA\n- {FUNCIONAL}\n- {APIS}\n- {INCIDENTES}\n"}
SALUDO = "Hola,\n\nVi la publicación para AI Solutions Engineer y me interesa.\n\n"
CIERRE = "\n\nAdjunto mi CV.\n\nQuedo a disposición,\nSaludos,\n\nAna Prueba"
RESPONDE = (SALUDO + "Soy Analista con +7 años de experiencia en integración de APIs REST, consultas en PostgreSQL "
            "y soporte de producción." + CIERRE)
NO_RESPONDE = (SALUDO + "Soy Analista con +7 años de experiencia en relevamiento de requerimientos, criterios de "
               "aceptación y gestión de pases en Jira." + CIERRE)


@pytest.fixture(autouse=True)
def _sin_voz(monkeypatch):
    monkeypatch.setattr(ec, "_cargar_voz", lambda: {})


class Bridge:
    def __init__(self, *cuerpos):
        self.cuerpos = iter(cuerpos)
        self.prompts = []

    async def run_prompt(self, prompt, **kwargs):
        self.prompts.append(prompt)
        return {"subject": "AI Solutions Engineer - Ana Prueba", "body": next(self.cuerpos)}


def test_el_prompt_lleva_lo_que_pide_la_oferta_y_cubre_el_candidato():
    prompt = ec.build_composer_prompt(POST, POSTULACION, PERFIL, {})
    bloque = prompt.split("LO QUE PIDE LA OFERTA", 1)[1].split("EVIDENCIA DEL CANDIDATO", 1)[0]
    assert "APIs (coincide)" in bloque and "PostgreSQL (coincide)" in bloque
    assert "Integración de LLMs en productos (transferible: Integraciones con APIs)" in bloque
    assert "Integró servicios externos." in bloque
    assert "agentes y RAG" not in bloque and "full time" not in bloque  # ni brechas ni logística
    assert "al menos 2" in prompt


def test_la_evidencia_elegida_es_la_que_cubre_los_requisitos():
    evidencia = ec.seleccionar_evidencia(POST, POSTULACION)
    assert evidencia[0] == APIS and FUNCIONAL not in evidencia[:2]


@pytest.mark.parametrize("cuerpo, nombrados", [
    (RESPONDE, ["APIs", "PostgreSQL"]),
    (NO_RESPONDE, []),
    # El puesto en el saludo no cuenta como requisito nombrado
    ("Hola, vi la publicación para Analista PostgreSQL y me interesa.", []),
    ("Trabajé con Python y TypeScript.", ["TypeScript/JavaScript o Python"]),
])
def test_requisitos_nombrados(cuerpo, nombrados):
    reqs = REQUISITOS + [{"nombre": "TypeScript/JavaScript o Python", "estado": "MATCH_DIRECTO", "approved": True}]
    assert ec.requisitos_nombrados(cuerpo, ec.requisitos_de_la_oferta({}, {"requisitos": reqs}),
                                   excluir=["Analista PostgreSQL"]) == nombrados


@pytest.mark.asyncio
async def test_un_mail_que_no_nombra_lo_que_pide_la_oferta_entra_a_la_correccion():
    bridge = Bridge(NO_RESPONDE, RESPONDE)
    mail = await ec.compose_email(POST, POSTULACION, PERFIL, bridge)
    assert mail["body"].startswith(RESPONDE.split(CIERRE)[0]) and len(bridge.prompts) == 2
    correccion = bridge.prompts[1].split("TU RESPUESTA ANTERIOR NO CUMPLE", 1)[1]
    assert "requisitos de la oferta" in correccion and "APIs" in correccion


@pytest.mark.asyncio
async def test_si_la_correccion_tampoco_los_nombra_el_mail_no_se_aprueba():
    with pytest.raises(ec.MensajeRequiereRevision) as exc:
        await ec.compose_email(POST, POSTULACION, PERFIL, Bridge(NO_RESPONDE, NO_RESPONDE))
    assert any("requisitos de la oferta" in m for m in exc.value.motivos)


@pytest.mark.asyncio
@pytest.mark.parametrize("requisitos, exigidos", [([], 0), (REQUISITOS[:1], 1)])
async def test_lo_exigido_no_supera_lo_que_cubre_el_candidato(requisitos, exigidos):
    postulacion = {**POSTULACION, "requisitos": requisitos}
    cuerpo = (SALUDO + "Soy Analista con +7 años de experiencia en integración de APIs REST, soporte de producción "
              "y guardias." + CIERRE)
    mail = await ec.compose_email(POST, postulacion, PERFIL, Bridge(cuerpo))
    assert len(ec.requisitos_nombrados(mail["body"], ec.requisitos_de_la_oferta({}, postulacion))) >= exigidos


def test_los_requisitos_salen_del_analisis_aunque_vengan_como_json():
    import json
    desde_post = ec.requisitos_de_la_oferta({"analisis": {"requisitos": REQUISITOS}}, {})
    desde_json = ec.requisitos_de_la_oferta({}, {"requisitos": json.dumps(REQUISITOS)})
    assert [r["nombre"] for r in desde_post] == [r["nombre"] for r in desde_json] == [
        "APIs", "PostgreSQL", "Integración de LLMs en productos"]
