"""CV final con huella (creador, 15/11): el PDF que aprobó la auditoría es el mismo que se descarga, se adjunta y se
envía. Si el archivo falta, no coincide o el texto cambió después de auditarlo, no se sirve ni se envía y queda un
ticket. Candidato inventado; sin IA ni SMTP reales."""
import hashlib
from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient

import server
from core import cv_final, cv_maestro, database, radar_store
from core.mailer import GmailConfig
from core.radar import AprobacionInvalidadaError
from tests.cv_final_prueba import pdf_de_prueba
from test_radar_envio_verificado import _crear_post_y_postulacion, _fake_smtp_class

PDF = b"%PDF-1.4\n% CV auditado de Alex Rivera\n"


def _sha(datos: bytes) -> str:
    return hashlib.sha256(datos).hexdigest()


def _con_final(pid: int) -> Dict[str, Any]:
    p = database.get_postulacion(pid)
    campos = cv_final.guardar(pid, pdf_de_prueba(contenido=PDF), p["cv_markdown"], "CV - Alex Rivera - ANALISTA.pdf")
    database.update_postulacion(pid, **campos)
    return database.get_postulacion(pid)


@pytest.fixture
def tickets(monkeypatch):
    abiertos = []
    monkeypatch.setattr(server.tickets, "reportar_error", lambda *a, **k: abiertos.append(k) or 1)
    return abiertos


# ---------------------------------------------------------------- cv_final

def test_verificar_devuelve_el_mismo_archivo_auditado():
    pid, _ = _crear_post_y_postulacion()
    p = _con_final(pid)
    archivo = cv_final.verificar(p)
    assert archivo.read_bytes() == PDF and p["cv_pdf_sha256"] == _sha(PDF)
    assert archivo.name == "CV - Alex Rivera - ANALISTA.pdf"


def test_un_cv_sin_final_es_del_flujo_anterior():
    pid, _ = _crear_post_y_postulacion()
    assert cv_final.verificar(database.get_postulacion(pid)) is None


@pytest.mark.parametrize("cambio", ["texto", "archivo", "falta"])
def test_un_final_que_no_coincide_no_vale(cambio):
    pid, _ = _crear_post_y_postulacion()
    p = _con_final(pid)
    archivo = cv_final.verificar(p)
    if cambio == "texto":
        database.update_postulacion(pid, cv_markdown="# CV retocado después de auditarlo")
    elif cambio == "archivo":
        archivo.write_bytes(PDF + b"% otro")
    else:
        archivo.unlink()
    with pytest.raises(cv_final.CVFinalInvalido):
        cv_final.verificar(database.get_postulacion(pid))


# ---------------------------------------------------------------- descarga

def test_la_descarga_es_el_archivo_auditado(tickets):
    pid, _ = _crear_post_y_postulacion()
    p = _con_final(pid)
    r = TestClient(server.app).get(f"/api/ofertas/{pid}/export/pdf")
    assert r.status_code == 200 and _sha(r.content) == p["cv_pdf_sha256"]
    assert "CV - Alex Rivera - ANALISTA.pdf" in r.headers["content-disposition"] and tickets == []


@pytest.mark.parametrize("formato", ["pdf", "docx"])
def test_una_descarga_que_no_es_la_auditada_da_409_y_ticket(tickets, formato):
    pid, _ = _crear_post_y_postulacion()
    cv_final.verificar(_con_final(pid)).write_bytes(PDF + b"% alterado")
    if formato == "docx":
        database.update_postulacion(pid, cv_markdown="# CV retocado")
    r = TestClient(server.app).get(f"/api/ofertas/{pid}/export/{formato}")
    assert r.status_code == 409 and [t["tipo"] for t in tickets if t.get("tipo")] == ["cv_final_invalido"]


# ---------------------------------------------------------------- envío

def _preparar_envio(monkeypatch, smtp_state, on_login=None):
    config = GmailConfig("yo@correo-ficticio.test", "app-pass")
    monkeypatch.setattr("core.mailer.smtplib.SMTP_SSL", _fake_smtp_class(smtp_state, on_login))
    monkeypatch.setattr(server, "load_gmail_config", lambda: config)
    monkeypatch.setattr(server, "load_candidate_profile", lambda: {"nombre": "Alex Rivera"})

    def no_compilar(*a, **k):
        raise AssertionError("un CV con CV final no se vuelve a compilar")

    monkeypatch.setattr(server, "compile_markdown_to_pdf", no_compilar)


@pytest.mark.asyncio
async def test_el_adjunto_es_el_archivo_auditado(monkeypatch, tickets):
    smtp_state: Dict[str, Any] = {"events": [], "messages": [], "closed": False}
    pid, post_id = _crear_post_y_postulacion(estado="ENVIANDO", aprobado_at="2026-11-15 10:00:00")
    p = _con_final(pid)
    _preparar_envio(monkeypatch, smtp_state)
    await server._radar_send(radar_store.get_post(post_id))
    adjuntos = [a for m in smtp_state["messages"] for a in m.iter_attachments()]
    assert len(adjuntos) == 1 and _sha(adjuntos[0].get_payload(decode=True)) == p["cv_pdf_sha256"]
    assert adjuntos[0].get_filename() == "CV - Alex Rivera - ANALISTA.pdf" and tickets == []


@pytest.mark.asyncio
@pytest.mark.parametrize("cuando", ["antes", "durante_login"])
async def test_un_adjunto_que_no_es_el_auditado_no_se_envia(monkeypatch, tickets, cuando):
    smtp_state: Dict[str, Any] = {"events": [], "messages": [], "closed": False}
    pid, post_id = _crear_post_y_postulacion(estado="ENVIANDO", aprobado_at="2026-11-15 10:00:00")
    archivo = cv_final.verificar(_con_final(pid))
    if cuando == "antes":
        archivo.write_bytes(PDF + b"% alterado")
    _preparar_envio(monkeypatch, smtp_state,
                    on_login=(lambda: archivo.write_bytes(PDF + b"% alterado")) if cuando == "durante_login" else None)
    with pytest.raises(AprobacionInvalidadaError, match="no es el que aprobó la auditoría"):
        await server._radar_send(radar_store.get_post(post_id))
    assert smtp_state["messages"] == [] and [t["tipo"] for t in tickets if t.get("tipo")] == ["cv_final_invalido"]
    assert radar_store.get_post(post_id).get("sent_at") is None


# ---------------------------------------------------------------- siglas del título en el perfil

@pytest.mark.parametrize("titulo,fuentes,perfil,esperado", [
    ("TÉCNICO DE SOPORTE IT", "Técnico/a de Soporte IT\nBuscamos soporte",
     "Hace 7 años trabajo como técnico de soporte it en bancos.", "Hace 7 años trabajo como técnico de soporte IT en bancos."),
    ("IT SUPPORT SPECIALIST", "We need an IT Support Specialist. It is remote.",
     "For 7 years I have worked as an it support specialist and it was fun.",
     "For 7 years I have worked as an IT support specialist and it was fun."),
    ("TÉCNICO DE SOPORTE IT", "TÉCNICO DE SOPORTE IT\nde todo",  # un título gritado no marca siglas
     "Hace 7 años trabajo como técnico de soporte it.", "Hace 7 años trabajo como técnico de soporte it."),
])
def test_el_perfil_conserva_las_siglas_del_titulo(titulo, fuentes, perfil, esperado):
    assert cv_maestro.restaurar_siglas(perfil, titulo, cv_maestro.siglas_del_titulo(titulo, fuentes)) == esperado



# ---------------------------------------------------------------- perfil en 3ª persona (creador, 15/11)

@pytest.mark.parametrize("ingles", [False, True])
def test_el_perfil_va_en_tercera_persona_y_la_revision_lo_sabe(ingles):
    from core import pautas_cv
    areas = [{"area": "análisis", "anios": "5"}]
    texto = pautas_cv.bloque_perfil("ANALISTA", areas, ["SQL", "Jira"], ingles)
    from core.prompts_evaluacion import (PERFIL_FORMULAS_PROHIBIDAS, PERFIL_REGLA_ANIOS, PERFIL_REGLA_PERSONA,
                                         REVISION_SENALES_IA_PROMPT)
    revision = REVISION_SENALES_IA_PROMPT.format(cv="X")
    # Una sola lista para el generador y para la revisión con IA: no se contradicen
    assert all(f"«{f}»" in texto and f"«{f}»" in revision for f in PERFIL_FORMULAS_PROHIBIDAS)
    assert PERFIL_REGLA_ANIOS in texto and PERFIL_REGLA_ANIOS in revision
    assert PERFIL_REGLA_PERSONA in texto and PERFIL_REGLA_PERSONA in revision
    # «Profesional con…» y «<Rol> con N años…» son la forma pedida: ya no son fórmulas prohibidas
    assert not any(f.startswith(("Profesional con", "<Rol> con")) for f in PERFIL_FORMULAS_PROHIBIDAS)
    # Sin años (CV general), ni la regla de años ni un ejemplo con años
    sin = pautas_cv.bloque_perfil("", [], ["SQL", "Jira"], ingles)
    assert PERFIL_REGLA_ANIOS not in sin and ("Professional with" if ingles else "Profesional con") in sin


# ---------------------------------------------------------------- pautas y auditoría no se contradicen (creador, 15/11)

@pytest.mark.parametrize("idioma", ["es", "en"])
def test_las_pautas_traen_todos_los_controles_de_la_revision_con_ia(idioma):
    from core import pautas_cv
    from core.prompts_evaluacion import PATRONES_SENALES_IA
    texto = pautas_cv.pautas(["SQL"], idioma, [], titulo="ANALISTA", areas=[], fuertes=["SQL", "Jira"])
    assert all(p in texto for p in PATRONES_SENALES_IA)


@pytest.mark.parametrize("ingles", [False, True])
@pytest.mark.parametrize("areas", [[], [{"area": "análisis", "anios": "5"}]])
def test_el_ejemplo_de_perfil_de_las_pautas_pasa_la_auditoria(ingles, areas):
    import re
    from core import pautas_cv
    from core.auditor import scan_anti_ai_writing
    bloque = pautas_cv.bloque_perfil("ANALISTA", areas, ["SQL", "Postman"], ingles)
    ejemplo = re.sub(r"\s+", " ", re.search(r"no copies sus datos\): «(.+?)»", bloque, re.S).group(1))
    titulo = "## SUMMARY" if ingles else "## PERFIL"
    assert scan_anti_ai_writing(f"# ANA PRUEBA\n\n{titulo}\n{ejemplo}\n")["detected_words"] == []
    # y el control de 3ª persona del flujo lo acepta
    regex = cv_maestro._RE_1A_PERSONA_EN if ingles else cv_maestro._RE_1A_PERSONA_ES
    assert not regex.search(ejemplo)


def test_la_correccion_recibe_la_reescritura_y_prevalece_la_auditoria():
    from types import SimpleNamespace
    b = SimpleNamespace(perfil=SimpleNamespace(texto="Hago soporte IT.", id="P1"), editables=lambda: [])
    reporte = {"approved": False, "anti_ai": {"detected_words": [], "senales_llm": [
        {"frase": "Hago soporte IT.", "patron": "apertura formulaica", "reescritura": "Resuelvo incidentes de soporte IT."}]}}
    hallazgo = cv_maestro.ubicar(b, reporte)[0]
    assert "la auditoría propone: «Resuelvo incidentes de soporte IT.»" in hallazgo.motivo
    import inspect
    assert "PREVALECE LA AUDITORÍA" in inspect.getsource(cv_maestro.pedido_correccion)


def test_guardar_sin_el_pdf_auditado_no_vale(tmp_path):
    with pytest.raises(cv_final.CVFinalInvalido, match="No existe el PDF auditado"):
        cv_final.guardar(1, tmp_path / "no-existe.pdf", "# CV", "CV.pdf")


def test_un_final_sin_huellas_no_vale():
    with pytest.raises(cv_final.CVFinalInvalido, match="Faltan datos"):
        cv_final.verificar({"cv_pdf": "finales/1/CV.pdf", "cv_markdown": "# CV"})
