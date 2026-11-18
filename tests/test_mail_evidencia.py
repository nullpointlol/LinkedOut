"""Evidencia del mail: datos sintéticos, sin proveedores ni archivos reales."""
import pytest

from core import email_composer as ec


POST = {"texto": "Soporte de aplicaciones críticas en Oil & Gas.",
        "triage": {"puesto": "Soporte", "email_destino": "test@example.com"}}
PERFIL = {"nombre": "Ana Prueba", "rol": "Analista", "anios_experiencia": 7}
INCIDENTES = "Resolví incidentes críticos mediante ServiceNow con guardias de producción."
CORE = "Di soporte al aplicativo core con SQL y documenté 24 procedimientos."
POSTULACION = {"pitch": "Analista de soporte.",
               "dolores_empresa": ["Incidentes críticos y soporte del aplicativo core"],
               "cv_markdown": "## EXPERIENCIA\n- Organicé reuniones internas.\n"
                              f"- {INCIDENTES}\n• {CORE}\n"
                              "## EDUCACIÓN\n- Soporte del aplicativo core con SAP."}
CONCRETO = ("Hola, vi la búsqueda de soporte y me interesa. Soy Analista, con 7 años "
            "de experiencia en soporte. Para sostener la operación ante fallas, "
            "resolví incidentes críticos con ServiceNow. Adjunto mi CV.")


class Bridge:
    def __init__(self, *cuerpos):
        self.cuerpos = iter(cuerpos)
        self.calls = []

    async def run_prompt(self, prompt, **kwargs):
        self.calls.append(prompt)
        return {"subject": "Postulación soporte", "body": next(self.cuerpos)}


def test_prompt_selecciona_experiencia_relevante_y_no_educacion():
    prompt = ec.build_composer_prompt(POST, POSTULACION, PERFIL, {})
    assert "EVIDENCIA" in prompt
    bloque = prompt.split("EVIDENCIA", 1)[1].split("PITCH BASE", 1)[0]
    assert INCIDENTES in bloque and CORE in bloque
    assert "reuniones" not in bloque and "SAP" not in bloque
    assert "UN dolor concreto" in prompt and "no copiar" in prompt


@pytest.mark.parametrize("cv", ["", "## EXPERIENCIA\nSin viñetas."])
def test_sin_vinetas_usa_pitch(cv):
    p = {**POSTULACION, "cv_markdown": cv, "pitch": "Resolví tickets con Zendesk."}
    prompt = ec.build_composer_prompt(POST, p, PERFIL, {})
    assert "EVIDENCIA" in prompt
    assert "Resolví tickets con Zendesk." in prompt.split("PITCH BASE", 1)[0]


@pytest.mark.parametrize("frase", ["traduciendo necesidades de negocio", "aportar valor",
    "conectar áreas", "sinergias", "soluciones a medida", "equipos multidisciplinarios"])
def test_rechaza_frases_genericas(frase):
    assert ec.problemas_de_tono(f"Soy analista, {frase}.")


@pytest.mark.parametrize("evidencia", [[], ["Analista de soporte."]])
def test_sin_anclas_disponibles_no_exige_un_hecho_inexistente(evidencia):
    assert ec.problemas_de_tono("Hola, me interesa. Adjunto mi CV.",
                               evidencia=evidencia) == []
    assert ec.problemas_de_tono("Hola, puedo aportar valor.", evidencia=evidencia)


@pytest.mark.asyncio
@pytest.mark.parametrize("primero", [CONCRETO, "Hola, puedo aportar valor al equipo.",
                                       "Hola, me interesa el puesto. Adjunto mi CV."])
async def test_presupuesto_y_reescritura_con_evidencia(monkeypatch, primero):
    monkeypatch.setattr(ec, "_cargar_voz", lambda: {})
    bridge = Bridge(primero, CONCRETO)
    r = await ec.compose_email(POST, POSTULACION, PERFIL, bridge)
    assert "ServiceNow" in r["body"] and not r["is_fallback"]
    assert len(bridge.calls) == (1 if primero == CONCRETO else 2)
    if len(bridge.calls) == 2:
        assert "evidencia" in bridge.calls[1].lower()


@pytest.mark.asyncio
async def test_segunda_sin_evidencia_bloquea_aunque_mencione_soporte(monkeypatch):
    monkeypatch.setattr(ec, "_cargar_voz", lambda: {})
    bridge = Bridge("Hola, me interesa el soporte.", "Hola, puedo trabajar en soporte.")
    with pytest.raises(ec.MensajeRequiereRevision, match="evidencia"):
        await ec.compose_email(POST, POSTULACION, PERFIL, bridge)
    assert len(bridge.calls) == 2


@pytest.mark.asyncio
async def test_pitch_tambien_exige_hecho_concreto(monkeypatch):
    monkeypatch.setattr(ec, "_cargar_voz", lambda: {})
    p = {"pitch": "Resolví tickets con Zendesk."}
    bridge = Bridge("Hola, me interesa.", "Hola, resolví tickets con Zendesk.")
    r = await ec.compose_email(POST, p, PERFIL, bridge)
    assert "Zendesk" in r["body"] and len(bridge.calls) == 2


def test_solapamiento_sin_acentos_y_desempate_por_concrecion():
    p = {"dolores_empresa": ["Resolucion de incidentes criticos"], "cv_markdown":
         "## Experiencia\n- Resolución de incidentes críticos.\n"
         "- Resolución de incidentes críticos con SQL.\n"
         "- Resolución de incidentes críticos con Jira.\n"
         "- Resolución de incidentes críticos con SAP."}
    elegidas = ec.seleccionar_evidencia(POST, p)
    assert len(elegidas) == 3
    assert all(t in v for t, v in zip(["SQL", "Jira", "SAP"], elegidas))
    assert elegidas == ec.seleccionar_evidencia(POST, p)


def test_anclas_no_se_cubren_desde_firma_ni_fijas_ni_subcadenas():
    for cuerpo, firma, fijas in [("Hola, uso MySQL.", "", []),
                                 ("Hola\nSQL", "SQL", []),
                                 ("Hola\nDisponible en SQL", "", ["Disponible en SQL"])]:
        assert any("evidencia" in p for p in ec.problemas_de_tono(
            cuerpo, firma=firma, fijas=fijas, evidencia=["Probé consultas con SQL."]))


@pytest.mark.asyncio
async def test_fallback_tecnico_no_elude_la_evidencia(monkeypatch):
    monkeypatch.setattr(ec, "_cargar_voz", lambda: {})

    class SinServicio:
        async def run_prompt(self, *args, **kwargs):
            raise TimeoutError("simulado")

    # Creador, 09/11: el respaldo pasa el mismo control porque suma la viñeta elegida (antes quedaba en
    # ERROR para revisar a mano).
    mail = await ec.compose_email(POST, POSTULACION, PERFIL, SinServicio())
    assert mail["is_fallback"] is True
    assert "Por ejemplo: " in mail["body"]
    assert any(v.rstrip(".") in mail["body"] for v in (INCIDENTES, CORE))


def test_sin_solapamiento_conserva_cv_y_ordena_por_concrecion():
    p = {"dolores_empresa": ["Facturación pendiente"],
         "pitch": "Resolví facturación con SAP.",
         "cv_markdown": "## Experiencia\n- Organicé reuniones.\n"
                        "- Probé consultas con SQL.\n- Resolví tickets con Jira.\n"
                        "- Migré 24 procedimientos con ServiceNow."}
    elegidas = ec.seleccionar_evidencia(POST, p)
    assert len(elegidas) == 3
    assert "ServiceNow" in elegidas[0]
    assert all(v in p["cv_markdown"] for v in elegidas)
    assert not any("SAP" in v for v in elegidas)


@pytest.mark.asyncio
async def test_sin_solapamiento_exige_evidencia_igual(monkeypatch):
    monkeypatch.setattr(ec, "_cargar_voz", lambda: {})
    p = {"dolores_empresa": ["Facturación pendiente"],
         "cv_markdown": "## Experiencia\n- Probé consultas con SQL."}
    bridge = Bridge("Hola, me interesa.", "Hola, me interesa.")
    with pytest.raises(ec.MensajeRequiereRevision, match="evidencia"):
        await ec.compose_email(POST, p, PERFIL, bridge)
    assert len(bridge.calls) == 2


@pytest.mark.asyncio
async def test_error_procesando_reescritura_conserva_motivos(monkeypatch):
    monkeypatch.setattr(ec, "_cargar_voz", lambda: {})
    def fallar(*args, **kwargs):
        raise ValueError("procesamiento simulado")
    monkeypatch.setattr(ec, "_completar_y_normalizar", fallar)
    bridge = Bridge("Estimado equipo, puedo aportar valor.", CONCRETO)
    with pytest.raises(ec.MensajeRequiereRevision) as exc:
        await ec.compose_email(POST, POSTULACION, PERFIL, bridge)
    assert any("estimado" in m for m in exc.value.motivos)
    assert any("evidencia" in m for m in exc.value.motivos)
    assert len(bridge.calls) == 2
