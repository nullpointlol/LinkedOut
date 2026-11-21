"""Presencial o híbrida fuera de los lugares del perfil: no apto por ubicación."""
import pytest

from core import descartes

PERFIL = {"base": "San Martín, AMBA", "remoto_desde": "Argentina",
          "localidades_permitidas": ["amba", "caba", "gba", "buenos aires", "capital federal", "san martin"],
          "residencia": ["san martin", "zona norte", "gba", "argentina"],
          "residencia_cerca_de": ["caba", "capital federal"]}


def _aplicar(ubicacion, manual=False, **extra):
    analisis = {"ubicacion": ubicacion, **extra}
    descartes.aplicar_condicion_ubicacion(analisis, "Buscamos analista.", PERFIL, manual=manual)
    return analisis


def _motivos(analisis):
    return [m for m in (analisis.get("no_apto") or {}).get("motivos", []) if m["categoria"] == "UBICACION"]


@pytest.mark.parametrize("ubicacion", [
    {"pais": "Argentina", "provincia": "Córdoba", "localidad": "Zona Norte", "modalidad": "hibrida"},
    {"pais": "Argentina", "provincia": "Neuquén", "localidad": "Neuquén", "modalidad": "híbrida"},
    {"pais": "Argentina", "provincia": "Río Negro", "localidad": "General Roca", "modalidad": "presencial"},
    {"pais": "Chile", "provincia": None, "localidad": "Santiago", "modalidad": "presencial"},
])
def test_fuera_del_perfil_no_apto(ubicacion):
    motivos = _motivos(_aplicar(ubicacion))
    assert len(motivos) == 1 and ubicacion["localidad"] in motivos[0]["texto"]


@pytest.mark.parametrize("ubicacion", [
    {"pais": "Argentina", "provincia": "CABA", "localidad": "Palermo", "modalidad": "hibrida"},
    {"pais": "Argentina", "provincia": "Buenos Aires", "localidad": "San Martín", "modalidad": "presencial"},
    {"pais": "Argentina", "provincia": "Córdoba", "localidad": None, "modalidad": "remota"},
    {"pais": None, "provincia": None, "localidad": None, "modalidad": None},
])
def test_dentro_del_perfil_o_remota_es_apta(ubicacion):
    assert _motivos(_aplicar(ubicacion)) == []


def test_carga_manual_y_postular_igual_no_suman_motivo():
    fuera = {"pais": "Argentina", "provincia": "Córdoba", "localidad": "Córdoba", "modalidad": "presencial"}
    assert _motivos(_aplicar(fuera, manual=True)) == []
    assert _motivos(_aplicar(fuera, ubicacion_aceptada=True)) == []


def test_reaplicar_no_duplica_y_quita_si_cambia():
    analisis = _aplicar({"pais": "Argentina", "provincia": "Córdoba", "localidad": "Córdoba", "modalidad": "presencial"})
    descartes.aplicar_condicion_ubicacion(analisis, "Buscamos analista.", PERFIL, manual=False)
    assert len(_motivos(analisis)) == 1
    analisis["ubicacion"]["modalidad"] = "remota"
    descartes.aplicar_condicion_ubicacion(analisis, "Buscamos analista.", PERFIL, manual=False)
    assert _motivos(analisis) == [] and "no_apto" not in analisis


def test_sin_perfil_no_inventa():
    analisis = {"ubicacion": {"provincia": "Córdoba", "localidad": "Córdoba", "modalidad": "presencial"}}
    descartes.aplicar_condicion_ubicacion(analisis, "Buscamos analista.", {}, manual=False)
    assert "no_apto" not in analisis
