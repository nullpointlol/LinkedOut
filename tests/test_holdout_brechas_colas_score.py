from copy import deepcopy
from datetime import datetime, timezone
import os
import pytest
from core import colas, strategist
from core.prioridades import elegible_automatico


def test_formula_exacta_y_afinidad_conservada():
    match = {"nombre": "Python", "estado": "MATCH_DIRECTO", "tipo": "REQUISITO_EXCLUYENTE"}
    transferible = {"nombre": "SQL", "estado": "TRANSFERIBLE", "tipo": "REQUISITO_EXCLUYENTE", "approved": True}
    # invariante: referencias de la fórmula actual, grupo A/B sin brechas excluyentes.
    assert strategist.calculate_match_score([match, transferible]) == 85.0
    assert strategist.calculate_match_score([match]) == 100.0
    assert strategist.peso_requisito({**match, "nombre": "Presencial en CABA"}) == 0.0
    if os.getenv("INVARIANT_CHECK") == "1": return
    gap = {"nombre": "Arquitectura distribuida", "estado": "BRECHA_REAL", "tipo": "REQUISITO_EXCLUYENTE"}
    assert strategist.peso_requisito(gap) == 1.5
    score, desglose = strategist.calculate_match_score_with_breakdown([match, gap])
    assert score == 40.0
    assert desglose["brechas"][0]["puntos_deducidos"] == 60.0
    assert strategist.peso_requisito({**gap, "tipo": "REQUISITO_DESEABLE"}) == 0.5
    assert strategist.peso_requisito({**gap, "tipo": "RESPONSABILIDAD"}) == 1.0

@pytest.mark.parametrize("dias,grupo", [(0,"A"),(30,"A"),(0,"C"),(20,"B")])
def test_no_apto_no_se_esconde_en_fondo_y_forzado_entra(dias, grupo):
    from datetime import timedelta
    ahora = datetime(2026,11,9,12,tzinfo=timezone.utc)
    p = {"id": 1, "estado": "ANALIZADA", "texto": "Oferta de analista. " * 40,
         "created_at": (ahora-timedelta(days=dias)).isoformat(), "emails": ["rrhh@example.com"],
         "triage": {"puesto": "Analista funcional"}, "analisis": {"puesto": "Analista funcional", "grupo_rol": grupo,
         "match_score": 90, "no_apto": {"motivos": [{"requisito":"Viajes", "categoria":"VIAJES", "texto":"Viajar al interior"}]}}}
    original = deepcopy(p)
    # invariante: forzar mantiene prioridad 2; una función pura no cambia estado ni la entrada.
    assert colas.nivel({**p,"gen_prioridad":2}, ahora) == colas.PRIORITARIA
    assert p == original and p["estado"] == "ANALIZADA"
    if os.getenv("INVARIANT_CHECK") == "1": return
    assert elegible_automatico(p) is False
    assert colas.nivel(p, ahora) is None
    assert all(not q for q in colas.armar_colas([p], ahora).values())
    forzado = {**p,"gen_prioridad":2}
    assert elegible_automatico(forzado) is False
    assert colas.armar_colas([forzado], ahora)[colas.PRIORITARIA] == [forzado]
    assert p == original
