"""Tests para Ciclo de Vida y Pipeline de Seguimiento (Tracker & Follow-up)."""
import os
import sqlite3
import pytest
from pathlib import Path
from datetime import datetime, timedelta
from fastapi.testclient import TestClient

from core.database import (
    init_db,
    create_postulacion,
    get_postulacion,
    list_postulaciones,
    update_postulacion,
    update_postulacion_lifecycle,
    format_days_elapsed,
    ALLOWED_ESTADOS,
)
from server import app


@pytest.fixture(autouse=True)
def setup_isolated_db(tmp_path, monkeypatch):
    """Blindaje de tests: aísla la base de datos a un archivo temporal para no ensuciar BD real."""
    test_db = tmp_path / "test_lifecycle_isolated.db"
    monkeypatch.setattr("core.database.DB_PATH", test_db)
    init_db(test_db)
    yield test_db
    if test_db.exists():
        try:
            test_db.unlink()
        except Exception:
            pass


def test_db_schema_migration_existing_and_new_records(tmp_path):
    """Verifica la migración de esquema sobre BD existente sin las nuevas columnas y con nueva BD."""
    legacy_db = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(legacy_db))
    # Crear esquema viejo sin columnas de ciclo de vida
    conn.execute("""
        CREATE TABLE postulaciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            titulo TEXT NOT NULL,
            empresa TEXT DEFAULT 'Confidencial',
            url TEXT DEFAULT '',
            contenido_oferta TEXT NOT NULL,
            dolores_empresa TEXT DEFAULT '[]',
            requisitos TEXT DEFAULT '[]',
            match_score REAL DEFAULT 0.0,
            cv_markdown TEXT DEFAULT '',
            pitch TEXT DEFAULT '',
            reporte_auditoria TEXT DEFAULT '{}',
            telemetry TEXT DEFAULT '{}',
            status TEXT DEFAULT 'PENDING'
        );
    """)
    conn.execute("INSERT INTO postulaciones (titulo, contenido_oferta) VALUES ('Senior Dev', 'Python job');")
    conn.commit()
    conn.close()

    # Ejecutar init_db sobre la BD heredada (migración con ALTER TABLE)
    init_db(legacy_db)

    # Comprobar que el registro previo ahora tiene las nuevas columnas con valores por defecto seguros
    post = get_postulacion(1, legacy_db)
    assert post is not None
    assert post["estado_postulacion"] == "POR_POSTULAR"
    assert post["fecha_postulacion"] is None
    assert post["link_postulacion"] == ""
    assert post["notas_seguimiento"] == ""
    assert post["contacto_recruiter"] == ""


def test_update_postulacion_lifecycle_valid_and_invalid_statuses():
    """Prueba transiciones de estado válidas, asignación de fecha automática y rechazo de estados inválidos."""
    post_id = create_postulacion(
        titulo="Backend Engineer",
        empresa="Mercado Libre",
        contenido="Python & FastAPI"
    )

    # Estado por defecto inicial
    post = get_postulacion(post_id)
    assert post["estado_postulacion"] == "POR_POSTULAR"

    # Transición válida a POSTULADA sin fecha explícita (debe autoasignar fecha actual)
    updated = update_postulacion_lifecycle(
        postulacion_id=post_id,
        estado="POSTULADA",
        link="https://jobs.mercadolibre.com/123",
        contacto="recruiter@meli.test",
        notas="Entrevista técnica en espera"
    )
    assert updated is not None
    assert updated["estado_postulacion"] == "POSTULADA"
    assert updated["link_postulacion"] == "https://jobs.mercadolibre.com/123"
    assert updated["contacto_recruiter"] == "recruiter@meli.test"
    assert updated["notas_seguimiento"] == "Entrevista técnica en espera"
    assert updated["fecha_postulacion"] is not None

    # Transición válida a EN_ENTREVISTA preservando o actualizando datos
    updated2 = update_postulacion_lifecycle(
        postulacion_id=post_id,
        estado="EN_ENTREVISTA",
        notas="Primera ronda superada con Tech Lead"
    )
    assert updated2["estado_postulacion"] == "EN_ENTREVISTA"
    assert updated2["notas_seguimiento"] == "Primera ronda superada con Tech Lead"
    # El link previo no debe borrarse si se omite o preserva
    assert updated2["link_postulacion"] == "https://jobs.mercadolibre.com/123"

    # Transición válida a OFERTA
    updated3 = update_postulacion_lifecycle(post_id, estado="OFERTA")
    assert updated3["estado_postulacion"] == "OFERTA"

    # Transición válida a DESESTIMADA
    updated4 = update_postulacion_lifecycle(post_id, estado="DESESTIMADA")
    assert updated4["estado_postulacion"] == "DESESTIMADA"

    # Estado inválido debe levantar ValueError
    with pytest.raises(ValueError) as excinfo:
        update_postulacion_lifecycle(post_id, estado="STATUS_INVENTADO")
    assert "Estado de postulación inválido" in str(excinfo.value)

    # ID no existente debe retornar None
    assert update_postulacion_lifecycle(9999, estado="POSTULADA") is None


def test_list_postulaciones_filtered_by_lifecycle():
    """Prueba el listado y filtrado por estado de ciclo de vida (unitario y por agrupadores)."""
    id1 = create_postulacion("P1", "E1", "c1")
    id2 = create_postulacion("P2", "E2", "c2")
    id3 = create_postulacion("P3", "E3", "c3")
    id4 = create_postulacion("P4", "E4", "c4")

    update_postulacion_lifecycle(id1, estado="POR_POSTULAR")
    update_postulacion_lifecycle(id2, estado="POSTULADA")
    update_postulacion_lifecycle(id3, estado="EN_ENTREVISTA")
    update_postulacion_lifecycle(id4, estado="DESESTIMADA")

    # Sin filtro: todas las ofertas
    todas = list_postulaciones()
    assert len(todas) == 4

    # Filtro 'POSTULADA'
    postuladas = list_postulaciones(filtro_lifecycle="POSTULADA")
    assert len(postuladas) == 1
    assert postuladas[0]["id"] == id2

    # Filtro 'POR_POSTULAR'
    por_postular = list_postulaciones(filtro_lifecycle="POR_POSTULAR")
    assert len(por_postular) == 1
    assert por_postular[0]["id"] == id1

    # Agrupador 'EN_PROCESO' (EN_ENTREVISTA + OFERTA)
    en_proceso = list_postulaciones(filtro_lifecycle="EN_PROCESO")
    assert len(en_proceso) == 1
    assert en_proceso[0]["id"] == id3

    # Agrupador 'FINALIZADAS' (DESESTIMADA)
    finalizadas = list_postulaciones(filtro_lifecycle="FINALIZADAS")
    assert len(finalizadas) == 1
    assert finalizadas[0]["id"] == id4


def test_api_lifecycle_endpoint_and_filtering():
    """Prueba el endpoint POST /api/ofertas/{id}/lifecycle y GET /api/ofertas?filtro_lifecycle=..."""
    client = TestClient(app)

    # Crear una oferta directamente en la base de datos de prueba
    post_id = create_postulacion(
        titulo="Lifecycle Tester",
        empresa="Tech Corp",
        contenido="Python, SQL, QA"
    )

    # Endpoint POST /api/ofertas/{id}/lifecycle con payload válido
    payload = {
        "estado_postulacion": "POSTULADA",
        "link_postulacion": "https://linkedin.com/jobs/view/999",
        "notas_seguimiento": "Postulado mediante Easy Apply",
        "contacto_recruiter": "Sofia Gómez (Talent Acquisition)",
        "fecha_postulacion": "2026-10-24 14:00:00"
    }
    r_lifecycle = client.post(f"/api/ofertas/{post_id}/lifecycle", json=payload)
    assert r_lifecycle.status_code == 200
    data = r_lifecycle.json()
    assert data["estado_postulacion"] == "POSTULADA"
    assert data["link_postulacion"] == "https://linkedin.com/jobs/view/999"
    assert data["notas_seguimiento"] == "Postulado mediante Easy Apply"
    assert data["contacto_recruiter"] == "Sofia Gómez (Talent Acquisition)"
    assert data["fecha_postulacion"] == "2026-10-24 14:00:00"

    # Verificar obtención con GET /api/ofertas/{id}
    r_get = client.get(f"/api/ofertas/{post_id}")
    assert r_get.status_code == 200
    det = r_get.json()
    assert det["estado_postulacion"] == "POSTULADA"

    # Probar endpoint con estado inválido (debe retornar 400)
    r_invalid = client.post(f"/api/ofertas/{post_id}/lifecycle", json={"estado_postulacion": "NO_EXISTE"})
    assert r_invalid.status_code == 400

    # Probar endpoint con oferta inexistente (debe retornar 404)
    r_notfound = client.post("/api/ofertas/999999/lifecycle", json={"estado_postulacion": "POSTULADA"})
    assert r_notfound.status_code == 404

    # Probar filtrado en GET /api/ofertas?filtro_lifecycle=POSTULADA
    r_list_filter = client.get("/api/ofertas?filtro_lifecycle=POSTULADA")
    assert r_list_filter.status_code == 200
    ofertas_postuladas = r_list_filter.json()
    assert any(o["id"] == post_id for o in ofertas_postuladas)

    # Probar filtrado en GET /api/ofertas?filtro_lifecycle=DESESTIMADA (no debe incluir post_id)
    r_list_des = client.get("/api/ofertas?filtro_lifecycle=DESESTIMADA")
    assert r_list_des.status_code == 200
    assert not any(o["id"] == post_id for o in r_list_des.json())


def test_format_days_elapsed_utility():
    """Prueba el cálculo y formateo de días transcurridos desde la fecha de postulación."""
    now = datetime.now()

    # Fecha hoy
    today_str = now.strftime("%Y-%m-%d %H:%M:%S")
    res_today = format_days_elapsed(today_str)
    assert res_today in ("hoy", "hace 0d")

    # Fecha hace 1 día
    yesterday = now - timedelta(days=1)
    res_yesterday = format_days_elapsed(yesterday.strftime("%Y-%m-%d %H:%M:%S"))
    assert res_yesterday == "hace 1d"

    # Fecha hace 5 días
    five_days_ago = now - timedelta(days=5)
    res_five = format_days_elapsed(five_days_ago.strftime("%Y-%m-%d %H:%M:%S"))
    assert res_five == "hace 5d"

    # Valores nulos o vacíos
    assert format_days_elapsed(None) == ""
    assert format_days_elapsed("") == ""
    assert format_days_elapsed("fecha_invalida") == ""


def test_frontend_ui_elements_exist():
    """Verifica que index.html y app.js contengan los elementos y controladores de ciclo de vida requeridos."""
    html_path = Path("static/index.html")
    js_path = Path("static/app.js")

    assert html_path.exists()
    assert js_path.exists()

    html = html_path.read_text(encoding="utf-8")
    js = js_path.read_text(encoding="utf-8")

    # Verificación en HTML de tabs de ciclo de vida
    assert "Por Postular" in html or "POR_POSTULAR" in html
    assert "Postuladas" in html or "POSTULADA" in html
    assert "En proceso" in html or "En Proceso" in html  # §8: sentence case
    assert "Finalizadas" in html

    # Verificación en HTML de control de seguimiento
    assert "selectEstadoPostulacion" in html or "lifecycleControlBox" in html
    assert "inputLinkPostulacion" in html or "link_postulacion" in html
    assert "inputContactoRecruiter" in html or "contacto_recruiter" in html
    assert "inputNotasSeguimiento" in html or "notas_seguimiento" in html

    # Verificación en JS de handlers y llamadas a lifecycle
    assert "/lifecycle" in js
    assert "estado_postulacion" in js
    assert "formatearDiasTranscurridos" in js or "fecha_postulacion" in js
