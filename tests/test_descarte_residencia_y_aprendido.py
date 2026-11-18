"""Residencia contra donde vive el candidato, híbrido en el exterior y «Me descarta» que aprende el rubro
(tickets del dev 1112, 15/11). Candidato inventado: vive en Villa Prueba."""
import json

import pytest

from core import aprendizajes, descartes

RESTRICCIONES = {
    "base": "Villa Prueba, AMBA",
    "presencial_hibrido_permitido": "AMBA",
    "remoto_desde": "Argentina",
    "localidades_permitidas": ["amba", "caba", "gba", "buenos aires", "villa prueba", "moron", "pilar"],
    "residencia": ["villa prueba", "gba", "amba", "buenos aires", "pba", "argentina"],
    "residencia_cerca_de": ["caba", "capital federal"],
}


def _req(nombre, estado="BRECHA_REAL", tipo="REQUISITO_EXCLUYENTE", **extra):
    return {"nombre": nombre, "texto": nombre, "tipo": tipo, "estado": estado, **extra}


@pytest.mark.parametrize("nombre,estado", [
    ("Residencia en Zona Oeste", "TRANSFERIBLE"),       # oferta 11144: el modelo la dio por cumplida
    ("Residir en Morón (excluyente)", "MATCH_DIRECTO"),
    ("Vivir en zona sur del conurbano", "BRECHA_REAL"),
    ("Residencia en CABA", "MATCH_DIRECTO"),
    ("Residencia en zonas cercanas a Pilar", "TRANSFERIBLE"),
])
def test_una_residencia_que_no_es_la_del_candidato_descarta(nombre, estado):
    motivos = descartes.motivos_descarte({"requisitos": [_req(nombre, estado)]}, RESTRICCIONES)
    assert [m["categoria"] for m in motivos] == ["UBICACION"]


@pytest.mark.parametrize("nombre", [
    "Residencia en Zona Oeste (Villa Prueba)",  # oferta 2042: nombra la localidad del candidato
    "Residir en CABA o GBA",
    "Residencia en Argentina",
    "Residencia en zonas aledañas",             # sin un lugar reconocible no se descarta
    "Residencia en Zona Oeste (deseable)",
    "Residencia en la zona",                    # la del puesto, sin nombrarla (oferta 11274)
    "Residencia en zona o aledaños",
    "Para residencia familiar ubicada en Barrio El Golf, Nordelta",  # la casa del empleador (oferta 11163)
    "Residencia en CABA o aledaños",            # el candidato vive cerca de CABA (residencia_cerca_de)
    "Residencia en CABA o PBA",
])
def test_una_residencia_compatible_o_vaga_no_descarta(nombre):
    assert descartes.motivos_descarte({"requisitos": [_req(nombre, "MATCH_DIRECTO")]}, RESTRICCIONES) == []


def test_sin_residencia_en_el_perfil_no_hay_regla():
    restricciones = {k: v for k, v in RESTRICCIONES.items() if k != "residencia"}
    assert descartes.motivos_descarte({"requisitos": [_req("Residencia en Zona Oeste", "TRANSFERIBLE")]}, restricciones) == []


def test_el_veto_del_creador_prevalece_sobre_la_residencia():
    req = _req("Residencia en Zona Oeste", eliminatorio_usuario=False)
    assert descartes.motivos_descarte({"requisitos": [req]}, RESTRICCIONES) == []


def test_hibrido_en_una_ciudad_del_exterior_no_descarta():
    # puede aceptar remoto 100 % para extranjeros (creador, 16/11)
    req = _req("Modalidad híbrida en Bogotá", logistico=True, eliminatorio=False)  # oferta 10120
    assert descartes.motivos_descarte({"requisitos": [req]}, RESTRICCIONES) == []


def test_me_descarta_vale_en_una_responsabilidad_y_un_deseable():
    reqs = [_req("Reclutamiento masivo de candidatos", tipo="RESPONSABILIDAD", eliminatorio_usuario=True),
            _req("Experiencia en servicios legales", tipo="REQUISITO_DESEABLE", eliminatorio_usuario=True)]
    assert [m["categoria"] for m in descartes.motivos_descarte({"requisitos": reqs}, RESTRICCIONES)] == ["RUBRO", "RUBRO"]
    # sin la decisión del creador, una responsabilidad no descarta sola
    assert descartes.motivos_descarte({"requisitos": [_req("Reclutamiento masivo", tipo="RESPONSABILIDAD")]}, RESTRICCIONES) == []


@pytest.fixture
def aprendido(tmp_path, monkeypatch):
    archivo = tmp_path / "aprendizajes.json"
    monkeypatch.setattr(aprendizajes, "APRENDIZAJES_FILE", archivo)
    aprendizajes.registrar_aprendizaje("Reclutamiento masivo de candidatos", "BRECHA_REAL", eliminatorio=True)
    aprendizajes.registrar_aprendizaje("Experiencia en servicios legales", "BRECHA_REAL", eliminatorio=True)
    return archivo


@pytest.mark.parametrize("nombre", ["Experiencia en reclutamiento masivo", "Servicios legales corporativos"])
def test_el_rubro_aprendido_descarta_otra_redaccion(aprendido, nombre):
    reqs = [_req(nombre, tipo="RESPONSABILIDAD")]
    assert aprendizajes.aplicar_aprendizajes_a_requisitos(reqs) == 1
    assert reqs[0]["eliminatorio_usuario"] is True and reqs[0]["estado"] == "BRECHA_REAL"
    assert [m["categoria"] for m in descartes.motivos_descarte({"requisitos": reqs}, RESTRICCIONES)] == ["RUBRO"]


@pytest.mark.parametrize("nombre,estado", [
    ("Servicios de soporte", "BRECHA_REAL"),                # comparte una sola palabra
    ("Experiencia en reclutamiento masivo", "MATCH_DIRECTO"),  # el candidato lo tiene: no se descarta
])
def test_lo_que_no_es_el_rubro_o_ya_se_tiene_no_se_marca(aprendido, nombre, estado):
    reqs = [_req(nombre, estado)]
    aprendizajes.aplicar_aprendizajes_a_requisitos(reqs)
    assert "eliminatorio_usuario" not in reqs[0]
