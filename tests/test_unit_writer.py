"""Unitarios de `core/writer.py`: pitches multicanal, saneado y poda del CV, viñetas con etiqueta,
parseo de experiencia sin roles y ramas del redactor con la IA simulada (nunca se llama a `agy`)."""
import logging
from unittest.mock import AsyncMock

import pytest

from core import writer
from core.cv_guardas import COMPLETO, UNA_PAGINA
from core.titulo_cv import DecisionTitulo
from core.writer import (
    WriterAgent,
    _format_bullet_with_tag,
    _parse_experience_sections,
    ensure_cv_length,
    sanitize_cv_formatting,
    synthesize_multichannel_pitches,
)

PERSONALES = "Sam Ortega\nPuesto: Analista Técnico Funcional\nVilla Test | +54 9 11 0000-0000 | sam@example.test\n"
EXPERIENCIA = """## Acme
### Analista Técnico Funcional | 2019 - 2026
• Relevé requerimientos funcionales con el negocio.
• Automaticé reportes con Python y SQL.
"""
SIN_TITULO = DecisionTitulo(caso=0, clave=None, titulo="", roles_reales=(), puesto_oferta="")


# --------------------------------------------------------------------------- pitches multicanal

def test_pitches_toman_el_perfil_del_quinto_argumento_posicional():
    """Si el quinto argumento posicional es un dict, se usa como perfil (firma heredada)."""
    res = synthesize_multichannel_pitches("", "Analista", "Acme", None, {"nombre": "Sam Ortega"})
    assert res["inmail"].endswith("Sam Ortega")
    assert "Asunto: Postulación Analista - Sam Ortega" in res["email"]


def test_pitches_sin_perfil_y_perfil_ilegible_firman_como_candidato(monkeypatch):
    """Sin perfil y con la carga del perfil fallando, la firma es «Candidato»."""
    def falla():
        raise OSError("sin disco")

    monkeypatch.setattr(writer, "load_candidate_profile", falla)
    res = synthesize_multichannel_pitches("", "Analista", "Acme")
    assert res["inmail"].endswith("Saludos! Candidato")
    assert res["email"].rstrip().endswith("Candidato")


def test_pitches_nombre_en_blanco_vuelve_a_candidato():
    """Un nombre de solo espacios no deja la firma vacía."""
    res = synthesize_multichannel_pitches("", "Analista", "Acme", profile={"nombre": "   "})
    assert res["inmail"].endswith("Saludos! Candidato")


def test_inmail_nunca_supera_300_caracteres_aun_con_nombre_enorme():
    """Con un nombre muy largo el InMail se recorta y sigue respetando el máximo de 300."""
    for largo in [200, 400]:
        nombre = "Sam" + "x" * largo
        res = synthesize_multichannel_pitches("", "Analista " * 10, "Acme " * 10, profile={"nombre": nombre})
        assert len(res["inmail"]) <= 300


def test_email_con_asunto_propio_suma_la_firma_si_falta_el_nombre():
    """Un pitch que ya trae «Asunto:» se respeta y se le agrega la firma con contacto."""
    perfil = {"nombre": "Sam Ortega", "contacto": "sam@example.test"}
    res = synthesize_multichannel_pitches("Asunto: Hola\n\nCuerpo del mail.", "Analista", "Acme", profile=perfil)
    assert res["email"].startswith("Asunto: Hola")
    assert res["email"].endswith("Saludos cordiales,\nSam Ortega\nsam@example.test")


def test_email_con_asunto_y_nombre_no_duplica_firma():
    """Si el pitch con asunto ya nombra al candidato, no se agrega otra firma."""
    pitch = "Asunto: Hola\n\nSoy Sam Ortega y me interesa."
    res = synthesize_multichannel_pitches(pitch, "Analista", "Acme", profile={"nombre": "Sam Ortega"})
    assert res["email"] == pitch


def test_web_ats_saltea_saludos_y_no_repite_postulo():
    """El texto web descarta saludos y firmas; si ya empieza con «Postulo» no se antepone otra intro."""
    pitch = ("Hola equipo de Acme, ¿cómo están?\n\n\n\n"
             "Postulo a la vacante porque automaticé reportes de calidad durante años.\n\n"
             "Saludos cordiales, Sam Ortega")
    res = synthesize_multichannel_pitches(pitch, "Analista", "Acme", profile={"nombre": "Sam Ortega"})
    assert res["web_ats"] == "Postulo a la vacante porque automaticé reportes de calidad durante años."


# --------------------------------------------------------------------------- saneado y poda

def test_saneado_y_poda_con_cv_vacio_devuelven_vacio():
    """Ambas funciones devuelven cadena vacía para un CV vacío."""
    assert sanitize_cv_formatting("") == ""
    assert ensure_cv_length("") == ""


def test_poda_rescata_el_nombre_que_quedo_dentro_de_una_seccion(monkeypatch):
    """Un «# Nombre» escrito dentro de una sección vuelve al encabezado, antes del PERFIL."""
    monkeypatch.setattr(writer, "load_candidate_profile", lambda: {"educacion": [], "idiomas": []})
    cv = ("## PERFIL\nTexto de perfil.\n# Sam Ortega\nsam@example.test\n"
          "## EXPERIENCIA\n### Analista | Acme | 2019 - 2026\n• Hice algo.")
    res = ensure_cv_length(cv)
    assert res.startswith("# Sam Ortega\nsam@example.test")
    assert res.index("# Sam Ortega") < res.index("## PERFIL")


def test_poda_conserva_intro_continuaciones_y_otras_secciones(monkeypatch):
    """Intro de experiencia, líneas que siguen a una viñeta y secciones no canónicas se conservan;
    sin educación ni idiomas en el perfil se usan los textos por defecto."""
    def falla():
        raise ValueError("perfil roto")

    monkeypatch.setattr(writer, "load_candidate_profile", falla)
    cv = ("# Sam Ortega\n\n## PERFIL\n\nAnalista.\n\n"
          "## EXPERIENCIA\nIntro de la trayectoria.\n### Analista | Acme | 2019 - 2026\n"
          "• Primera viñeta\ncontinuación de la viñeta\n"
          "## PROYECTOS PERSONALES\n\nBot de reportes.\n")
    res = ensure_cv_length(cv)
    assert "## EXPERIENCIA\n\nIntro de la trayectoria." in res
    assert "• Primera viñeta\ncontinuación de la viñeta" in res
    assert res.rstrip().endswith("## PROYECTOS PERSONALES\nBot de reportes.")
    assert "• Formación Profesional Universitaria / Técnica." in res
    assert "• Español: Nativo." in res and "• Inglés: Técnico / Profesional (B2/C1)." in res


def test_poda_experiencia_sin_empresas_conserva_las_lineas_sueltas(monkeypatch):
    """Una EXPERIENCIA sin «###» se ensambla con sus líneas tal cual."""
    monkeypatch.setattr(writer, "load_candidate_profile", lambda: {})
    cv = "# Sam Ortega\n## EXPERIENCIA\n• Viñeta sin empresa\n## EDUCACIÓN\n• Tecnicatura"
    res = ensure_cv_length(cv)
    assert "## EXPERIENCIA\n\n• Viñeta sin empresa" in res
    assert "## EDUCACIÓN\n• Tecnicatura" in res


def test_poda_recorta_a_tres_vinetas_cuando_cuatro_no_alcanzan(monkeypatch):
    """Si con 4 viñetas por empresa sigue largo, se baja a 3 y entra en el máximo."""
    monkeypatch.setattr(writer, "load_candidate_profile", lambda: {})
    def cv_con(n):
        vinetas = "\n".join(f"• Viñeta número {i} " + "x" * 80 for i in range(n))
        return f"# Sam Ortega\n## EXPERIENCIA\n### Analista | Acme | 2019 - 2026\n{vinetas}"

    largo_con_tres = len(ensure_cv_length(cv_con(3), max_chars=100_000))
    res = ensure_cv_length(cv_con(6), max_chars=largo_con_tres)
    assert res.count("• Viñeta número") == 3
    assert len(res) <= largo_con_tres


# --------------------------------------------------------------------------- viñetas con etiqueta

def test_vineta_con_etiqueta_propia_la_conserva_y_corrige_ize():
    """Una viñeta «**Etiqueta:**» conserva la etiqueta; «-izé» pasa a «-icé» y se cierra con punto."""
    res = _format_bullet_with_tag("• **Calidad:** realizé pruebas")
    assert res == "• **Calidad:** Realicé pruebas."


def test_vineta_sin_etiqueta_infiere_la_categoria():
    """Sin etiqueta se infiere una por palabras clave (o la etiqueta por defecto)."""
    for texto, etiqueta in [
        ("Atendí soporte de segundo nivel", "Observabilidad & Soporte:"),
        ("Coordiné con stakeholders del negocio", "Gestión Funcional & Procesos:"),
        ("Hice tareas generales", "Optimización Técnica:"),
    ]:
        assert _format_bullet_with_tag(f"- {texto}.").startswith(f"• **{etiqueta}** ")


def test_vineta_etiqueta_por_defecto_personalizada():
    """Sin palabras clave, la viñeta usa la etiqueta por defecto que se pase, con «:»."""
    assert _format_bullet_with_tag("Hice tareas.", default_tag="General").startswith("• **General:** ")


# --------------------------------------------------------------------------- experiencia sin roles

def test_parse_experiencia_sin_roles_por_empresa():
    """Sin «### Rol | años», cada «## Empresa» con viñetas es una sección; las reservadas se saltean."""
    texto = ("## Acme | 2019 - 2026\n\n### Analista | 2019 - 2026\n• Viñeta A\n"
             "## PERFIL\n• No es empresa\n"
             "## Korva\n### Korva Operador\n• Viñeta B\n"
             "## Velora\n### Soporte\n• Viñeta C\n"
             "## Vacía\n")
    secciones = _parse_experience_sections(texto)
    assert [s["company"] for s in secciones] == ["Acme", "Korva", "Velora"]
    assert secciones[0]["header"] == "### Analista | Acme | 2019 - 2026"
    assert secciones[1]["header"] == "### Korva Operador"
    assert secciones[2]["header"] == "### Soporte | Velora"
    assert secciones[0]["bullets"] == ["• Viñeta A", "• No es empresa"]


# --------------------------------------------------------------------------- redactor con IA simulada

def _agente(respuesta, monkeypatch):
    monkeypatch.setattr(writer, "decidir_titulo", AsyncMock(return_value=SIN_TITULO))
    bridge = AsyncMock()
    bridge.run_prompt.return_value = respuesta
    return WriterAgent(bridge=bridge)


def _analisis(formato=COMPLETO):
    return {"puesto": "Analista", "empresa": "Acme", "formato_cv": formato,
            "requisitos": [{"nombre": "SQL", "estado": "MATCH_DIRECTO", "approved": True}]}


CV_OK = ("# Sam Ortega\nANALISTA\n\n## PERFIL\nAnalista con experiencia.\n\n## EXPERIENCIA\n"
         "### Analista Técnico Funcional | Acme | 2019 - 2026\n• Relevé requerimientos.\n\n"
         "## HABILIDADES TÉCNICAS\n• **Datos:** SQL.\n\n## EDUCACIÓN\n• Tecnicatura.\n\n## IDIOMAS\n• Español: Nativo.")


@pytest.mark.asyncio
@pytest.mark.parametrize("perfil_extra, firma", [
    ({"nombre": "Sam Ortega"}, "Sam Ortega"),
    ({}, "Sam Ortega"),
])
async def test_inmail_largo_del_modelo_se_recorta_con_firma(monkeypatch, perfil_extra, firma):
    """Un InMail de más de 300 caracteres se corta en palabra y termina con la firma del candidato."""
    respuesta = {"cv_markdown": CV_OK, "pitch": "Pitch base.",
                 "pitches_multicanal": {"inmail": "palabra " * 60, "email": "e", "web_ats": "w"}}
    agente = _agente(respuesta, monkeypatch)
    perfil = {"experiencia": EXPERIENCIA, "personales": PERSONALES, **perfil_extra}
    res = await agente.generate_cv_and_pitch(_analisis(), profile=perfil)
    inmail = res["pitches_multicanal"]["inmail"]
    assert len(inmail) <= 300
    assert inmail.endswith(f"... Saludos! {firma}")
    assert res["is_fallback"] is False


@pytest.mark.asyncio
async def test_cv_de_una_pagina_largo_se_poda_a_5000(monkeypatch):
    """En formato de 1 página, un CV del modelo de más de 5000 caracteres se poda."""
    vinetas = "\n".join(f"• Relevé requerimiento {i} " + "detalle " * 20 for i in range(40))
    cv = CV_OK.replace("• Relevé requerimientos.", vinetas)
    assert len(cv) > 5000
    agente = _agente({"cv_markdown": cv, "pitch": "p",
                      "pitches_multicanal": {"inmail": "i", "email": "e", "web_ats": "w"}}, monkeypatch)
    res = await agente.generate_cv_and_pitch(_analisis(UNA_PAGINA),
                                             profile={"experiencia": EXPERIENCIA, "personales": PERSONALES})
    assert len(res["cv_markdown"]) <= 5000
    assert res["formato_cv"] == UNA_PAGINA


@pytest.mark.asyncio
async def test_correcciones_de_perfil_quedan_en_el_log(monkeypatch, caplog):
    """Cuando las guardas corrigen el perfil sin IA, el redactor lo deja registrado."""
    monkeypatch.setattr(writer, "corregir_perfil", lambda cv: (cv, ["años quitados"]))
    agente = _agente({"cv_markdown": CV_OK, "pitch": "p",
                      "pitches_multicanal": {"inmail": "i", "email": "e", "web_ats": "w"}}, monkeypatch)
    with caplog.at_level(logging.INFO, logger="core.writer"):
        await agente.generate_cv_and_pitch(_analisis(), profile={"experiencia": EXPERIENCIA, "personales": PERSONALES})
    assert "Perfil corregido sin IA: años quitados" in caplog.text
