"""El medidor de cobertura cuenta exacto y abre la base en solo lectura. Datos inventados."""
import json
import sqlite3

import pytest

from tools.medir_cobertura_cv import medir

CV = "# LUCÍA FERRARO\n\n## PERFIL\nAnalista con Grafana.\n\n## HABILIDADES TÉCNICAS\n• Postman, Elastic.\n"


def _base(tmp_path):
    db = tmp_path / "postulaciones.db"
    con = sqlite3.connect(db)
    con.execute("create table postulaciones (id integer primary key, titulo text, requisitos text, cv_markdown text,"
                " status text, estado_postulacion text)")
    con.execute("create table linkedin_posts (id integer primary key, estado text, postulacion_id integer)")
    reqs = json.dumps([{"nombre": "Grafana, Dynatrace, Elastic", "estado": "MATCH_DIRECTO", "approved": True},
                       {"nombre": "Postman", "estado": "MATCH_DIRECTO", "approved": True}])
    con.executemany("insert into postulaciones values (?,?,?,?,?,?)", [
        (1, "Monitoreo", reqs, CV, "READY", "POR_POSTULAR"),
        (2, "Soporte", json.dumps([{"nombre": "Postman", "estado": "MATCH_DIRECTO", "approved": True}]), CV,
         "READY", "POSTULADA"),
        (3, "Descartada", reqs, CV, "READY", "POR_POSTULAR")])
    con.executemany("insert into linkedin_posts values (?,?,?)", [(10, "LISTO", 1), (30, "DESCARTADO", 3)])
    con.commit()
    con.close()
    return db


def test_cuenta_los_faltantes_de_las_listo_y_enviadas(tmp_path):
    r = medir(_base(tmp_path))
    assert r["total"] == {"postulaciones": 2, "afectadas": 1, "LISTO": 1, "ENVIADO": 0, "exigidos": 5, "faltan": 2}
    assert r["detalle"][0]["faltan"] == ["Grafana (MATCH_DIRECTO)", "Dynatrace (MATCH_DIRECTO)"]


def test_abre_la_base_en_solo_lectura(tmp_path, monkeypatch):
    db = _base(tmp_path)
    abiertas = []
    original = sqlite3.connect

    def connect(ruta, *a, **kw):
        abiertas.append(str(ruta))
        return original(ruta, *a, **kw)
    monkeypatch.setattr(sqlite3, "connect", connect)
    medir(db)
    assert abiertas and all("mode=ro" in a for a in abiertas)
