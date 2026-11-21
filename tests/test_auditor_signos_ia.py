"""Auditoría de "Signs of AI writing": patrones de estructura y lente LLM con citas literales."""
import pytest

from core.auditor import AuditorAgent, scan_anti_ai_writing

CV_IA = """# ANA PRUEBA
ANALISTA

## PERFIL
Cuento con más de 10 años de experiencia en soporte de aplicaciones de alta exigencia.

## EXPERIENCIA

### Acme
Analista | 2018 - 2025
• Validé reglas de negocio con scripts en **Python**, evitando fallas en producción.
• Automaticé reportes de proyectos, reduciendo tiempos de auditoría.
• Corregí datos con **SQL**, reduciendo la dependencia operativa.
• Diagnostiqué incidentes en integraciones con **APIs REST** y **SOAP** usando **PagerDuty**.

## HABILIDADES TÉCNICAS
• **Lenguajes:** Python, SQL.
"""

CV_NATURAL = """# ANA PRUEBA
ANALISTA

## PERFIL
Analista de soporte con diez años en aplicaciones transaccionales del sector seguros.

## EXPERIENCIA

### Acme
Analista | 2018 - 2025
• Escribí scripts en **Python** que consultan APIs REST y bajé la auditoría de 90 aplicaciones de dos días a una hora.
• Corregí inconsistencias de datos con SQL y armé una guía que hoy usa todo el equipo de soporte.
• Diagnostiqué incidentes en integraciones ESB con PagerDuty y Insomnia.
• Clasifiqué alertas 24x7 para cuentas de EE. UU. y Europa.

## HABILIDADES TÉCNICAS
• **Lenguajes:** Python, SQL.
"""


def test_structural_ai_patterns_are_detected():
    detectadas = " | ".join(scan_anti_ai_writing(CV_IA)["detected_words"])
    assert "simetría sintáctica" in detectadas
    assert "exceso de negritas" in detectadas
    assert "apertura formulaica" in detectadas
    assert "impacto vago" in detectadas
    assert "de alta exigencia" in detectadas


def test_natural_cv_is_clean():
    r = scan_anti_ai_writing(CV_NATURAL)
    assert r["clean"] is True, r["detected_words"]


class _BridgeFalso:
    effort = "low"
    last_telemetry = None

    def __init__(self, respuestas):
        self.respuestas = list(respuestas)
        self.prompts = []

    async def run_prompt(self, prompt, json_mode=True, effort=None, timeout_seconds=None, cwd=None):
        self.prompts.append(prompt)
        r = self.respuestas.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


# Al importar (antes de los fixtures) todavía es la real: el conftest la reemplaza en cada test
_REVISAR_REAL = AuditorAgent.revisar_senales_ia


@pytest.fixture
def lente_real(monkeypatch):
    """Deshace el stub del conftest: estos tests prueban la lente con un bridge falso."""
    monkeypatch.setattr(AuditorAgent, "revisar_senales_ia", _REVISAR_REAL)


@pytest.mark.asyncio
async def test_llm_signals_must_be_literal_quotes(lente_real):
    bridge = _BridgeFalso([{"senales": [
        {"frase": "Escribí scripts en Python que consultan APIs REST", "patron": "plantilla", "reescritura": "x"},
        {"frase": "frase inventada que no está en el CV", "patron": "inflado", "reescritura": "y"},
        "no es un dict",
    ]}])
    senales = await AuditorAgent(bridge=bridge).revisar_senales_ia(CV_NATURAL)
    assert [s["frase"] for s in senales] == ["Escribí scripts en Python que consultan APIs REST"]
    assert "Signs of AI writing" in bridge.prompts[0] and "<cv>" in bridge.prompts[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("respuesta", [RuntimeError("agy caído"), "texto", {"senales": "x"}, {}])
async def test_llm_lens_failure_yields_no_signals(lente_real, respuesta):
    assert await AuditorAgent(bridge=_BridgeFalso([respuesta])).revisar_senales_ia(CV_NATURAL) == []


@pytest.mark.asyncio
async def test_llm_signals_block_approval_and_drive_the_rewrite(lente_real):
    senal = {"senales": [{"frase": "bajé la auditoría de 90 aplicaciones", "patron": "inflado", "reescritura": "reescrito"}]}
    reescrito = {"cv_markdown": CV_NATURAL.replace("bajé la auditoría de 90 aplicaciones", "auditaba 90 aplicaciones"),
                 "pitch": "Hola"}
    # 1) lente sobre el original: 1 señal; 2) remediación; 3) lente sobre el remediado: limpio
    bridge = _BridgeFalso([senal, reescrito, {"senales": []}])
    keywords = ["Python", "SQL"]
    cv, pitch, reporte = await AuditorAgent(bridge=bridge).audit_and_remediate(CV_NATURAL, "Hola", keywords, max_attempts=2)
    assert "«bajé la auditoría de 90 aplicaciones» (inflado)" in bridge.prompts[1]
    assert "reescrito" in bridge.prompts[1]
    assert "auditaba 90 aplicaciones" in cv
    assert reporte["anti_ai"]["clean"] is True


def test_audit_with_llm_signals_is_not_approved():
    r = AuditorAgent(bridge=_BridgeFalso([])).audit(
        CV_NATURAL, "", ["Python"], senales_llm=[{"frase": "x", "patron": "p", "reescritura": ""}])
    assert r["approved"] is False and r["anti_ai"]["clean"] is False and r["anti_ai_score"] == 85


@pytest.mark.asyncio
async def test_leftover_llm_signals_get_the_suggested_rewrite_applied(lente_real):
    frase = "bajé la auditoría de 90 aplicaciones de dos días a una hora"
    senal = {"senales": [{"frase": frase, "patron": "vago", "reescritura": "reduje la auditoría de 90 aplicaciones a una hora"}]}
    # La lente vuelve a marcar lo mismo tras la reescritura del LLM (que no lo cambió)
    bridge = _BridgeFalso([senal, {"cv_markdown": CV_NATURAL, "pitch": "Hola"}, senal])
    cv, _, reporte = await AuditorAgent(bridge=bridge).audit_and_remediate(CV_NATURAL, "Hola", ["Python"], max_attempts=1)
    assert "reduje la auditoría de 90 aplicaciones a una hora" in cv and frase not in cv
    assert reporte["anti_ai"]["clean"] is True


def test_rewrite_is_skipped_when_quote_is_gone_or_rewrite_is_empty_or_huge():
    from core.auditor import aplicar_reescrituras
    texto, restantes = aplicar_reescrituras("uno dos tres", [
        {"frase": "dos", "reescritura": "2"},
        {"frase": "cuatro", "reescritura": "4"},
        {"frase": "tres", "reescritura": ""},  # vacía, pero no es oración ni cláusula final: queda
        {"frase": "uno", "reescritura": "x" * 200},
    ])
    assert texto == "uno 2 tres"
    assert [r["frase"] for r in restantes] == ["cuatro", "tres", "uno"]


@pytest.mark.asyncio
async def test_long_quotes_are_dropped_not_truncated(lente_real):
    larga = "Escribí scripts en **Python** que consultan APIs REST " * 6  # más de 240 caracteres
    bridge = _BridgeFalso([{"senales": [{"frase": "x" * 300, "patron": "p", "reescritura": "y"},
                                        {"frase": larga, "patron": "p", "reescritura": "y"}]}])
    assert await AuditorAgent(bridge=bridge).revisar_senales_ia(CV_NATURAL) == []


def test_rewrite_that_breaks_bold_markup_is_not_applied():
    from core.auditor import aplicar_reescrituras
    texto, restantes = aplicar_reescrituras("Usé **APIs REST** a diario", [{"frase": "Usé **APIs", "reescritura": "Usé APIs"}])
    assert texto == "Usé **APIs REST** a diario" and len(restantes) == 1



def test_empty_rewrite_removes_a_whole_sentence_or_a_trailing_clause():
    from core.auditor import aplicar_reescrituras
    perfil = "Analizo datos. Centralizo métricas dispersas entre áreas. Uso SQL."
    texto, restantes = aplicar_reescrituras(perfil, [{"frase": "Centralizo métricas dispersas entre áreas.", "reescritura": ""}])
    assert texto == "Analizo datos. Uso SQL." and restantes == []
    final = "Analizo datos. Centralizo métricas entre áreas."
    assert aplicar_reescrituras(final, [{"frase": "Centralizo métricas entre áreas.", "reescritura": ""}])[0] == "Analizo datos."
    vinieta = "• Elaboré especificaciones para integrar sistemas, cumpliendo los plazos y alcances acordados."
    texto, _ = aplicar_reescrituras(vinieta, [{"frase": "cumpliendo los plazos y alcances acordados.", "reescritura": ""}])
    assert texto == "• Elaboré especificaciones para integrar sistemas."


# Postulación 438, V2: la cita de la lente era parcial y su reescritura cerraba la oración con punto
_V2 = ("• Coordiné conectar negocio y tecnología para detectar a tiempo dependencias, casos límite y tareas viables "
       "para automatización inteligente.")
_SENAL_V2 = {"frase": "Coordiné conectar negocio y tecnología para detectar a tiempo dependencias, casos límite y "
                      "tareas viables",
             "reescritura": "Relevé dependencias, casos límite y oportunidades de automatización junto a los equipos "
                            "de negocio y desarrollo."}


def test_una_reescritura_que_deja_un_fragmento_tras_el_punto_no_se_aplica():
    from core.auditor import aplicar_reescrituras
    texto, restantes = aplicar_reescrituras(_V2, [_SENAL_V2])
    assert texto == _V2 and restantes == [_SENAL_V2]  # la línea entera va a la corrección con IA


def test_una_oracion_cortada_es_una_senal_del_texto_final():
    roto = "• Relevé dependencias junto a los equipos de negocio y desarrollo. para automatización inteligente."
    detectadas = scan_anti_ai_writing(roto)["detected_words"]
    assert any("desarrollo. para automatización inteligente." in d and "oración cortada" in d for d in detectadas)
    for sano in ("• Usé SQL, Excel, etc. y Python a diario.", "• Lideré el equipo. Después migré los datos.",
                 "• Bajé el tiempo de respuesta a aprox. un día.", "Vi la búsqueda de Analista Ssr. y me interesa.",
                 "• Atendí clientes de EE. UU. en inglés."):
        assert not any("oración cortada" in d for d in scan_anti_ai_writing(sano)["detected_words"])
