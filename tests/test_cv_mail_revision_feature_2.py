"""Ronda 2: dictamen del texto real, rayas antes de auditar y literales por fragmento.

Plan: reproducir los tres defectos; corregir sin IA extra; ejecutar la selección
de la misión. Sólo datos aislados y fronteras externas falsas.

Ronda 2: conservar el rechazo de aprobaciones simuladas sin diagnóstico ATS.
Los fixtures heredados autorizados usan CV válidos sin aflojar sus aserciones.
Validación: ejecutar la selección de la misión; la compuerta total queda al loop.
"""
from copy import deepcopy
import re
from unittest.mock import AsyncMock

import pytest

import server
from core import database, revision_store
from core.auditor import AuditorAgent, scan_anti_ai_writing
from tests.test_cv_mail_final_contract import (
    CV, PITCH, KEYWORDS, RUTAS, frontera, _crear, _ejecutar,
)


SIN_EVIDENCIAS = re.sub(r"^• .*", "• SQL APIs REST Testing Microservicios Análisis funcional.", CV,
                        flags=re.MULTILINE)


@pytest.mark.asyncio
@pytest.mark.parametrize("ruta", RUTAS)
@pytest.mark.parametrize("texto", [
    "# CV\n## PERFIL\nTest",
    "# CV Juan Pérez (Contingencia Legada)\n\n## PERFIL PROFESIONAL\n"
    "Desarrollador con experiencia en Python y bases de datos.",
    SIN_EVIDENCIAS,
], ids=["fixture-eventos", "fixture-contingencia", "sin-evidencias"])
async def test_aprobacion_parcial_no_exime_al_texto_final_de_la_auditoria(
        frontera, monkeypatch, ruta, texto):
    """Un auditor sustituido no puede habilitar READY con un CV inválido."""
    frontera.cv = texto
    pid = _crear(frontera)
    reporte_simulado = {"score_ats": 95, "approved": True}

    async def auditar(self, cv, pitch, pitches, **kwargs):
        return cv, pitch, pitches, deepcopy(reporte_simulado)

    monkeypatch.setattr(AuditorAgent, "audit_and_remediate_all", auditar)
    await _ejecutar(ruta, pid)

    actual = database.get_postulacion(pid)
    reporte = actual["reporte_auditoria"]
    if texto is SIN_EVIDENCIAS:
        # sin métricas que no se pueden inventar es un límite del perfil, no un bloqueo:
        # vale la medición real (no la del auditor sustituido) y queda aprobado con observaciones.
        assert actual["status"] == "READY" and actual["cv_markdown"] == texto
        assert reporte["approved"] is True and reporte["limites_perfil"] is True
        assert reporte["ats"]["ats_score"] < 80 and reporte["deducciones"]
        assert reporte_simulado == {"score_ats": 95, "approved": True}
        return
    assert actual["status"] == "ERROR"
    assert actual["cv_markdown"] == texto
    assert reporte["approved"] is False and reporte["ats"]["ats_score"] < 80
    assert reporte["deducciones"] and reporte["hallazgos_unificados"]["requiere_accion"]
    assert any(i["postulacion_id"] == pid and i["detalle"] for i in revision_store.listar())
    assert not any(t == "complete" or d.get("status") == "READY" for t, d in frontera.eventos)
    assert reporte_simulado == {"score_ats": 95, "approved": True}
    assert frontera.bridge_contado.llamadas == []


@pytest.mark.asyncio
@pytest.mark.parametrize("ruta", RUTAS)
async def test_pipeline_ats_final_bajo_sin_perder_keywords_sigue_con_observaciones(frontera, monkeypatch, ruta):
    """un ATS bajo solo por métricas que no se pueden inventar no deja el CV en ERROR."""
    pid = _crear(frontera)
    medicion = AuditorAgent().audit(SIN_EVIDENCIAS, PITCH, KEYWORDS)
    assert medicion["approved"] is False and medicion["ats"]["ats_score"] < 80
    assert not medicion["ats"]["missing_keywords"] and medicion["anti_ai"]["clean"]
    monkeypatch.setattr(server, "_asegurar_completo", AsyncMock(
        return_value=(SIN_EVIDENCIAS, PITCH, {}, [])))
    await _ejecutar(ruta, pid)
    actual = database.get_postulacion(pid)
    assert actual["status"] == "READY"
    assert actual["cv_markdown"] == SIN_EVIDENCIAS
    assert actual["reporte_auditoria"]["approved"] is True
    assert actual["reporte_auditoria"]["verdict"] == "PASE CON OBSERVACIONES"
    assert actual["reporte_auditoria"]["ats"] == medicion["ats"]
    assert actual["reporte_auditoria"]["deducciones"] == medicion["deducciones"]
    assert len(frontera.bridge_contado.llamadas) == 1


def test_rechazo_previo_se_conserva_incluso_si_cambia_el_ats(frontera):
    pid = _crear(frontera)
    previo = {"approved": False, "deducciones": ["Señal LLM pendiente"], "ats_score": 91}
    copia = deepcopy(previo)
    final, motivos = server._dictamen_final(database.get_postulacion(pid), SIN_EVIDENCIAS,
                                           PITCH, {}, previo, KEYWORDS)
    assert final == copia == previo and "Señal LLM pendiente" in motivos
    assert frontera.bridge_contado.llamadas == []


@pytest.mark.asyncio
@pytest.mark.parametrize("ruta", RUTAS)
async def test_pipeline_rango_con_raya_equivale_a_ascii_sin_remediacion(frontera, ruta):
    for separador in ("-", "–", "—"):
        frontera.cv = CV.replace("2018 - 2025", f"2018 {separador} 2025")
        pid = _crear(frontera)
        frontera.bridge_contado.llamadas.clear()
        await _ejecutar(ruta, pid)
        actual = database.get_postulacion(pid)
        assert actual["status"] == "READY"
        assert actual["reporte_auditoria"]["approved"] is True
        assert actual["cv_markdown"] == CV
        assert len(frontera.bridge_contado.revisiones) == 1
        assert frontera.bridge_contado.remediaciones == []


@pytest.mark.parametrize("campo", ["CV", "pitch", "body", "subject"])
@pytest.mark.parametrize("literal", ["https://ejemplo.test/a–b", "ana–qa@example.test", "ANA–PRUEBA",
                                    "Villa — Test | ana@example.test"])
def test_saneado_y_escaneo_protegen_fragmentos_y_no_la_linea(monkeypatch, campo, literal):
    monkeypatch.setattr(server, "_perfil_o_vacio", lambda: {
        "nombre": "ANA–PRUEBA", "contacto": "Villa — Test | ana@example.test"})
    texto = f"Documenté procesos — {literal}"
    esperado = f"Documenté procesos - {literal}"
    assert server._sanear_texto_final(texto, campo) == esperado
    assert server._sanear_texto_final(esperado, campo) == esperado
    assert server._motivos_texto_final(esperado, campo) == []
    assert any("raya" in m for m in server._motivos_texto_final(texto, campo))


def test_reauditoria_usa_los_criterios_del_reporte_y_mide_el_ats_actual(frontera):
    pid = _crear(frontera, ["keyword heredada ausente"])
    previo = AuditorAgent().audit(CV, PITCH, KEYWORDS)
    final, motivos = server._dictamen_final(database.get_postulacion(pid), SIN_EVIDENCIAS,
                                           PITCH, {}, previo, cv_antes=CV)
    # medido sobre el CV actual, solo faltan métricas: aprobado con observaciones
    assert final["approved"] is True and final["limites_perfil"] is True and not motivos
    assert final["ats"]["missing_keywords"] == []
    assert final["ats"]["dimensiones"]["metricas_empiricas"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("ruta", RUTAS)
async def test_rayas_normalizadas_antes_de_entrar_al_auditor(frontera, monkeypatch, ruta):
    frontera.cv = CV.replace("2018 - 2025", "2018 – 2025")
    recibidos = []
    original = AuditorAgent.audit_and_remediate_all
    async def observar(self, cv, *args, **kwargs):
        recibidos.append(cv)
        return await original(self, cv, *args, **kwargs)
    monkeypatch.setattr(AuditorAgent, "audit_and_remediate_all", observar)
    pid = _crear(frontera)
    await _ejecutar(ruta, pid)
    assert recibidos == [CV]
    assert database.get_postulacion(pid)["status"] == "READY"
    assert len(frontera.bridge_contado.llamadas) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("ruta", RUTAS)
async def test_pipeline_normaliza_prosa_junto_a_url_literal(frontera, ruta):
    frontera.cv = CV + "\nDocumenté procesos — https://ejemplo.test/a–b"
    pid = _crear(frontera)
    await _ejecutar(ruta, pid)
    actual = database.get_postulacion(pid)
    assert actual["status"] == "READY"
    assert actual["cv_markdown"] == CV + "\nDocumenté procesos - https://ejemplo.test/a–b"
    assert actual["reporte_auditoria"]["approved"] is True
    assert len(frontera.bridge_contado.llamadas) == 1


@pytest.mark.asyncio
async def test_completar_mensaje_normaliza_fragmentos_sin_revision_ni_llamadas(frontera, monkeypatch):
    pid = _crear(frontera)
    monkeypatch.setattr(server, "_perfil_o_vacio", lambda: {"nombre": "ANA–PRUEBA"})
    mensaje = {"subject": "QA — https://ejemplo.test/a–b", "body":
               "Documenté procesos — https://ejemplo.test/a–b\nANA–PRUEBA", "is_fallback": False}
    esperado = {"subject": "QA - https://ejemplo.test/a–b", "body":
                "Documenté procesos - https://ejemplo.test/a–b\nANA–PRUEBA", "is_fallback": False}
    final = await server._completar_mensaje({}, {"id": pid}, mensaje)
    assert final == esperado
    assert await server._completar_mensaje({}, {"id": pid}, final) == esperado
    assert not revision_store.listar() and frontera.bridge_contado.llamadas == []
    assert scan_anti_ai_writing("Documenté procesos - https://ejemplo.test/a–b")["clean"]
