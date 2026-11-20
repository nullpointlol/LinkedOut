"""Limpieza de ofertas viejas: borra ANALIZADA, DESCARTADO e IGNORADA de más de DIAS_LIMPIEZA días con afinidad
menor a la mínima (o sin afinidad), con backup de la base antes de borrar."""
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import core.database
from core import radar_store
from core.database import create_postulacion, get_postulacion
from core.radar_store import (DIAS_LIMPIEZA, candidatas_limpieza, get_post, insert_post_if_new, limpiar_viejas,
                              update_post)


def _post(n, estado, dias, score=None, con_cv=False):
    pid = insert_post_if_new({"urn": f"urn:li:activity:{n}", "url": "", "autor": "Ana Pérez", "cargo_autor": "RR. HH.",
                              "texto": f"Buscamos analista {n}", "fecha_rel": "3 h", "emails": []}, "analista")
    campos = {"estado": estado}
    if score is not None:
        campos["analisis"] = {"match_score": score}
    if con_cv:
        campos["postulacion_id"] = create_postulacion(f"Analista {n}", "Empresa Ejemplo", "Texto", status="READY")
    update_post(pid, **campos)
    fecha = (datetime.now(timezone.utc) - timedelta(days=dias)).strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(core.database.DB_PATH)
    with conn:
        conn.execute("UPDATE linkedin_posts SET created_at = ? WHERE id = ?", (fecha, pid))
    conn.close()
    return pid


@pytest.fixture
def base():
    viejo = DIAS_LIMPIEZA + 5
    return {
        "analizada_baja": _post(1, "ANALIZADA", viejo, 40),
        "descartado_sin_score": _post(2, "DESCARTADO", viejo),
        "ignorada_baja_con_cv": _post(3, "IGNORADA", viejo, 55, con_cv=True),
        "analizada_alta": _post(4, "ANALIZADA", viejo, 85),
        "analizada_reciente": _post(5, "ANALIZADA", DIAS_LIMPIEZA - 2, 30),
        "enviado_viejo": _post(6, "ENVIADO", viejo, 20),
        "listo_viejo": _post(7, "LISTO", viejo, 20),
        "error_viejo": _post(8, "ERROR", viejo),
    }


def test_candidatas_solo_las_que_cumplen_las_tres_condiciones(base):
    c = candidatas_limpieza(70)
    assert c["total"] == 3
    assert c["por_estado"] == {"ANALIZADA": 1, "DESCARTADO": 1, "IGNORADA": 1}


def test_respeta_el_umbral_vigente(base):
    assert candidatas_limpieza(90)["total"] == 4  # la de 85 pasa a ser baja
    assert candidatas_limpieza(50)["total"] == 2  # la de 55 deja de serlo


def test_borra_solo_las_candidatas_y_sus_postulaciones(base):
    cv = get_post(base["ignorada_baja_con_cv"])["postulacion_id"]
    r = limpiar_viejas(70)
    assert r["borradas"] == 3 and r["postulaciones"] == 1
    for clave in ("analizada_baja", "descartado_sin_score", "ignorada_baja_con_cv"):
        assert get_post(base[clave]) is None
    for clave in ("analizada_alta", "analizada_reciente", "enviado_viejo", "listo_viejo", "error_viejo"):
        assert get_post(base[clave]) is not None
    assert get_postulacion(cv) is None
    assert candidatas_limpieza(70)["total"] == 0


def test_hace_backup_antes_de_borrar(base):
    r = limpiar_viejas(70)
    copia = sqlite3.connect(r["backup"])
    try:
        assert copia.execute("SELECT COUNT(*) FROM linkedin_posts").fetchone()[0] == 8
    finally:
        copia.close()
    assert r["backup"].endswith("-antes-de-limpieza.db")
    assert r["mb_despues"] <= r["mb_antes"]


def test_si_el_backup_falla_no_borra_nada(base, monkeypatch):
    def falla(*_a, **_k):
        raise OSError("disco lleno")
    monkeypatch.setattr(radar_store, "_backup_base", falla)
    with pytest.raises(OSError):
        limpiar_viejas(70)
    assert candidatas_limpieza(70)["total"] == 3


def test_sin_candidatas_no_hace_backup(monkeypatch):
    llamado = []
    monkeypatch.setattr(radar_store, "_backup_base", lambda *a, **k: llamado.append(1))
    r = limpiar_viejas(70)
    assert r["borradas"] == 0 and llamado == []


def test_analisis_ilegible_cuenta_como_sin_afinidad():
    pid = _post(9, "DESCARTADO", DIAS_LIMPIEZA + 1)
    conn = sqlite3.connect(core.database.DB_PATH)
    with conn:
        conn.execute("UPDATE linkedin_posts SET analisis = '{roto' WHERE id = ?", (pid,))
    conn.close()
    assert candidatas_limpieza(70)["total"] == 1


# ---------------------------------------------------------------- API

def _cliente(ip="127.0.0.1"):
    import server
    from fastapi.testclient import TestClient
    return TestClient(server.app, base_url="http://localhost:8000", client=(ip, 51000))


def test_api_cuenta_y_limpia_con_el_umbral_vigente(base):
    radar_store.update_radar_state(umbral_match=70)
    c = _cliente()
    r = c.get("/api/radar/limpieza")
    assert r.status_code == 200 and r.json()["total"] == 3
    r = c.post("/api/radar/limpieza")
    assert r.status_code == 200, r.text
    assert r.json()["borradas"] == 3 and r.json()["postulaciones"] == 1
    assert c.get("/api/radar/limpieza").json()["total"] == 0


def test_api_rechaza_fuera_de_la_pc(base, monkeypatch):
    import server
    from core.radar_api import ESTADO_HOSTS_DE_TEST
    monkeypatch.setattr(server.app.state, ESTADO_HOSTS_DE_TEST, set(), raising=False)
    c = _cliente(ip="192.168.1.50")
    assert c.get("/api/radar/limpieza").status_code == 403
    assert c.post("/api/radar/limpieza").status_code == 403
    assert candidatas_limpieza(70)["total"] == 3
