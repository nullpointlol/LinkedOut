"""Postulaciones con la auditoría del CV no aprobada: la lista lo dice, se corrigen solo las líneas marcadas y
se envían igual solo con el permiso explícito, que no vale para otros motivos.

Perfil y oferta ficticios; sin IA real, sin Gmail real ni datos reales.
"""
import itertools
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import server
from core import auditoria_envio, database, radar_store
from core.mailer import GmailConfig
from core.radar import RadarLoop

_URN = itertools.count(1)

FRASE = "para automatización inteligente."
REPORTE_NO_APROBADO = {
    "approved": False, "global_score": 95.5, "verdict": "REQUIERE REMEDIACIÓN",
    "deducciones": ["-15 pts en Redacción: términos no naturales o faltas ortográficas "
                    f"(«{FRASE}» (frase que suena a plantilla))."],
    "hallazgos_unificados": {
        "lista_observaciones": [f"Remover clichés y muletillas de IA detectadas: «{FRASE}» (frase que suena a plantilla)."],
        "requiere_accion": True},
    "anti_ai": {"senales_llm": [{"frase": FRASE, "patron": "frase que suena a plantilla",
                                 "reescritura": "para su posterior automatización."}]},
}


@pytest.fixture
def radar(monkeypatch):
    stub = AsyncMock(spec=RadarLoop)
    monkeypatch.setattr(server, "radar_loop", stub)
    return stub


@pytest.fixture
def client(radar, monkeypatch):
    monkeypatch.setattr("core.radar_api.load_gmail_config", lambda: GmailConfig("yo@correo-ficticio.test", "x"))
    return TestClient(server.app)


def crear(reporte=None, estado="LISTO", status="READY", cv="# ANA PRUEBA\n\n## PERFIL\nAnalista.\n") -> tuple:
    n = next(_URN)
    pid = database.create_postulacion("Analista", "Acme", "Buscamos analista.", status=status, origen="linkedin")
    database.update_postulacion(pid, cv_markdown=cv, reporte_auditoria=reporte if reporte is not None
                                else {"approved": True, "global_score": 99})
    post_id = radar_store.insert_post_if_new({
        "urn": f"urn:li:activity:9{n}", "url": f"https://www.linkedin.com/feed/update/urn:li:activity:9{n}/",
        "autor": "Acme", "cargo_autor": "HR", "texto": "Buscamos analista. CV a rrhh@empresa.test",
        "fecha_rel": "1 h", "emails": ["rrhh@empresa.test"]}, query="q")
    radar_store.update_post(post_id, estado=estado, postulacion_id=pid, email_to="rrhh@empresa.test",
                            email_subject="Analista", email_body="Hola, adjunto mi CV.",
                            triage={"es_busqueda_laboral": True, "puesto": "Analista", "empresa": "Acme"})
    return post_id, pid


# ----------------------------------------------------------------- motivos cortos

def test_motivos_cortos_dicen_categoria_y_frase_citada():
    assert auditoria_envio.motivos_cortos(REPORTE_NO_APROBADO) == [f"Redacción: «{FRASE}»"]


def test_motivos_cortos_son_como_mucho_tres_y_cortos():
    reporte = {"approved": False, "hallazgos_unificados": {"lista_observaciones": [
        "x" * 300, "Falta la keyword «SQL»", "CV: raya en prosa", "Otro motivo"]}}
    motivos = auditoria_envio.motivos_cortos(reporte)
    assert len(motivos) == auditoria_envio.MAX_MOTIVOS == 3
    assert all(len(m) <= auditoria_envio.LARGO_MOTIVO for m in motivos)
    assert motivos[0].endswith("…") and motivos[1] == "Falta la keyword «SQL»"


def test_motivos_de_un_cv_no_aprobado_sin_ids_de_linea():
    reporte = {"approved": False, "hallazgos_unificados": {"lista_observaciones": [
        "CV no aprobado tras la corrección: V3: «frase» (patrón); general: falta la keyword «SQL»"]}}
    assert auditoria_envio.motivos_cortos(reporte) == ["«frase» (patrón)", "falta la keyword «SQL»"]


def test_sin_dictamen_o_aprobado_no_hay_motivos():
    assert auditoria_envio.no_aprobada({"approved": True}) is False
    assert auditoria_envio.no_aprobada({}) is False and auditoria_envio.no_aprobada(None) is False
    assert auditoria_envio.no_aprobada({"approved": False}) is True
    assert auditoria_envio.motivos_cortos({"approved": False}) == ["La auditoría no aprobó el CV"]


# ----------------------------------------------------------------- lista y detalle

def test_la_lista_expone_auditoria_ok_y_los_motivos_solo_si_no_aprobo(client):
    malo, _ = crear(REPORTE_NO_APROBADO)
    bueno, _ = crear()
    items = {p["id"]: p for p in client.get("/api/radar/posts?estado=LISTO").json()}
    assert items[malo]["auditoria_ok"] is False
    assert items[malo]["auditoria_motivos"] == [f"Redacción: «{FRASE}»"]
    assert items[bueno]["auditoria_ok"] is True and "auditoria_motivos" not in items[bueno]


def test_el_detalle_expone_lo_mismo_que_la_lista(client):
    malo, _ = crear(REPORTE_NO_APROBADO)
    detalle = client.get(f"/api/radar/posts/{malo}").json()
    assert detalle["auditoria_ok"] is False and detalle["auditoria_motivos"] == [f"Redacción: «{FRASE}»"]
    # Aprobado, el detalle conserva su forma: sin los campos
    bueno, _ = crear()
    assert "auditoria_ok" not in client.get(f"/api/radar/posts/{bueno}").json()


def test_un_error_por_cv_no_aprobado_tambien_dice_revisar(client):
    no_aprobado = {"approved": False, "hallazgos_unificados": {"lista_observaciones": [
        "CV no aprobado tras la corrección: V2: «frase» (patrón)"], "requiere_accion": True}}
    post_id, _ = crear(no_aprobado, estado="ERROR", status="ERROR")
    item = next(p for p in client.get("/api/radar/posts").json() if p["id"] == post_id)
    assert item["auditoria_ok"] is False and item["auditoria_motivos"] == ["«frase» (patrón)"]
    detalle = client.get(f"/api/radar/posts/{post_id}").json()
    assert detalle["auditoria_ok"] is False and detalle["auditoria_motivos"] == ["«frase» (patrón)"]


def test_por_revisar_cuenta_las_listas_con_auditoria_no_aprobada(client):
    antes = client.get("/api/radar/status").json()["revision_pendientes"]
    malo, _ = crear(REPORTE_NO_APROBADO)
    crear(REPORTE_NO_APROBADO, estado="ENVIADO")  # ya salió: no hay nada que revisar
    crear()
    assert client.get("/api/radar/status").json()["revision_pendientes"] == antes + 1
    revision = client.get("/api/radar/revision").json()
    assert revision["pendientes"] == antes + 1
    item = next(i for i in revision["items"] if i.get("tipo") == "auditoria")
    assert item["post_id"] == malo and item["frase"] == f"Redacción: «{FRASE}»"
    assert (item["puesto"], item["empresa"]) == ("Analista", "Acme")


# ----------------------------------------------------------------- enviar igual

MOTIVO_AUDITORIA = "la auditoría del CV no aprobó el contenido"


def aprobar(client, post_id, **extra):
    version = radar_store.get_post(post_id)["updated_at"]
    return client.post(f"/api/radar/posts/{post_id}/approve", json={"updated_at": version, **extra})


def test_sin_permiso_la_auditoria_no_aprobada_bloquea_el_envio(client, radar):
    post_id, _ = crear(REPORTE_NO_APROBADO)
    r = aprobar(client, post_id)
    assert r.status_code == 409 and MOTIVO_AUDITORIA in r.json()["detail"]
    post = radar_store.get_post(post_id)
    assert post["aprobado_at"] is None and post["forzado_auditoria_at"] is None
    radar.enqueue_send.assert_not_awaited()


def test_con_permiso_se_aprueba_y_queda_registrado(client, radar, caplog):
    post_id, _ = crear(REPORTE_NO_APROBADO)
    with caplog.at_level("WARNING"):
        r = aprobar(client, post_id, forzar_auditoria=True)
    assert r.status_code == 200, r.json()
    post = radar_store.get_post(post_id)
    assert post["aprobado_at"] and post["forzado_auditoria_at"] == post["aprobado_at"]
    radar.enqueue_send.assert_awaited_with(post_id)
    assert any(f"post {post_id}" in m and "sin la auditoría" in m for m in caplog.messages)


def test_el_permiso_no_vale_para_otros_motivos(client, radar):
    post_id, _ = crear(REPORTE_NO_APROBADO)
    radar_store.update_post(post_id, email_body="Hola, soy [Nombre] y adjunto mi CV.")
    r = aprobar(client, post_id, forzar_auditoria=True)
    assert r.status_code == 409
    detalle = r.json()["detail"]
    assert "[Nombre]" in detalle and MOTIVO_AUDITORIA not in detalle
    assert radar_store.get_post(post_id)["aprobado_at"] is None
    radar.enqueue_send.assert_not_awaited()


def test_el_permiso_no_vale_si_el_contenido_cambio(client, radar):
    post_id, _ = crear(REPORTE_NO_APROBADO)
    version = radar_store.get_post(post_id)["updated_at"]
    radar_store.update_post(post_id, email_body="Otro mensaje.", updated_at="2000-01-01 00:00:00")
    r = client.post(f"/api/radar/posts/{post_id}/approve", json={"updated_at": version, "forzar_auditoria": True})
    assert r.status_code == 409 and radar_store.get_post(post_id)["aprobado_at"] is None


def test_con_la_auditoria_aprobada_el_permiso_no_registra_nada(client, radar):
    post_id, _ = crear()
    assert aprobar(client, post_id, forzar_auditoria=True).status_code == 200
    assert radar_store.get_post(post_id)["forzado_auditoria_at"] is None


def test_el_envio_respeta_solo_el_permiso_de_esa_aprobacion():
    post_id, pid = crear(REPORTE_NO_APROBADO)
    radar_store.update_post(post_id, aprobado_at="2026-11-20 10:00:00", forzado_auditoria_at="2026-11-20 10:00:00")
    post = radar_store.get_post(post_id)
    assert server._revisar_contenido_heredado(post) == []
    assert server._motivos_contenido_heredado(post, database.get_postulacion(pid)) == []
    # Una aprobación posterior sin el permiso no hereda el anterior
    radar_store.update_post(post_id, aprobado_at="2026-11-20 11:00:00")
    post = radar_store.get_post(post_id)
    assert server._motivos_contenido_heredado(post, database.get_postulacion(pid)) == [MOTIVO_AUDITORIA]
    assert MOTIVO_AUDITORIA in server._revisar_contenido_heredado(post)
