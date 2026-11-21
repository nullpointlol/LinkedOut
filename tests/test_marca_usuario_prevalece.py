"""La marca del usuario prevalece y la evidencia del maestro cuenta para la regla de años."""
from unittest.mock import AsyncMock, Mock

import pytest

from core import aprendizajes
from core.afinidad_reglas import aplicar_reglas
from core.radar_api import _ciclar_requisito
from core.strategist import StrategistAgent

MAESTRO = """# Experiencia profesional
## Circo Ejemplo
### Analista de Soporte Productivo | 2019 - 2024
• Atendí incidentes de pista con el tablero Zentrix.
## Acrobacias SA
### Operador de Monitoreo de Funciones | 2014 - 2019
• Monitoreé alertas de 40 carpas con Zentrix y Lumbra.
## HABILIDADES TÉCNICAS
• **Incidentes y monitoreo:** Zentrix, Lumbra.
"""
AREAS = [{"area": "Experiencia laboral total", "anios": "11"},
         {"area": "Soporte e incidentes", "anios": "más de 8"},
         {"area": "Monitoreo de funciones", "anios": "5"}]
REQ = {"nombre": "Experiencia de 6+ años en Zentrix", "tipo": "REQUISITO_EXCLUYENTE", "estado": "TRANSFERIBLE",
       "approved": True, "justificacion": "Usa Zentrix en soporte y monitoreo.",
       "texto": "Candidates with 6+ years of hands-on Zentrix experience are encouraged to apply."}


@pytest.fixture(autouse=True)
def _aprendizajes_propios(monkeypatch, tmp_path):
    monkeypatch.setattr(aprendizajes, "APRENDIZAJES_FILE", tmp_path / "aprendizajes.json")


def test_la_evidencia_del_maestro_cuenta_para_los_anios():
    r = aplicar_reglas([dict(REQ)], puesto="Zentrix Developer", areas=AREAS, base_candidato=MAESTRO)[0]
    # Zentrix figura en los cargos de soporte (más de 8 años) y de monitoreo: no es brecha de años
    assert r["estado"] == "TRANSFERIBLE" and r.get("dura") is None and r["approved"] is True
    assert r["justificacion"] == REQ["justificacion"]


def test_con_menos_anios_en_las_areas_de_la_evidencia_es_brecha_con_el_area():
    pocos = [{"area": "Soporte e incidentes", "anios": "3"}, {"area": "Monitoreo de funciones", "anios": "2"}]
    r = aplicar_reglas([dict(REQ)], puesto="Zentrix Developer", areas=pocos, base_candidato=MAESTRO)[0]
    assert r["estado"] == "BRECHA_REAL" and r["dura"] == "anios"
    assert r["justificacion"] == "Pide 6 años; el perfil tiene 3 en Soporte e incidentes"


def test_sin_evidencia_en_el_maestro_sigue_sin_anios_en_esa_area():
    req = {**REQ, "nombre": "Experiencia de 6+ años en Quarkon", "texto": "6+ years of Quarkon"}
    r = aplicar_reglas([req], puesto="Quarkon Developer", areas=AREAS, base_candidato=MAESTRO)[0]
    assert r["estado"] == "BRECHA_REAL" and r["justificacion"] == "Pide 6 años; sin años en esa área"


def test_tu_marca_de_coincidencia_saca_la_brecha_fuerte():
    reqs = [{**REQ, "estado": "BRECHA_REAL", "approved": False, "dura": "anios",
             "justificacion": "Pide 6 años; sin años en esa área"}]
    _ciclar_requisito(reqs, REQ["nombre"])  # brecha → transferible
    r = _ciclar_requisito(reqs, REQ["nombre"])  # transferible → coincidencia
    assert r["estado"] == "MATCH_DIRECTO" and r["aprendido"] is True and "dura" not in r
    # Un recálculo de reglas no pisa la marca
    assert aplicar_reglas(reqs, puesto="Zentrix Developer", areas=[], base_candidato="")[0] == r


def test_tu_marca_aprendida_saca_la_brecha_fuerte_al_aplicarse():
    aprendizajes.registrar_aprendizaje(REQ["nombre"], "MATCH_DIRECTO", origen="radar")
    reqs = [{**REQ, "estado": "BRECHA_REAL", "approved": False, "dura": "anios"}]
    aprendizajes.aplicar_aprendizajes_a_requisitos(reqs)
    assert reqs[0]["estado"] == "MATCH_DIRECTO" and reqs[0]["aprendido"] is True and "dura" not in reqs[0]


@pytest.mark.asyncio
async def test_un_re_analisis_no_pisa_tu_marca(monkeypatch):
    aprendizajes.registrar_aprendizaje(REQ["nombre"], "MATCH_DIRECTO", origen="radar")
    from core import strategist
    monkeypatch.setattr(strategist, "anios_por_area_en_cache", lambda: [])
    monkeypatch.setattr(strategist, "cargar_base_candidato", lambda: "")
    respuesta = {"empresa": "Acme", "puesto": "Zentrix Developer",
                 "requisitos": [{**REQ, "estado": "BRECHA_REAL", "approved": False}]}
    agente = StrategistAgent(bridge=Mock(effort="low", run_prompt=AsyncMock(return_value=respuesta)),
                             expander=Mock(get_expanded_competencies=Mock(return_value=[])))
    r = (await agente.analyze_offer("Zentrix Developer\nCandidates with 6+ years of Zentrix"))["requisitos"][0]
    assert r["estado"] == "MATCH_DIRECTO" and r["aprendido"] is True and "dura" not in r
