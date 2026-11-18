"""Afinidad y admisión de brechas eliminatorias, sin datos ni proveedores reales."""
import copy
import os
from datetime import datetime, timedelta, timezone

import pytest

from core import colas, radar_store as store, strategist
from core.prioridades import elegible_automatico
from tests.test_radar_workers import crear_post, make_radar


AHORA = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)


def req(nombre, estado="MATCH_DIRECTO", tipo="REQUISITO_EXCLUYENTE", **extra):
    return dict(nombre=nombre, estado=estado, tipo=tipo, **extra)


def post(pid=1, grupo="a", dias=0, score=95, **extra):
    return dict(id=pid, estado="ANALIZADA", fuente="radar", gen_prioridad=0,
                created_at=(AHORA - timedelta(days=dias)).isoformat(),
                texto="Oferta sintética", emails=["rrhh@example.com"],
                triage={"puesto": "Analista Funcional"},
                analisis={"grupo_rol": grupo, "match_score": score}, **extra)


def sello():
    return {"motivos": [{"requisito": "Viajes al interior", "categoria": "VIAJES",
                         "texto": "Disponibilidad para viajar al interior"}]}


def test_pesos_exactos():
    # invariante: plan, logística=0, genérico=0,25, deseable=0,5, resto=1.
    for tag, peso in [
        (req("Modalidad presencial en CABA", "BRECHA_REAL"), 0.0),
        (req("Modalidad presencial en CABA"), 0.0),
        (req("Inglés técnico"), 0.25),
        (req("Docker", tipo="REQUISITO_DESEABLE"), 0.5),
        (req("Docker", "BRECHA_REAL", "REQUISITO_DESEABLE"), 0.5),
        (req("SQL", "BRECHA_REAL", "RESPONSABILIDAD"), 1.0),
        (req("SQL"), 1.0),
        (req("SQL", "TRANSFERIBLE", approved=True), 1.0),
    ]:
        assert strategist.peso_requisito(tag) == peso
    tags = [req("SQL"), req("Docker", "BRECHA_REAL")]
    original = copy.deepcopy(tags)
    score, detalle = strategist.calculate_match_score_with_breakdown(tags)
    assert tags == original  # invariante: función pura, entrada intacta en base.
    if os.getenv("INVARIANT_CHECK") == "1":
        return
    assert strategist.peso_requisito(tags[1]) == 1.5
    assert strategist.peso_requisito(req("Inglés técnico", "BRECHA_REAL")) == 1.5
    assert score == 40.0  # base 50,0; 1 / (1 + 1,5) = 40%.
    assert detalle["puntos_faltantes"] == 60.0
    assert detalle["brechas"] == [{"nombre": "Docker", "puntos_deducidos": 60.0}]


def test_scores_base_sin_brechas():
    casos = [
        ([req("SQL"), req("Docker", "TRANSFERIBLE", approved=True)], 85.0),
        ([req("SQL"), req("Docker")], 100.0),
        ([req("Modalidad presencial en CABA")], 0.0),
    ]
    for tags, esperado in casos:
        original = copy.deepcopy(tags)
        # invariante: bases numéricas medidas en plan: 85,0; 100,0; logística=0.
        assert strategist.calculate_match_score(tags) == esperado
        assert tags == original  # invariante: score base no muta requisitos.
    if os.getenv("INVARIANT_CHECK") == "1":
        return
    # Sin techo artificial: ocho matches y dos brechas dan 8/11, no <=70.
    assert strategist.calculate_match_score(
        [req("SQL")] * 8 + [req("Docker", "BRECHA_REAL")] * 2
    ) == 72.7


@pytest.mark.parametrize("grupo,dias,score", [
    ("a", 0, 95), ("b", 0, 95), ("c", 0, 95),
    ("a", 40, 95), ("b", 40, 95), ("c", 40, 95), ("a", 0, 20),
])
def test_no_apto_fuera_de_ambas_colas_y_forzado(grupo, dias, score):
    p = post(grupo=grupo, dias=dias, score=score)
    esperado = colas.PRIORITARIA if grupo in ("a", "b") and dias <= 14 and score > 70 else colas.FONDO
    assert colas.nivel(p, AHORA) == esperado  # invariante: niveles base, test_colas_dinamicas.
    for vacio in (None, {}, {"motivos": []}):
        p["analisis"]["no_apto"] = vacio
        assert colas.nivel(p, AHORA) == esperado  # invariante: sin motivos no excluir de más.
        assert elegible_automatico(p) == (score > 70)  # invariante: umbral base estricto.
    p["analisis"]["no_apto"] = sello()
    original = copy.deepcopy(p)
    armadas = colas.armar_colas([p], AHORA)
    assert p == original  # invariante: colas puras no mutan post, análisis ni ANALIZADA.
    for fuerza in ({"gen_prioridad": 2}, {"fuente": "manual"}):
        forzada = copy.deepcopy(p)
        forzada.update(fuerza)
        assert colas.nivel(forzada, AHORA) == colas.PRIORITARIA  # invariante: fuerza manual base.
        assert colas.armar_colas([forzada], AHORA)[colas.PRIORITARIA] == [forzada]  # invariante: admisión manual base.
    if os.getenv("INVARIANT_CHECK") == "1":
        return
    assert not elegible_automatico(p)
    assert colas.nivel(p, AHORA) is None
    assert armadas == {colas.PRIORITARIA: [], colas.FONDO: []}
    for fuerza in ({"gen_prioridad": 2}, {"fuente": "manual"}):
        forzada = copy.deepcopy(p)
        forzada.update(fuerza)
        assert not elegible_automatico(forzada)


def test_pausas_intactas(tmp_path):
    loop = make_radar(tmp_path)
    ids = [crear_post(i, "ANALIZADA", analisis={"match_score": 95},
                      fecha_rel=rel, gen_prioridad=prioridad)
           for i, rel, prioridad in [(1, "1 h", 0), (2, "20 días", 0), (3, "1 h", 2)]]
    store.update_radar_state(analisis_pausado=True, generacion_pausada=True,
                             envios_pausados=True, backlog_activo=True)
    antes = store.get_radar_state()
    assert loop._siguiente_generacion() is None  # invariante: test_colas_dinamicas, pausa frena ambas colas.
    for pid in ids:
        assert not store.tomar_candidata(pid, colas.PRIORITARIA)  # invariante: pausa impide toma base.
        assert not store.tomar_candidata(pid, colas.FONDO)  # invariante: pausa impide toma base.
        assert store.get_post(pid)["estado"] == "ANALIZADA"  # invariante: sin transición automática.
    assert store.get_radar_state() == antes  # invariante: flags booleanos y pausas base intactos.
    if os.getenv("INVARIANT_CHECK") == "1":
        return
