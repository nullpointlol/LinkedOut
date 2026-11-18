"""Señales estructurales conservadoras, reparación segura y presupuesto real."""
import os

import pytest

from core.auditor import AuditorAgent, _destinos_remediacion, scan_anti_ai_writing
from tests.test_auditoria_rapida import CV_ES, PITCH, KEYWORDS, EXPECTED_COMPANIES
from tests.test_rendimiento_auditoria import BridgeContado, REVISION_REAL, numero


def etiquetas(texto):
    return " | ".join(scan_anti_ai_writing(texto)["detected_words"])


def test_ejemplo_pdf_tres_senales():
    # invariante: evidencia verificable y gerundios instrumentales no son impacto vacío.
    assert scan_anti_ai_writing("• Consulté registros usando SQL.")["clean"]
    assert scan_anti_ai_writing("• Reduje los tiempos de despacho un 25%.")["clean"]
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    texto = ("• Organicé el cambio de guardia para acelerar el despacho\n"
             "según SLA.\n• Documenté el pase de guardia — incidencias pendientes.")
    resultado = scan_anti_ai_writing(texto)
    assert not resultado["clean"]
    assert resultado["anti_ai_score"] < 100
    for senal in ("impacto vago", "repetición cercana", "raya"):
        assert senal in etiquetas(texto)
    assert "simetría sintáctica" not in etiquetas(texto)


@pytest.mark.parametrize("texto,senal", [
    ("• Ordené tickets para acelerar el despacho según SLA.", "impacto vago"),
    ("• Automaticé reportes, reduciendo tiempos de auditoría.", "impacto vago"),
    ("Cambio de guardia.\nPase de guardia.", "repetición cercana"),
    ("Experiencia – soporte", "raya"),
    ("Experiencia — soporte", "raya"),
])
def test_senales_individuales(texto, senal):
    # invariante: el dict público y el puntaje limpio se conservan.
    assert scan_anti_ai_writing("") == {"clean": True, "detected_words": [], "anti_ai_score": 100}
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    assert senal in etiquetas(texto)
    assert scan_anti_ai_writing(texto)["anti_ai_score"] < 100


def test_simetria_dos_cierres():
    uno = "• Revisé lotes, evitando fallas en producción."
    # invariante: una única consecuencia no es una estructura repetida.
    assert "simetría sintáctica" not in etiquetas(uno)
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    dos = uno + "\n• Registré alertas, garantizando continuidad del servicio."
    assert "simetría sintáctica" in etiquetas(dos)
    cinco = dos + "\n• Consulté SQL.\n• Documenté APIs.\n• Clasifiqué eventos."
    assert "simetría sintáctica" in etiquetas(cinco)  # 2/5 = 0.40
    assert "simetría sintáctica" not in etiquetas(cinco + "\n• Escribí pruebas.")
    destinos = _destinos_remediacion("## EXPERIENCIA\n" + dos, "", {
        "ats": {}, "anti_ai": scan_anti_ai_writing(dos)})
    assert set(destinos) == {"EXPERIENCIA"}


def test_simetria_previa_entre_secciones():
    # invariante: con menos de cuatro viñetas de experiencia se examinaba
    # también el conjunto del documento. No perder ese bloqueo previo.
    texto = ("## EXPERIENCIA\n• Consulté SQL.\n• Documenté APIs.\n"
             "## HABILIDADES\n• Revisé lotes, evitando fallas.\n"
             "• Registré alertas, garantizando continuidad.")
    assert "simetría sintáctica" in etiquetas(texto)


def test_simetria_no_castiga_resultados_medidos_al_ampliar_umbral():
    medidas = (
        "• Lideré la migración de microservicios a Kubernetes, optimizando tiempos de respuesta un 35% y reduciendo costos operativos anuales.\n"
        "• Implementé arquitecturas tolerantes a fallos en AWS y GCP soportando más de 50.000 transacciones concurrentes por segundo.\n"
        "• Diseñé pipelines de integración continua automatizados reduciendo el tiempo de despliegue de 3 horas a 12 minutos.\n"
        "• Desarrollé clústeres elásticos con Terraform y Docker, asegurando un uptime del 99.99% en periodos de alto tráfico.\n"
        "• Automaticé políticas de monitoreo y trazabilidad distribuida para 40 servicios críticos de pagos."
    )
    # invariante: conservar el bloqueo previo de repetición mayoritaria (2/4),
    # incluso con cifras; el nuevo umbral no debe debilitarlo.
    previa = ("• Revisé lotes, reduciendo tiempos un 20%.\n"
              "• Registré alertas, evitando 30 fallas.\n"
              "• Consulté SQL.\n• Documenté APIs.")
    assert "simetría sintáctica" in etiquetas(previa)
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    # Dos cierres entre cinco viñetas con resultados verificables no son
    # consecuencias vacías: regresión de un CV que antes pasaba el control.
    texto = "## EXPERIENCIA\n" + medidas
    resultado = scan_anti_ai_writing(texto)
    assert resultado["clean"], resultado["detected_words"]
    assert resultado["anti_ai_score"] == 100
    assert _destinos_remediacion(texto, "", {"ats": {}, "anti_ai": resultado}) == {}


def test_gerundios_tecnicos_legitimos():
    # invariante: no rechazar gerundios instrumentales ni resultados medidos.
    for texto in ("• Consulté registros usando SQL.",
                  "• Analicé alertas clasificando eventos por severidad.",
                  "• Reduje tiempos un 25% en 90 aplicaciones.",
                  "Soy analista de soporte y adjunto mi CV."):
        assert scan_anti_ai_writing(texto)["clean"], etiquetas(texto)
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    for texto in ("https://ejemplo.test/a–b", "`INC—123`", "INC_42–prod",
                  "[Perfil](https://ejemplo.test/a—b)"):
        assert scan_anti_ai_writing(texto)["clean"], etiquetas(texto)
    assert "repetición cercana" not in etiquetas("Cambio de guardia.\n" + "Otra tarea. " * 100 + "Pase de guardia.")


def test_reparacion_segura_y_residual():
    auditor = AuditorAgent(bridge=BridgeContado())
    # invariante: datos y resultados verificables se conservan exactamente.
    protegido = "• Reduje tiempos de despacho un 25% usando SQL."
    assert auditor.remediate_text_deterministic(protegido) == protegido
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    base = "• Ordené tickets"
    for cierre in (", para acelerar el despacho según SLA.",
                   ", reduciendo tiempos de auditoría."):
        assert auditor.remediate_text_deterministic(base + cierre) == base + "."
    ambiguo = "• Organicé tareas para acelerar el despacho según SLA."
    assert auditor.remediate_text_deterministic(ambiguo) == ambiguo
    assert "impacto vago" in etiquetas(ambiguo)
    assert set(_destinos_remediacion("## EXPERIENCIA\n" + ambiguo, "", {
        "ats": {}, "anti_ai": scan_anti_ai_writing(ambiguo)})) == {"EXPERIENCIA"}


@pytest.mark.asyncio
async def test_bloqueos_previos_y_cuota(monkeypatch):
    monkeypatch.setattr(AuditorAgent, "revisar_senales_ia", REVISION_REAL)
    auditor = AuditorAgent(bridge=BridgeContado())
    # invariante: todos los bloqueos anteriores siguen vigentes, sin I/O real.
    assert auditor.audit(CV_ES, PITCH, KEYWORDS, EXPECTED_COMPANIES)["approved"]
    for cv, kw, empresas, senales in (
        (CV_ES, ["inexistente"], EXPECTED_COMPANIES, []),
        (CV_ES.replace("## IDIOMAS", "## OTROS"), KEYWORDS, EXPECTED_COMPANIES, []),
        (CV_ES, KEYWORDS, ["Empresa ausente"], []),
        (CV_ES, KEYWORDS, EXPECTED_COMPANIES,
         [{"frase": "trayectoria de 7 años", "patron": "plantilla", "reescritura": ""}]),
        ("## PERFIL\nSQL", ["SQL"], [], []),
    ):
        assert not auditor.audit(cv, PITCH, kw, empresas, senales_llm=senales)["approved"]
    for faltante in (False, True):
        n = numero(CV_ES, "• Análisis funcional")
        bridge = BridgeContado(respuesta={"lineas": [{"n": n, "texto": CV_ES.splitlines()[n] + " Rust, Kubernetes, Terraform."}]})
        cv, pitch, reporte = await AuditorAgent(bridge=bridge).audit_and_remediate(
            CV_ES, PITCH, KEYWORDS + (["Rust", "Kubernetes", "Terraform"] if faltante else []),
            expected_companies=EXPECTED_COMPANIES)
        assert len(bridge.llamadas) == (2 if faltante else 1)
        assert reporte["approved"]
        assert [l for l in cv.splitlines() if l.startswith("#") or "@" in l] == [
            l for l in CV_ES.splitlines() if l.startswith("#") or "@" in l]
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    reporte = auditor.audit(CV_ES, "Soy analista de soporte.", KEYWORDS, EXPECTED_COMPANIES)
    assert reporte["approved"]
    reporte = auditor.audit(CV_ES, "Organicé tareas para acelerar el despacho según SLA.", KEYWORDS, EXPECTED_COMPANIES)
    assert not reporte["approved"]
