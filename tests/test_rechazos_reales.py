"""Regresiones de los tres casos reales de rechazo (postulaciones 100, 101, 102):
1. Negrita ya corregida no rechaza (vigencia según patrón; desaparece si el formato no está).
2. Muletilla en web_ats no bloquea y queda registrada para revisar.
3. Impacto cualitativo verificable pasa y el no respaldado no (ausencia de cifra vs evidencia).
4. Presupuesto de llamadas igual (sin llamadas extra por oferta).
5. Registrar hallazgos por etapa también cuando termina en ERROR.
"""
import asyncio
import json
from copy import deepcopy
import pytest

import server
from core import database, revision_store
from core.auditor import (
    AuditorAgent, _es_impacto_vago, _senales_presentes, scan_anti_ai_writing,
)
from core.prompts_evaluacion import SIGNS_OF_AI_WRITING_WORDS
from tests.test_cv_formatting_and_a4_e2e import KEYWORDS, EXPECTED_COMPANIES
from tests.test_rendimiento_auditoria import BridgeContado, CV_ES, REVISION_REAL

CV_SINTETICO = CV_ES.rstrip("\n").replace("SAM ORTEGA", "ANA PRUEBA")
CV_SINTETICO = CV_SINTETICO.replace(CV_SINTETICO.splitlines()[1], "Villa Test | ana@example.test | https://example.test/cv")
PITCH_SINTETICO = "Hola equipo, les comparto mi postulación como Analista Funcional Técnico."


@pytest.fixture
def lente_real(monkeypatch):
    """Restaura la lente real para probar el comportamiento de señales."""
    monkeypatch.setattr(AuditorAgent, "revisar_senales_ia", REVISION_REAL)


def test_negrita_ya_corregida_no_rechaza():
    """1. Una objeción de negrita desaparece cuando ese formato ya no está en el texto.
    Las señales semánticas que sigan en el texto se mantienen."""
    texto_sin_negrita = "• Validé reglas de negocio con scripts en Python, evitando fallas."
    texto_con_negrita = "• Validé reglas de negocio con scripts en **Python**, evitando fallas."

    senal_negrita = {
        "frase": "**Python**",
        "patron": "exceso de negritas",
        "reescritura": "Python",
    }
    senal_semantica = {
        "frase": "evitando fallas",
        "patron": "impacto vago",
        "reescritura": "sin incidentes",
    }

    # Cuando el formato ya no está, la objeción de negrita desaparece
    assert _senales_presentes(texto_sin_negrita, [senal_negrita]) == []
    # Si la negrita sigue presente, la señal se detecta
    assert _senales_presentes(texto_con_negrita, [senal_negrita]) == [senal_negrita]

    # Las señales semánticas se mantienen independientemente del formato
    assert _senales_presentes(texto_sin_negrita, [senal_semantica]) == [senal_semantica]
    assert _senales_presentes(texto_con_negrita, [senal_semantica]) == [senal_semantica]

    # Ambas juntas sobre el texto ya corregido: solo queda la semántica
    presentes = _senales_presentes(texto_sin_negrita, [senal_negrita, senal_semantica])
    assert presentes == [senal_semantica]

    # Auditoría directa aprueba cuando las negritas corregidas ya no están
    auditor = AuditorAgent()
    reporte = auditor.audit(CV_SINTETICO, PITCH_SINTETICO, KEYWORDS,
                            expected_companies=EXPECTED_COMPANIES,
                            senales_llm=_senales_presentes(CV_SINTETICO, [senal_negrita]))
    assert reporte["approved"] is True
    assert reporte["anti_ai"]["clean"] is True


def test_objecion_de_formato_con_cita_sin_marcadores():
    senal = {"frase": "Validé reglas con Python", "patron": "exceso de negritas"}
    assert _senales_presentes("• Validé reglas con **Python**.", [senal]) == [senal]
    assert _senales_presentes("• Validé reglas con Python.\n• Consulté **SQL**.", [senal]) == []


def test_muletilla_en_web_ats_no_bloquea_y_queda_para_revisar():
    """2. Las señales en canales que no se envían (web_ats, inmail) quedan registradas
    para revisar y no bloquean. Se exige calidad al CV y al mail enviado."""
    pid = database.create_postulacion("Analista Funcional", "Empresa Test", "Oferta con SQL.")
    web_ats_con_muletilla = "Analista con amplia práctica en soporte de aplicaciones de alta exigencia."
    pitches = {
        "web_ats": web_ats_con_muletilla,
        "inmail": "Mensaje breve para contacto.",
    }
    database.update_postulacion(pid, cv_markdown=CV_SINTETICO, pitch=PITCH_SINTETICO,
                                pitches_multicanal=pitches)

    reporte_aprobado = {
        "approved": True,
        "score_ats": 100.0,
        "ats": {"ats_score": 100.0, "found_keywords": KEYWORDS, "missing_keywords": []},
        "anti_ai": {"clean": True, "detected_words": []},
    }

    final, motivos = server._dictamen_final(
        database.get_postulacion(pid),
        CV_SINTETICO,
        PITCH_SINTETICO,
        pitches,
        reporte_aprobado,
        keywords=KEYWORDS,
    )

    # No bloquea la postulación
    assert final["approved"] is True
    assert motivos == []

    # Queda registrada en revision_store para revisar
    registrados = [r for r in revision_store.listar()
                   if r["postulacion_id"] == pid and r["campo"] == "mensaje (web_ats)"]
    assert len(registrados) > 0
    assert any("alta exigencia" in r["detalle"] for r in registrados)

    # Remediación determinista sanea la muletilla del canal preservando el hecho
    saneado = AuditorAgent().remediate_text_deterministic(web_ats_con_muletilla)
    assert "alta exigencia" not in saneado
    assert "complejidad" not in saneado
    assert "exigencias altas" in saneado


def test_impacto_cualitativo_verificable_pasa_y_el_no_respaldado_no():
    """3. Distinguir ausencia de cifra de ausencia de evidencia.
    Un resultado cualitativo verificable no bloquea; uno no respaldado sí."""
    # Resultados cualitativos verificables (gobernanza, procesos, testing, consistencia): NO son vagos
    cualitativo_1 = "• Reduje errores de validación según la comparación documentada antes y después en las actas."
    cualitativo_2 = "• Mejoré la calidad de entrega: cada requisito quedó vinculado a su caso de prueba en la matriz de trazabilidad."
    cualitativo_3 = "• Diseñé ambientes de testing; reduje tiempos de certificación según la comparación registrada antes y después en las actas."

    assert _es_impacto_vago(cualitativo_1) is False
    assert _es_impacto_vago(cualitativo_2) is False
    assert _es_impacto_vago(cualitativo_3) is False

    scan_1 = scan_anti_ai_writing(cualitativo_1)
    assert scan_1["clean"] is True, scan_1["detected_words"]
    scan_2 = scan_anti_ai_writing(cualitativo_2)
    assert scan_2["clean"] is True, scan_2["detected_words"]

    # Resultados no respaldados (reducción de tiempos sin evidencia cuantitativa ni cualitativa): SÍ son vagos
    no_respaldado_1 = "• Automaticé reportes, reduciendo tiempos de auditoría."
    no_respaldado_2 = "• Ordené tickets para acelerar el despacho según SLA."

    assert _es_impacto_vago(no_respaldado_1) is True
    assert _es_impacto_vago(no_respaldado_2) is True

    scan_vago_1 = scan_anti_ai_writing(no_respaldado_1)
    assert not scan_vago_1["clean"]
    assert any("impacto vago" in d for d in scan_vago_1["detected_words"])


@pytest.mark.asyncio
async def test_presupuesto_de_llamadas_igual(lente_real):
    """4. El presupuesto de llamadas LLM por oferta se mantiene idéntico:
    1 llamada en caso limpio, exactamente 2 llamadas con remediación consolidada."""
    # Caso A: CV limpio -> exactamente 1 llamada (revisión de señales)
    bridge_limpio = BridgeContado(senales=[])
    auditor_limpio = AuditorAgent(bridge=bridge_limpio)
    cv_res, pitch_res, pitches_res, rep_limpio = await auditor_limpio.audit_and_remediate_all(
        CV_SINTETICO,
        PITCH_SINTETICO,
        pitches_multicanal={"web_ats": "Texto de prueba ATS."},
        keywords=KEYWORDS,
        max_attempts=1,
        expected_companies=EXPECTED_COMPANIES,
    )
    assert len(bridge_limpio.llamadas) == 1
    assert len(bridge_limpio.revisiones) == 1
    assert len(bridge_limpio.remediaciones) == 0
    assert rep_limpio["approved"] is True

    # Caso B: CV con hallazgos -> exactamente 2 llamadas (1 revisión + 1 remediación consolidada)
    senal_remediar = {
        "frase": "recorrido de 7 años",
        "patron": "impacto vago",
        "reescritura": "trayectoria de 7 años",
    }
    cita = "trayectoria de 7 años"
    assert cita in CV_SINTETICO
    cv_con_hallazgo = CV_SINTETICO.replace(cita, "recorrido de 7 años")
    assert cv_con_hallazgo != CV_SINTETICO
    n = next(n for n, l in enumerate(cv_con_hallazgo.splitlines()) if "recorrido de 7 años" in l)
    bridge_remediar = BridgeContado(
        respuesta={"lineas": [{"n": n, "texto": CV_SINTETICO.splitlines()[n]}]},
        senales=[senal_remediar],
    )
    auditor_remediar = AuditorAgent(bridge=bridge_remediar)
    cv_rem, pitch_rem, pitches_rem, rep_rem = await auditor_remediar.audit_and_remediate_all(
        cv_con_hallazgo,
        PITCH_SINTETICO,
        pitches_multicanal={"web_ats": "Texto de prueba ATS."},
        keywords=KEYWORDS,
        max_attempts=1,
        expected_companies=EXPECTED_COMPANIES,
    )
    assert len(bridge_remediar.llamadas) == 2
    assert len(bridge_remediar.revisiones) == 1
    assert len(bridge_remediar.remediaciones) == 1
    assert cv_rem == CV_SINTETICO
    assert rep_rem["approved"] is True


@pytest.mark.asyncio
async def test_registrar_hallazgos_por_etapa_cuando_termina_en_error():
    """5. Registrar hallazgos por etapa también cuando termina en ERROR."""
    pid = database.create_postulacion("Analista", "Empresa", "Oferta de prueba.")
    server._ETAPAS_PIPELINE[pid] = [
        {"etapa": "auditoria", "segundos": 2.1, "llamadas": 1, "modelo": "gemini-3.8-flash-high", "tokens": 150}
    ]

    reporte_rechazado = {
        "approved": False,
        "score_ats": 60.0,
        "verdict": "RECHAZADO",
        "deducciones": ["Faltan empresas obligatorias"],
        "hallazgos_unificados": {
            "requiere_accion": True,
            "lista_observaciones": ["Restaurar empresas obligatorias del historial laboral: KORVA."],
        },
    }

    with pytest.raises(RuntimeError, match="Revisá el CV"):
        await server._finalizar_cv(pid, CV_SINTETICO, PITCH_SINTETICO, {}, reporte_rechazado, KEYWORDS)

    actual = database.get_postulacion(pid)
    assert actual["status"] == "ERROR"
    assert actual["reporte_auditoria"]["approved"] is False

    # Telemetría conserva las etapas medidas a pesar del error
    telemetria = actual.get("telemetry")
    if isinstance(telemetria, str):
        telemetria = json.loads(telemetria)
    assert isinstance(telemetria, dict)
    assert "etapas" in telemetria
    assert any(e["etapa"] == "auditoria" for e in telemetria["etapas"])
    assert telemetria["status"] == "ERROR"


@pytest.mark.parametrize("texto", [
    "• Reduje tiempos documentando reglas de negocio.",
    "• Diseñé ambientes de testing, agilizando tiempos de certificación.",
    "• Optimicé la calidad de entrega aplicando criterios de aceptación.",
    "• Reduje incidencias validando reglas de negocio con casos de prueba.",
    "• Documenté reglas de negocio,\n  reduciendo tiempos de auditoría.",
])
def test_practica_no_demuestra_resultado(texto):
    assert _es_impacto_vago(texto)
    assert not scan_anti_ai_writing(texto)["clean"]


@pytest.mark.parametrize("texto,esperado", [
    ("Apasionado por SQL", "interesado en SQL"),
    ("Aplicaciones de alta exigencia", "Aplicaciones con exigencias altas"),
    ("Sólida trayectoria con amplia experiencia", "trayectoria con experiencia"),
    ("Sólida experiencia en SQL", "experiencia en SQL"),
    ("Marcado interés en finanzas", "interés en finanzas"),
    ("Vasto conocimiento de arquitectura", "conocimiento de arquitectura"),
    ("Destacada labor en el equipo", "labor en el equipo"),
])
def test_saneado_no_inventa_atributos_y_es_estable(texto, esperado):
    auditor = AuditorAgent()
    referencia = " https://example.test/robusto ana+proactivo@example.test `leverage`"
    limpio = auditor.remediate_text_deterministic(texto + referencia)
    assert limpio.lower() == (esperado + referencia).lower()
    assert scan_anti_ai_writing(limpio)["clean"]
    assert auditor.remediate_text_deterministic(limpio) == limpio


@pytest.mark.parametrize("expresion", SIGNS_OF_AI_WRITING_WORDS)
def test_lista_completa_de_cliches_se_sanea_sin_introducir_otro(expresion):
    auditor = AuditorAgent()
    original = "## PERFIL\nMi trabajo: " + expresion + " SQL."
    limpio = auditor.remediate_text_deterministic(original)
    if expresion in {"robusto", "robusta"}:
        # No existe una sustitución general segura por otro atributo técnico.
        assert limpio == original
        assert expresion in scan_anti_ai_writing(limpio)["detected_words"]
        return
    assert not set(scan_anti_ai_writing(limpio)["detected_words"]) & set(SIGNS_OF_AI_WRITING_WORDS)
    assert auditor.remediate_text_deterministic(limpio) == limpio


@pytest.mark.asyncio
@pytest.mark.parametrize("simultaneos", [False, True])
async def test_consolidacion_canales_antes_de_unica_remediacion(lente_real, simultaneos):
    malo = "Reduje tiempos documentando reglas de negocio."
    bueno = "Documenté reglas de negocio."
    cv = CV_SINTETICO
    parches = []
    if simultaneos:
        n = next(n for n, l in enumerate(cv.splitlines()) if l.startswith("Analista Técnico Funcional"))
        cv = cv.replace(cv.splitlines()[n], malo)
        parches = [{"n": n, "texto": CV_SINTETICO.splitlines()[n]}]
    bridge = BridgeContado(respuesta={"lineas": parches, "pitch": bueno,
        "pitches_multicanal": {"email": bueno, "web_ats": bueno, "inmail": "CAMBIO AJENO"}})
    auditor = AuditorAgent(bridge=bridge)
    cv_fin, pitch_fin, canales, reporte = await auditor.audit_and_remediate_all(
        cv, malo if simultaneos else PITCH_SINTETICO,
        {"email": malo, "web_ats": malo, "inmail": "Contacto breve."},
        keywords=KEYWORDS, expected_companies=EXPECTED_COMPANIES)
    assert len(bridge.revisiones) == 1
    assert len(bridge.remediaciones) == 1
    prompt = bridge.remediaciones[0]
    assert '"email"' in prompt and '"web_ats"' in prompt
    assert malo in prompt
    assert cv_fin == CV_SINTETICO
    assert pitch_fin == (bueno if simultaneos else PITCH_SINTETICO)
    assert canales == {"email": bueno, "web_ats": bueno, "inmail": "Contacto breve."}
    assert reporte["approved"] is True
    assert reporte.get("hallazgos_canales") == []
    evidencia = auditor.hallazgos_por_etapa
    assert [e["etapa"] for e in evidencia] == [
        "saneamiento_determinista", "auditoria_inicial", "auditoria_final"]
    assert evidencia[1]["canales"]["email"]
    assert evidencia[2]["canales"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("canal", ["email", "web_ats", "inmail"])
async def test_residual_de_canal_se_registra_y_solo_email_bloquea(lente_real, canal):
    malo = "Reduje tiempos documentando reglas de negocio."
    auditor = AuditorAgent(bridge=BridgeContado())
    cv, pitch, canales, reporte = await auditor.audit_and_remediate_all(
        CV_SINTETICO, PITCH_SINTETICO, {canal: malo},
        keywords=KEYWORDS, expected_companies=EXPECTED_COMPANIES)
    assert reporte["hallazgos_canales"]
    assert reporte["approved"] is (canal != "email")
    pid = database.create_postulacion("Analista", "Empresa", "Oferta sintética")
    database.update_postulacion(pid, cv_markdown=cv, pitch=pitch, pitches_multicanal=canales)
    final, motivos = server._dictamen_final(database.get_postulacion(pid), cv, pitch,
                                           canales, reporte, KEYWORDS)
    assert final["approved"] is (canal != "email")
    assert bool(motivos) is (canal == "email")
    assert any(r["campo"] == f"mensaje ({canal})" for r in revision_store.listar())


from tests.test_cv_mail_final_contract import RUTAS, frontera, _crear, _ejecutar


def test_etapas_del_intento_actual_no_se_mezclan_con_el_anterior():
    pid = database.create_postulacion("Analista", "Empresa", "Oferta sintética")
    anterior = {"etapas": [{"etapa": "auditoria", "segundos": 30}], "total_s": 30}
    actual = {"etapa": "auditoria", "segundos": 2.1}
    server._ETAPAS_PIPELINE[pid] = [actual]
    consolidada = server._telemetria_con_etapas(pid, anterior)
    assert consolidada["etapas"] == [actual]
    assert consolidada["total_s"] == 2.1
    assert server._telemetria_con_etapas(pid, consolidada) == consolidada


@pytest.mark.asyncio
@pytest.mark.parametrize("ruta", RUTAS)
async def test_error_exterior_conserva_etapas_y_hallazgos(frontera, monkeypatch, ruta):
    pid = _crear(frontera)
    rechazado = {"approved": False, "deducciones": ["Falta una empresa obligatoria"],
                 "hallazgos_unificados": {"requiere_accion": True,
                     "lista_observaciones": ["Falta una empresa obligatoria"]}}

    async def auditar(self, cv, pitch, pitches, **kwargs):
        return cv, pitch, pitches, deepcopy(rechazado)

    finalizar_real = server._finalizar_cv

    async def finalizar(*args, **kwargs):
        medida = next(e for e in server._ETAPAS_PIPELINE[pid] if e["etapa"] == "auditoria")
        medida.update(segundos=2.1, llamadas=1, tokens=150)
        return await finalizar_real(*args, **kwargs)

    monkeypatch.setattr(AuditorAgent, "audit_and_remediate_all", auditar)
    monkeypatch.setattr(server, "_finalizar_cv", finalizar)
    await asyncio.wait_for(_ejecutar(ruta, pid), timeout=15)
    actual = database.get_postulacion(pid)
    assert actual["status"] == "ERROR"
    tel = actual["telemetry"]
    etapas = [e for e in tel["etapas"] if e["etapa"] == "auditoria"]
    assert len(etapas) == 1
    assert etapas[0]["llamadas"] == 1
    assert etapas[0]["hallazgos"] == rechazado["hallazgos_unificados"]
    assert tel["total_s"] >= 2.1
    assert tel["status"] == "ERROR"
    assert not any(t == "complete" for t, d in frontera.eventos)




@pytest.mark.asyncio
async def test_canal_no_enviado_no_dispara_remediacion(lente_real):
    """un hallazgo solo en web_ats con CV aprobado no suma llamadas."""
    bridge = BridgeContado(senales=[])
    auditor = AuditorAgent(bridge=bridge)
    _, _, canales, reporte = await auditor.audit_and_remediate_all(
        CV_SINTETICO, PITCH_SINTETICO,
        pitches_multicanal={"web_ats": "Reduje tiempos documentando reglas de negocio."},
        keywords=KEYWORDS, max_attempts=1, expected_companies=EXPECTED_COMPANIES)
    assert len(bridge.llamadas) == 1 and bridge.remediaciones == []
    assert reporte["approved"] is True
