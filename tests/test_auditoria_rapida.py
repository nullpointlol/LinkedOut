"""Tests del contrato de auditoría rápida en máximo dos llamadas.

Contrato:
1. Reglas deterministas primero sobre el CV, antes de cualquier llamada a la IA.
2. Una sola revisión con IA por CV; su resultado no se vuelve a pedir después de parchar.
3. Remediación consolidada en una sola llamada si quedan hallazgos; cero si no quedan.
4. Verificación final sin IA.
5. Registro de remediaciones fallidas y preservación del resultado determinista.
"""
import logging
from unittest.mock import AsyncMock

import pytest

from core.auditor import AuditorAgent, scan_anti_ai_writing, evaluate_ats_compliance
from tests.test_cv_formatting_and_a4_e2e import SAMPLE_E2E_CV, KEYWORDS, EXPECTED_COMPANIES
from tests.test_rendimiento_auditoria import REVISION_REAL, BridgeContado, numero

CV_ES = SAMPLE_E2E_CV.rstrip("\n")
PITCH = "Hola equipo, les comparto mi postulación como Analista Funcional Técnico."
FALTANTES = ["Kubernetes", "Terraform", "Rust"]
KW_FALTANTES = [*KEYWORDS, *FALTANTES]


@pytest.fixture
def lente_real(monkeypatch):
    monkeypatch.setattr(AuditorAgent, "revisar_senales_ia", REVISION_REAL)


@pytest.mark.asyncio
async def test_cv_con_hallazgos_exactamente_dos_llamadas_y_pasa_chequeos(lente_real):
    """(a) CV con hallazgos -> exactamente 2 llamadas (revisión + remediación consolidada).

    El CV final pasa los chequeos deterministas y no se repite la revisión.
    """
    n_skills = numero(CV_ES, "• Análisis funcional")
    linea_original = CV_ES.splitlines()[n_skills]
    linea_remediada = linea_original + " Kubernetes, Terraform, Rust."

    bridge = BridgeContado(
        respuesta={
            "lineas": [{"n": n_skills, "texto": linea_remediada}],
            "pitch": PITCH,
        }
    )
    auditor = AuditorAgent(bridge=bridge)

    cv_res, pitch_res, report = await auditor.audit_and_remediate(
        CV_ES, PITCH, KW_FALTANTES, max_attempts=1, expected_companies=EXPECTED_COMPANIES
    )

    # Exactamente 2 llamadas: 1 revisión + 1 remediación consolidada
    assert len(bridge.llamadas) == 2, f"Se esperaban 2 llamadas, hubo {len(bridge.llamadas)}"
    assert len(bridge.revisiones) == 1, "Debe haber una sola revisión con IA"
    assert len(bridge.remediaciones) == 1, "Debe haber una sola remediación consolidada"

    # Verificación final determinista aprobada
    assert report["approved"] is True
    assert "Kubernetes" in cv_res
    scan = scan_anti_ai_writing(f"{cv_res}\n\n{pitch_res}")
    assert scan["clean"] is True
    ats = evaluate_ats_compliance(cv_res, KW_FALTANTES, expected_companies=EXPECTED_COMPANIES)
    assert ats["ats_score"] >= 80.0
    assert ats["missing_keywords"] == []


@pytest.mark.asyncio
async def test_cv_limpio_una_sola_llamada_solo_revision(lente_real):
    """(b) CV limpio -> 1 llamada (solo revisión, cero llamadas de remediación)."""
    bridge = BridgeContado()
    auditor = AuditorAgent(bridge=bridge)

    cv_res, pitch_res, report = await auditor.audit_and_remediate(
        CV_ES, PITCH, KEYWORDS, max_attempts=1, expected_companies=EXPECTED_COMPANIES
    )

    assert len(bridge.llamadas) == 1, f"Se esperaba 1 llamada, hubo {len(bridge.llamadas)}"
    assert len(bridge.revisiones) == 1
    assert len(bridge.remediaciones) == 0
    assert cv_res == CV_ES
    assert pitch_res == PITCH
    assert report["approved"] is True


@pytest.mark.asyncio
async def test_muletillas_lista_negra_resueltas_deterministas_sin_remediacion(lente_real):
    """(c) Muletillas de lista negra solas -> se resuelven por regla determinista

    antes de cualquier llamada a la IA, sin llamada de remediación posterior.
    """
    # Insertamos términos estrictos de ANTI_AI_REPLACEMENTS
    cv_con_muletillas = CV_ES.replace(
        "Analista Técnico Funcional con trayectoria de 7 años",
        "Analista Técnico Funcional con sólida trayectoria y enfoque holístico articulando sinergias",
    )
    assert not scan_anti_ai_writing(cv_con_muletillas)["clean"]

    bridge = BridgeContado()
    auditor = AuditorAgent(bridge=bridge)

    cv_res, pitch_res, report = await auditor.audit_and_remediate(
        cv_con_muletillas, PITCH, KEYWORDS, max_attempts=1, expected_companies=EXPECTED_COMPANIES
    )

    # 1 sola llamada (revisión), 0 remediaciones
    assert len(bridge.llamadas) == 1, f"Se esperaba 1 llamada, hubo {len(bridge.llamadas)}"
    assert len(bridge.revisiones) == 1
    assert len(bridge.remediaciones) == 0

    # Las muletillas fueron eliminadas determinísticamente
    assert "sólida trayectoria" not in cv_res
    assert "enfoque holístico" not in cv_res
    assert "articulando sinergias" not in cv_res
    assert report["approved"] is True


@pytest.mark.asyncio
async def test_remediacion_fallida_se_registra_y_conserva_resultado_determinista(lente_real, caplog):
    """(d) Remediación fallida -> se registra en logs y el CV queda con el resultado determinista."""
    # CV con keywords faltantes y una muletilla determinista
    cv_con_muletilla = CV_ES.replace(
        "Analista Técnico Funcional con trayectoria de 7 años",
        "Analista Técnico Funcional con sólida experiencia de 7 años",
    )
    bridge = BridgeContado()
    bridge.respuesta = AsyncMock(side_effect=RuntimeError("Falla de API/Timeout en remediación"))
    auditor = AuditorAgent(bridge=bridge)

    with caplog.at_level(logging.WARNING):
        cv_res, pitch_res, report = await auditor.audit_and_remediate(
            cv_con_muletilla, PITCH, KW_FALTANTES, max_attempts=1, expected_companies=EXPECTED_COMPANIES
        )

    # Se intentó la revisión y la remediación (que falló)
    assert len(bridge.llamadas) == 2
    assert len(bridge.revisiones) == 1
    assert len(bridge.remediaciones) == 1

    # Se registró la advertencia en el logger
    assert any("Falló la remediación" in r.message for r in caplog.records)

    # El CV conserva el resultado de la pasada determinista (se limpió la muletilla)
    assert "sólida experiencia" not in cv_res
    # en contenido del candidato el reemplazo es neutro (no atribuye «comprobada»).
    assert "experiencia comprobada" not in cv_res
    # Pero no aprueba porque siguen faltando keywords
    assert report["approved"] is False
    assert report["ats"]["missing_keywords"] == FALTANTES


@pytest.mark.asyncio
@pytest.mark.parametrize("frase,perfil_final", [
    ("Trayectoria de 7 años", None),
    ("trayectoria de 7 años", "TRAYECTORIA de 7 años"),
    ("trayectoria de 7 años", "trayectoria   de **7 años**"),
])
async def test_senal_aceptada_normalizada_sigue_bloqueando(lente_real, frase, perfil_final):
    """La misma señal bloquea antes y después del parche aunque varíe su presentación."""
    senal = {"frase": frase, "patron": "plantilla controlada", "reescritura": ""}
    n_skills = numero(CV_ES, "• Análisis funcional")
    cambios = [{"n": n_skills, "texto": CV_ES.splitlines()[n_skills]
                + " Kubernetes, Terraform, Rust."}]
    if perfil_final:
        n_perfil = numero(CV_ES, "Analista Técnico Funcional con trayectoria")
        cambios.append({"n": n_perfil, "texto": CV_ES.splitlines()[n_perfil].replace(
            "trayectoria de 7 años", perfil_final)})
    bridge = BridgeContado()
    bridge.run_prompt = AsyncMock(side_effect=[
        {"senales": [senal]}, {"lineas": cambios, "pitch": ""}])
    auditor = AuditorAgent(bridge=bridge)

    cv, pitch, report = await auditor.audit_and_remediate(
        CV_ES, PITCH, KW_FALTANTES, max_attempts=1, expected_companies=EXPECTED_COMPANIES)

    assert report["approved"] is False
    assert report["anti_ai"]["senales_llm"] == [senal]
    assert report["ats"]["missing_keywords"] == []
    assert report == auditor.audit(cv, pitch, KW_FALTANTES,
                                  expected_companies=EXPECTED_COMPANIES, senales_llm=[senal])
    assert bridge.run_prompt.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("faltantes", [False, True])
async def test_saneado_conserva_identidad_contacto_y_encabezados(lente_real, faltantes):
    """Ninguna pasada determinista o sugerencia del LLM puede editar datos protegidos."""
    cv_base = CV_ES.replace("# SAM ORTEGA", "# PROACTIVO ORTEGA").replace(
        "sam.ortega@example.com", "proactivo@example.com").replace(
        "### Desarrollador de Software", "### Desarrollador proactivo de Software")
    n_skills = numero(cv_base, "• Análisis funcional")
    bridge = BridgeContado(
        senales=[{"frase": "PROACTIVO ORTEGA", "patron": "plantilla controlada",
                  "reescritura": "OTRO NOMBRE"}],
        respuesta={"lineas": [{"n": n_skills, "texto": cv_base.splitlines()[n_skills]
                               + " Kubernetes, Terraform, Rust."}], "pitch": ""})
    auditor = AuditorAgent(bridge=bridge)

    cv, _, report = await auditor.audit_and_remediate(
        cv_base, PITCH, KW_FALTANTES if faltantes else KEYWORDS,
        max_attempts=1, expected_companies=EXPECTED_COMPANIES)

    protegidas = [l for l in cv_base.splitlines() if l.startswith("#") or "@" in l]
    assert [l for l in cv.splitlines() if l.startswith("#") or "@" in l] == protegidas
    assert report["approved"] is False
    assert len(bridge.llamadas) == (2 if faltantes else 1)


@pytest.mark.asyncio
async def test_reescritura_vacia_no_borra_vineta_de_experiencia(lente_real):
    """Una sugerencia que borra una línea completa se rechaza igual que en HEAD."""
    vineta = next(l for l in CV_ES.splitlines() if l.startswith("• Automaticé"))
    senal = {"frase": vineta, "patron": "plantilla controlada", "reescritura": ""}
    bridge = BridgeContado(senales=[senal])
    auditor = AuditorAgent(bridge=bridge)

    cv, pitch, report = await auditor.audit_and_remediate(
        CV_ES, PITCH, KEYWORDS, max_attempts=1, expected_companies=EXPECTED_COMPANIES)

    assert cv == CV_ES
    assert vineta in cv.splitlines()
    assert report["approved"] is False
    assert report["anti_ai"]["senales_llm"] == [senal]
    assert report == auditor.audit(cv, pitch, KEYWORDS,
                                  expected_companies=EXPECTED_COMPANIES, senales_llm=[senal])
    assert len(bridge.llamadas) == 2
