"""Pruebas de la fórmula compartida de afinidad, topes por brechas duras y logística."""
import json
import os
from pathlib import Path
import pytest

from core import strategist


def _cargar_controles():
    # Las fixtures versionadas, no un archivo de trabajo sin versionar (_ref/)
    ruta = Path(__file__).resolve().parent / "fixtures" / "fixtures-casos.json"
    data = json.loads(ruta.read_text(encoding="utf-8"))
    return [
        (k, data[k]["match_score_actual"], data[k]["requisitos"])
        for k in [
            "control_frontend_sr",
            "control_desarrolladora_web",
            "control_fullstack_node",
            "control_react_ssr",
            "control_frontend_sr_cordoba",
        ]
    ]


def test_formula_y_tope_activo():
    controles = _cargar_controles()
    assert len(controles) == 5
    for clave, base_score, reqs in controles:
        score = strategist.calculate_match_score(reqs)
        # invariante: ofertas buenas de control siguen >= 70 y cambio <= 3 puntos
        assert score >= 70.0
        assert abs(score - base_score) <= 3.0

    if os.getenv("INVARIANT_CHECK") == "1":
        return

    # 1. Transferibles: approved=True vale 0.70, approved=False vale 0.40, MATCH_DIRECTO 1.0
    t_app = [{"nombre": "Docker", "estado": "TRANSFERIBLE", "approved": True}]
    assert strategist.calculate_match_score(t_app) == 70.0
    t_no_app = [{"nombre": "Docker", "estado": "TRANSFERIBLE", "approved": False}]
    assert strategist.calculate_match_score(t_no_app) == 40.0
    directo = [{"nombre": "Python", "estado": "MATCH_DIRECTO", "approved": True}]
    assert strategist.calculate_match_score(directo) == 100.0

    # 2. Peso duro vs común
    req_dura_excl = {
        "nombre": "Experiencia DevOps (+5 años)",
        "estado": "BRECHA_REAL",
        "tipo": "REQUISITO_EXCLUYENTE",
        "dura": "anios",
    }
    assert strategist.peso_requisito(req_dura_excl) == 2.0

    req_comun_excl = {
        "nombre": "Experiencia Linux",
        "estado": "BRECHA_REAL",
        "tipo": "REQUISITO_EXCLUYENTE",
    }
    assert strategist.peso_requisito(req_comun_excl) == 1.5

    req_dura_des = {
        "nombre": "Certificación PMP",
        "estado": "BRECHA_REAL",
        "tipo": "REQUISITO_DESEABLE",
        "dura": "cert",
    }
    # Deseable dura conserva su peso de categoría (0.5), pero cuenta en D para el tope
    assert strategist.peso_requisito(req_dura_des) == 0.5

    # 3. Mezcla y tope activo: tope = max(0, 100 - 12 * D)
    # Con 20 directos (peso 20) y 1 brecha dura excluyente (peso 2, dura="anios"):
    # promedio = (20 / 22) * 100 = 90.9%
    # D = 1 -> tope = 100 - 12 = 88.0. Score limitado a 88.0
    tags_20_1 = [{"nombre": f"Directo {i}", "estado": "MATCH_DIRECTO"} for i in range(20)] + [req_dura_excl]
    score_tope, desglose_tope = strategist.calculate_match_score_with_breakdown(tags_20_1)
    assert score_tope == 88.0
    assert desglose_tope["puntos_faltantes"] == 12.0

    # Deseable dura también cuenta en D para el tope
    # 10 directos (peso 10) + 2 deseables duras (peso 0.5 * 2 = 1.0) -> promedio = 10 / 11 = 90.9%
    # D = 2 -> tope = 100 - 12 * 2 = 76.0. Score limitado a 76.0
    tags_des = [{"nombre": f"Directo {i}", "estado": "MATCH_DIRECTO"} for i in range(10)] + [req_dura_des] * 2
    score_des, desglose_des = strategist.calculate_match_score_with_breakdown(tags_des)
    assert score_des == 76.0
    assert desglose_des["puntos_faltantes"] == 24.0

    # 4. Marca dura inactiva: en MATCH_DIRECTO o TRANSFERIBLE no pesa 2 ni limita la nota
    inactivo_dir = {
        "nombre": "Experiencia DevOps (+5 años)",
        "estado": "MATCH_DIRECTO",
        "tipo": "REQUISITO_EXCLUYENTE",
        "dura": "anios",
    }
    assert strategist.peso_requisito(inactivo_dir) == 1.0
    assert strategist.calculate_match_score([inactivo_dir]) == 100.0

    inactivo_trans = {
        "nombre": "Experiencia DevOps (+5 años)",
        "estado": "TRANSFERIBLE",
        "tipo": "REQUISITO_EXCLUYENTE",
        "dura": "anios",
        "approved": True,
    }
    assert strategist.peso_requisito(inactivo_trans) == 1.0
    assert strategist.calculate_match_score([inactivo_trans]) == 70.0

    # Al volver a BRECHA_REAL vuelve a contar en peso y tope
    reactivado = dict(inactivo_dir, estado="BRECHA_REAL")
    assert strategist.peso_requisito(reactivado) == 2.0
    score_reac, _ = strategist.calculate_match_score_with_breakdown([
        {"nombre": "SQL", "estado": "MATCH_DIRECTO"},
        reactivado,
    ])
    # 1 / (1 + 2) = 33.3%
    assert score_reac == 33.3

    # 5. Desglose: en brechas añadir dura con su valor solo si existe
    tags_desglose = [
        {"nombre": "SQL", "estado": "MATCH_DIRECTO"},
        req_comun_excl,
        req_dura_excl,
    ]
    _, desglose_mix = strategist.calculate_match_score_with_breakdown(tags_desglose)
    brechas_dict = {b["nombre"]: b for b in desglose_mix["brechas"]}
    assert "dura" not in brechas_dict[req_comun_excl["nombre"]]
    assert brechas_dict[req_dura_excl["nombre"]]["dura"] == "anios"


def test_logistica_y_limites():
    req_match = {"nombre": "Python", "estado": "MATCH_DIRECTO"}
    req_log = {"nombre": "Modalidad 100% remota", "estado": "MATCH_DIRECTO"}
    # invariante: logística base pesa 0 y no cambia nota si se retira
    assert strategist.peso_requisito(req_log) == 0.0
    assert strategist.calculate_match_score([req_match, req_log]) == strategist.calculate_match_score([req_match])

    if os.getenv("INVARIANT_CHECK") == "1":
        return

    # 1. Logística pesa 0 antes de evaluar dura
    req_log_dura = {
        "nombre": "Modalidad remota LATAM",
        "logistico": True,
        "estado": "BRECHA_REAL",
        "tipo": "REQUISITO_EXCLUYENTE",
        "dura": "anios",
    }
    assert strategist.peso_requisito(req_log_dura) == 0.0

    # Logística no aporta al tope D ni al desglose como penalización
    tags_log = [req_match, req_log_dura]
    score_log, desglose_log = strategist.calculate_match_score_with_breakdown(tags_log)
    assert score_log == 100.0
    assert desglose_log["puntos_faltantes"] == 0.0

    # Flag logistico: True explícito en requisito con nombre no detectado por regex
    req_flag_log = {
        "nombre": "Requisito especial de logística",
        "logistico": True,
        "estado": "BRECHA_REAL",
        "tipo": "REQUISITO_EXCLUYENTE",
    }
    assert strategist.peso_requisito(req_flag_log) == 0.0

    # 2. 9 duras y más: score no negativo
    # Con 9 duras, tope = max(0, 100 - 12 * 9) = max(0, -8) = 0.0
    tags_9 = [
        {"nombre": f"Match {i}", "estado": "MATCH_DIRECTO"} for i in range(5)
    ] + [
        {
            "nombre": f"Dura {i}",
            "estado": "BRECHA_REAL",
            "tipo": "REQUISITO_EXCLUYENTE",
            "dura": "nucleo",
        }
        for i in range(9)
    ]
    score_9, desglose_9 = strategist.calculate_match_score_with_breakdown(tags_9)
    assert score_9 == 0.0
    assert desglose_9["puntos_faltantes"] == 100.0

    # Con 10 duras, tope = max(0, 100 - 120) = 0.0 (nunca negativo)
    tags_10 = tags_9 + [{
        "nombre": "Dura extra",
        "estado": "BRECHA_REAL",
        "tipo": "REQUISITO_DESEABLE",
        "dura": "cert",
    }]
    score_10, desglose_10 = strategist.calculate_match_score_with_breakdown(tags_10)
    assert score_10 == 0.0
    assert desglose_10["puntos_faltantes"] == 100.0

    # 3. Límites: empty, 100%, 0%
    empty_score, empty_desglose = strategist.calculate_match_score_with_breakdown([])
    assert empty_score == 0.0
    assert empty_desglose["puntos_faltantes"] == 100.0
    assert empty_desglose["brechas"] == []
    assert empty_desglose["transferibles"] == []

    full_score, full_desglose = strategist.calculate_match_score_with_breakdown([req_match])
    assert full_score == 100.0
    assert full_desglose["puntos_faltantes"] == 0.0
    assert full_desglose["camino_al_100"] == "Perfil 100% alineado con los requerimientos."
