"""«Corregir»: sobre el CV actual, solo las líneas que marcó la auditoría; se vuelve a auditar y queda el CV final
con su huella. Sin regenerar de cero ni inventar datos; el mail no se toca.

Perfil, oferta y CV ficticios; sin IA real ni datos reales.
"""
import asyncio
import itertools
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import server
from core import correccion_auditoria as corr
from core import cv_final, database, radar_store
from core.radar import RadarLoop

_URN = itertools.count(1)

FRASE = "para automatización inteligente."
LINEA_MARCADA = f"Relevé necesidades de clientes junto a negocio y desarrollo {FRASE}"
CV = f"""# ANA PRUEBA
ANALISTA FUNCIONAL
Ciudad Test | ana@correo-ficticio.test

## PERFIL
Analista funcional con experiencia en relevamiento.

## EXPERIENCIA
### Analista Funcional | Acme (2020 - 2024)
• {LINEA_MARCADA}
• Documenté procesos con BPMN.

## HABILIDADES TÉCNICAS
• **Herramientas:** Jira, SQL

## EDUCACIÓN
• Licenciatura en Sistemas

## IDIOMAS
• Inglés B2
"""


def reporte(reescritura="para su posterior automatización.", **extra):
    return {"approved": False, "global_score": 95.5, "origen": "cv_maestro", "seleccion": "ia",
            "ats": {"found_keywords": ["Jira"], "missing_keywords": [], "found_experiences": ["Acme"],
                    "missing_experiences": [], "keyword_coverage": 1.0, "ats_score": 100.0},
            "anti_ai": {"senales_llm": [{"frase": FRASE, "patron": "frase que suena a plantilla",
                                         "reescritura": reescritura}]},
            "deducciones": [f"-15 pts en Redacción: («{FRASE}»)."], **extra}


async def sin_ia(prompt):
    raise AssertionError("no se llama a la IA cuando alcanza la reescritura de la auditoría")


def lineas_distintas(a: str, b: str):
    return [(x, y) for x, y in zip(a.splitlines(), b.splitlines()) if x != y]


# ----------------------------------------------------------------- lectura del CV

def test_leer_y_rearmar_sin_cambios_devuelve_el_mismo_texto():
    b, ubicacion = corr.borrador_de(CV)
    assert b.perfil.texto == "Analista funcional con experiencia en relevamiento."
    assert [l.id for l in b.editables()] == ["P1", "V1", "V2", "S1"]
    assert b.experiencia[0].empresa == "Acme"
    assert corr.reconstruir(CV, b, ubicacion) == CV


def test_la_reescritura_de_la_auditoria_se_aplica_solo_en_su_linea():
    nuevo, cambiadas = asyncio.run(corr.corregir(CV, reporte(), ["Jira"], sin_ia))
    assert cambiadas == {"V1"}
    assert lineas_distintas(CV, nuevo) == [
        (f"• {LINEA_MARCADA}", "• Relevé necesidades de clientes junto a negocio y desarrollo para su posterior "
                               "automatización.")]
    assert len(nuevo.splitlines()) == len(CV.splitlines()) and nuevo.endswith("\n")


def test_la_ia_corrige_solo_las_lineas_marcadas():
    pedidos = []

    async def ia(prompt):
        pedidos.append(prompt)
        # La IA cambia también el perfil y otra viñeta: no se toman
        return {"perfil": "Perfil inventado.", "lineas": [
            {"id": "V1", "texto": "Relevé necesidades de clientes junto a negocio y desarrollo."},
            {"id": "V2", "texto": "Documenté 40 procesos con BPMN."}]}

    nuevo, cambiadas = asyncio.run(corr.corregir(CV, reporte(reescritura=""), ["Jira"], ia))
    assert len(pedidos) == 1 and "Corregí SOLO lo que falló" in pedidos[0] and FRASE in pedidos[0]
    assert cambiadas == {"V1"}
    assert lineas_distintas(CV, nuevo) == [
        (f"• {LINEA_MARCADA}", "• Relevé necesidades de clientes junto a negocio y desarrollo.")]


def test_un_hallazgo_general_no_habilita_a_tocar_ninguna_linea():
    general = {"approved": False, "hallazgos_unificados": {"lista_observaciones": ["CV no aprobado tras la corrección"]}}
    with pytest.raises(corr.SinCorreccion):
        asyncio.run(corr.corregir(CV, general, [], sin_ia))


def test_senales_de_la_nueva_revision_solo_en_las_lineas_marcadas():
    senales = [{"frase": "Documenté procesos con BPMN.", "reescritura": ""},
               {"frase": "junto a negocio y desarrollo", "reescritura": ""}]
    assert corr.senales_de_lineas(CV, senales, {"V1"}) == [senales[1]]


# ----------------------------------------------------------------- API

@pytest.fixture
def radar(monkeypatch):
    stub = AsyncMock(spec=RadarLoop)
    monkeypatch.setattr(server, "radar_loop", stub)
    return stub


@pytest.fixture
def client(radar):
    return TestClient(server.app)


class AuditorFalso:
    """Vuelve a auditar sin IA: aprueba salvo que el CV conserve la frase marcada."""

    def __init__(self):
        self.auditados = []

    async def revisar_senales_ia(self, md):
        return [{"frase": "Documenté procesos con BPMN.", "patron": "otra cosa", "reescritura": ""}]

    def audit(self, md, pitch, keywords, expected_companies=None, senales_llm=None):
        self.auditados.append({"md": md, "keywords": keywords, "empresas": expected_companies, "senales": senales_llm})
        ok = FRASE not in md and not senales_llm
        return {"approved": ok, "global_score": 99.0 if ok else 80.0, "ats": {"found_keywords": keywords,
                "missing_keywords": [], "found_experiences": expected_companies, "missing_experiences": []}}


def pdf_una_pagina(md, target_role=None, output_filename=None, *a, **kw):
    from pypdf import PdfWriter
    ruta = Path(tempfile.mkdtemp(prefix="cv_corr_")) / (output_filename or "cv.pdf")
    w = PdfWriter()
    w.add_blank_page(width=595, height=842)
    with open(ruta, "wb") as f:
        w.write(f)
    return str(ruta)


@pytest.fixture
def entorno(monkeypatch):
    auditor = AuditorFalso()
    monkeypatch.setattr(server, "_auditor_correccion", lambda: auditor)
    monkeypatch.setattr(server, "compile_markdown_to_pdf", pdf_una_pagina)
    # El dictamen final sin IA se prueba aparte (y lo cambia otra sesión): acá se lo deja pasar tal cual
    monkeypatch.setattr(server, "_dictamen_final", lambda postulacion, cv, pitch, pitches, rep, *a, **kw: (rep, []))
    return auditor


def crear(rep, status="ERROR", estado="LISTO", cv=CV):
    n = next(_URN)
    pid = database.create_postulacion("Analista", "Acme", "Buscamos analista.", status=status, origen="linkedin")
    database.update_postulacion(pid, cv_markdown=cv, reporte_auditoria=rep, pitch="")
    post_id = radar_store.insert_post_if_new({
        "urn": f"urn:li:activity:8{n}", "url": f"https://www.linkedin.com/feed/update/urn:li:activity:8{n}/",
        "autor": "Acme", "cargo_autor": "HR", "texto": "Buscamos analista.", "fecha_rel": "1 h",
        "emails": ["rrhh@empresa.test"]}, query="q")
    radar_store.update_post(post_id, estado=estado, postulacion_id=pid, email_to="rrhh@empresa.test",
                            email_subject="Analista", email_body="Hola, adjunto mi CV.",
                            error="Revisá el contenido antes de enviar: la auditoría del CV no aprobó el contenido")
    return post_id, pid


def url(post_id):
    return f"/api/radar/posts/{post_id}/corregir-auditoria"


def test_corregir_desde_error_reaudita_y_deja_el_cv_final_listo(client, radar, entorno):
    post_id, pid = crear(reporte())
    antes = radar_store.get_post(post_id)
    r = client.post(url(post_id))
    assert r.status_code == 200, r.json()
    assert r.json()["aprobado"] is True and r.json()["lineas"] == ["V1"]
    postulacion = database.get_postulacion(pid)
    assert lineas_distintas(CV, postulacion["cv_markdown"]) == [
        (f"• {LINEA_MARCADA}", "• Relevé necesidades de clientes junto a negocio y desarrollo para su posterior "
                               "automatización.")]
    assert postulacion["status"] == "READY"
    informe = postulacion["reporte_auditoria"]
    assert informe["approved"] is True and informe["origen"] == "cv_maestro" and informe["seleccion"] == "ia"
    assert informe["correccion_auditoria"] == ["V1"]
    # Se volvió a auditar con los criterios del reporte, sin las señales de líneas que la auditoría ya aprobó
    auditado = entorno.auditados[-1]
    assert auditado["keywords"] == ["Jira"] and auditado["empresas"] == ["Acme"] and auditado["senales"] == []
    # El CV final es el PDF de este texto, con su huella
    assert cv_final.verificar(postulacion) is not None
    post = radar_store.get_post(post_id)
    assert post["error"] is None and post["aprobado_at"] is None and post["updated_at"] != antes["updated_at"]
    assert (post["email_subject"], post["email_body"]) == (antes["email_subject"], antes["email_body"])
    radar.notify_post.assert_awaited_with(post_id)


def test_corregir_que_sigue_sin_aprobar_conserva_el_dictamen_y_el_cv_final(client, entorno, monkeypatch):
    post_id, pid = crear(reporte())
    entorno.revisar_senales_ia = AsyncMock(return_value=[{"frase": "junto a negocio", "reescritura": ""}])
    r = client.post(url(post_id))
    assert r.status_code == 200 and r.json()["aprobado"] is False
    postulacion = database.get_postulacion(pid)
    assert postulacion["reporte_auditoria"]["approved"] is False and postulacion["status"] == "READY"
    assert cv_final.verificar(postulacion) is not None  # «Enviar igual» adjunta este CV


def test_corregir_rechaza_si_la_auditoria_aprobo_o_no_hay_cv(client, entorno):
    aprobado, _ = crear({"approved": True})
    assert client.post(url(aprobado)).status_code == 409
    sin_cv, _ = crear(reporte(), cv="")
    assert client.post(url(sin_cv)).status_code == 409
    enviado, _ = crear(reporte(), estado="ENVIADO")
    assert client.post(url(enviado)).status_code == 409
    assert client.post(url(999999)).status_code == 404


def test_corregir_sin_lineas_marcadas_lo_dice_y_no_toca_el_cv(client, entorno):
    post_id, pid = crear({"approved": False, "hallazgos_unificados": {"lista_observaciones": ["general"]}})
    r = client.post(url(post_id))
    assert r.status_code == 409 and "no marcó líneas" in r.json()["detail"]
    assert database.get_postulacion(pid)["cv_markdown"] == CV


def test_corregir_solo_desde_la_pc(client, entorno):
    post_id, pid = crear(reporte())
    r = client.post(url(post_id), headers={"sec-fetch-site": "cross-site"})
    assert r.status_code == 403
    assert database.get_postulacion(pid)["cv_markdown"] == CV


def test_corregir_no_pisa_un_cv_editado_mientras_tanto(client, entorno):
    post_id, pid = crear(reporte())

    async def editar_mientras_audita(md):
        database.update_postulacion(pid, cv_markdown=CV + "\nEditado a mano.")
        return []

    entorno.revisar_senales_ia = editar_mientras_audita
    r = client.post(url(post_id))
    assert r.status_code == 409
    assert database.get_postulacion(pid)["cv_markdown"] == CV + "\nEditado a mano."
