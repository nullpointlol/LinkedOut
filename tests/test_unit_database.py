"""Unitarios de `core/database.py` sobre la base aislada del test (conftest): días transcurridos,
migración repetida, normalización de filas dañadas y bordes de listado y actualización."""
import sqlite3
from datetime import datetime, timedelta

import pytest

import core.database as db


def test_dias_desde_fecha_sin_hora():
    """Una fecha AAAA-MM-DD también se interpreta; ayer es «hace 1d»."""
    ayer = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    assert db.format_days_elapsed(ayer) in ("hace 1d", "hace 2d")
    assert db.format_days_elapsed("no es fecha") == ""


def test_init_db_dos_veces_es_idempotente():
    """Volver a inicializar una base ya migrada no falla y conserva las columnas."""
    db.init_db(db.DB_PATH)
    conn = db.get_db_connection(db.DB_PATH)
    try:
        columnas = {fila["name"] for fila in conn.execute("PRAGMA table_info(postulaciones)")}
    finally:
        conn.close()
    assert "formato_cv" in columnas


def test_init_db_deja_la_base_en_wal(tmp_path):
    """La base opera en WAL: las lecturas no bloquean al Radar que escribe («database is locked»)."""
    base = tmp_path / "wal.db"
    db.init_db(base)
    conn = sqlite3.connect(base)
    try:
        modo = conn.execute("PRAGMA journal_mode;").fetchone()[0]
    finally:
        conn.close()
    assert modo.lower() == "wal"


def test_fila_con_json_danado_y_tipos_incorrectos_se_normaliza():
    """JSON ilegible queda como texto; tipos que no son dict y nulos toman valores por defecto."""
    fila = {"requisitos": "{roto", "pitches_multicanal": "[1]", "interview_prep": "[]",
            "networking_pitches": "3", "estado_postulacion": None, "radar_post_ref": None}
    d = db._row_to_dict(fila)
    assert d["requisitos"] == "{roto"
    assert (d["pitches_multicanal"], d["interview_prep"], d["networking_pitches"]) == ({}, {}, {})
    assert d["estado_postulacion"] == "POR_POSTULAR"
    assert d["radar_post_ref"] == ""


def test_listar_con_estado_como_primer_argumento():
    """Pasar el estado como primer argumento posicional lo usa como filtro sobre la base por defecto."""
    pid = db.create_postulacion("Analista", "Acme", "Buscamos analista.")
    db.update_postulacion(pid, estado_postulacion="POSTULADA")
    assert [p["id"] for p in db.list_postulaciones("POSTULADA")] == [pid]
    assert db.list_postulaciones("DESESTIMADA") == []


def test_actualizar_sin_campos_no_hace_nada_y_toggle_de_inexistente():
    """Actualizar sin campos es un no-op; alternar un tag de una postulación inexistente da None."""
    assert db.update_postulacion(999_999) is None
    assert db.toggle_tag(999_999, 0) is None
