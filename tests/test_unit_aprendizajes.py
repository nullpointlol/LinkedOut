"""Unitarios de `core/aprendizajes.py` sobre un archivo de aprendizajes propio de cada test (nunca
`data/` real): lectura dañada, validaciones, duplicados y aplicación a análisis y requisitos."""
import json

import pytest

from core import aprendizajes as ap


@pytest.fixture(autouse=True)
def archivo(monkeypatch, tmp_path):
    """Aprendizajes en un archivo temporal de este test."""
    ruta = tmp_path / "candidato" / "aprendizajes_candidato.json"
    monkeypatch.setattr(ap, "APRENDIZAJES_FILE", ruta)
    return ruta


def test_archivo_roto_se_lee_vacio(archivo, caplog):
    """Un JSON roto se registra como error y se lee como sin aprendizajes."""
    archivo.parent.mkdir(parents=True)
    archivo.write_text("{roto", encoding="utf-8")
    assert ap.obtener_aprendizajes() == {}
    assert "Error al leer aprendizajes" in caplog.text


def test_guardado_fallido_borra_el_temporal_y_registra(monkeypatch, archivo, caplog):
    """Si reemplazar el archivo falla y tampoco se puede borrar el temporal, se registran ambos errores."""
    from pathlib import Path

    def reemplazo_roto(self, destino):
        raise PermissionError("bloqueado")

    def borrado_roto(self, missing_ok=False):
        raise OSError("en uso")

    monkeypatch.setattr(Path, "replace", reemplazo_roto)
    monkeypatch.setattr(Path, "unlink", borrado_roto)
    assert ap._guardar_aprendizajes({"a": {}}) is False
    assert "Error al guardar aprendizajes" in caplog.text
    assert "Error al borrar temporal" in caplog.text


def test_registro_valida_tag_y_estado():
    """Tag vacío o estado desconocido lanzan ValueError."""
    for tag, estado in [("", "MATCH_DIRECTO"), (None, "MATCH_DIRECTO"), ("SQL", "QUIZAS")]:
        with pytest.raises(ValueError):
            ap.registrar_aprendizaje(tag, estado)


def test_registro_unifica_duplicados_y_conserva_la_decision(archivo):
    """Variantes del mismo tag (tildes/mayúsculas) se unifican; la decisión de descarte que tenía
    alguna de ellas sobrevive."""
    archivo.parent.mkdir(parents=True)
    archivo.write_text(json.dumps({
        "gastronomía": {"tag": "Gastronomía", "estado": "BRECHA_REAL"},
        "gastronomia": {"tag": "Gastronomia", "estado": "BRECHA_REAL", "eliminatorio": True},
    }), encoding="utf-8")
    entrada = ap.registrar_aprendizaje("GASTRONOMIA", "TRANSFERIBLE")
    datos = ap.obtener_aprendizajes()
    assert list(datos) == ["gastronomia"]
    assert entrada["eliminatorio"] is True and entrada["estado"] == "TRANSFERIBLE"


def test_aplicar_a_analisis_no_dict_lo_devuelve_igual():
    """Un análisis que no es dict se devuelve sin cambios."""
    assert ap.aplicar_aprendizajes_a_analisis(["x"]) == ["x"]


def test_aplicar_a_analisis_brecha_solo_si_estaba():
    """Una BRECHA aprendida reemplaza la mención existente; si no estaba, no se agrega."""
    ap.registrar_aprendizaje("Kubernetes", "BRECHA_REAL")
    ap.registrar_aprendizaje("Docker", "BRECHA_REAL")
    res = ap.aplicar_aprendizajes_a_analisis({"coincidencias": [{"nombre": "Kubernetes"}], "brechas": []})
    assert res["coincidencias"] == []
    assert res["brechas"] == ["Kubernetes"]


def test_aplicar_a_requisitos_ignora_los_que_no_son_dict():
    """Los requisitos que no son dict se saltean; el resto recibe el estado aprendido."""
    ap.registrar_aprendizaje("SQL", "MATCH_DIRECTO")
    reqs = ["texto suelto", {"nombre": "sql", "estado": "BRECHA_REAL"}]
    assert ap.aplicar_aprendizajes_a_requisitos(reqs) == 1
    assert reqs[1]["estado"] == "MATCH_DIRECTO" and reqs[1]["approved"] is True
