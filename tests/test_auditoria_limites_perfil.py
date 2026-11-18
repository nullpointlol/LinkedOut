"""Creador, 09/11: lo que no se puede corregir sin mentir (keywords o métricas que el perfil no tiene) no
bloquea el envío; los defectos corregibles sí."""
from copy import deepcopy

from core.auditor import (AuditorAgent, VEREDICTO_CON_OBSERVACIONES, aceptar_limites_del_perfil,
                          solo_limites_del_perfil)
from core.email_composer import deterministic_email, _validar_final
from tests.test_cv_mail_final_contract import CV, PITCH, KEYWORDS

KW_BRECHA = [*KEYWORDS, "Kubernetes", "Terraform", "Rust"]


def test_keywords_del_perfil_que_faltan_quedan_con_observaciones():
    estricto = AuditorAgent().audit(CV, PITCH, KW_BRECHA)
    assert estricto["approved"] is False and estricto["anti_ai"]["clean"]
    aceptado = aceptar_limites_del_perfil(deepcopy(estricto))
    assert aceptado["approved"] is True and aceptado["limites_perfil"] is True
    assert aceptado["verdict"] == aceptado["evaluacion_profesional"]["verdict"] == VEREDICTO_CON_OBSERVACIONES
    assert aceptado["hallazgos_unificados"]["requiere_accion"] is False
    # Se conserva la medición: las faltantes y las deducciones siguen a la vista
    assert aceptado["ats"]["missing_keywords"] == estricto["ats"]["missing_keywords"]
    assert aceptado["deducciones"] == estricto["deducciones"]


def test_los_defectos_corregibles_siguen_bloqueando():
    base = AuditorAgent().audit(CV, PITCH, KW_BRECHA)
    con_ia = deepcopy(base)
    con_ia["anti_ai"]["clean"] = False
    sin_seccion = deepcopy(base)
    sin_seccion["ats"]["has_all_sections"] = False
    sin_empresa = deepcopy(base)
    sin_empresa["ats"]["has_all_experiences"] = False
    con_bloqueo = deepcopy(base)
    con_bloqueo["bloqueos_finales"] = ["CV: quedó un marcador [Nombre]"]
    for reporte in (con_ia, sin_seccion, sin_empresa, con_bloqueo):
        assert not solo_limites_del_perfil(reporte)
        assert aceptar_limites_del_perfil(reporte)["approved"] is False


def test_un_dictamen_aprobado_no_cambia():
    aprobado = AuditorAgent().audit(CV, PITCH, KEYWORDS)
    assert aprobado["approved"] is True
    assert aceptar_limites_del_perfil(deepcopy(aprobado)) == aprobado


def test_el_mail_de_respaldo_nombra_la_evidencia():
    post = {"id": 1, "emails": ["rrhh@example.com"], "texto": "Buscamos Analista QA", "triage": {"puesto": "Analista QA"}}
    postulacion = {"pitch": "Me interesa el puesto y creo que puedo aportar.", "cv_markdown": CV}
    evidencia = ["Automaticé 120 casos de prueba con Cypress"]
    mail = deterministic_email(post, postulacion, {"nombre": "Ana Prueba"}, {}, evidencia=evidencia)
    assert "Por ejemplo: Automaticé 120 casos de prueba con Cypress." in mail["body"]
    # Si el pitch ya nombra la evidencia, no se agrega nada
    postulacion["pitch"] = "Automaticé pruebas con Cypress en mi último proyecto."
    mail = deterministic_email(post, postulacion, {"nombre": "Ana Prueba"}, {}, evidencia=evidencia)
    assert "Por ejemplo:" not in mail["body"]
