"""Tests para scoring-afinidad: reglas determinísticas sin IA e integración en ambos análisis."""
import copy
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from core.afinidad_reglas import aplicar_reglas, cargar_base_candidato, extraer_tokens_nucleo
from core.cv_guardas import es_requisito_logistico
from core.strategist import StrategistAgent, calculate_match_score_with_breakdown


def _cargar_fixtures():
    ruta = Path(__file__).resolve().parent / "fixtures" / "fixtures-casos.json"
    return json.loads(ruta.read_text(encoding="utf-8"))


def test_fixtures_criterios():
    fixtures = _cargar_fixtures()
    contexto = fixtures["_contexto"]
    areas = contexto["areas"]
    base_candidato = contexto["base_candidato_habilidades"]

    # invariante: Ofertas buenas de control (desarrollo web: frontend, fullstack, React):
    # ≤ 3 puntos de cambio y siguen ≥ 70 (base: match_score_actual en las fixtures).
    # Bases medidas: 97,6; 98,5; 100; 100; 95,2 (cómo se midieron: _contexto.nota_bases del JSON).
    controles = [
        ("control_frontend_sr", 97.6),
        ("control_desarrolladora_web", 98.5),
        ("control_fullstack_node", 100.0),
        ("control_react_ssr", 100.0),
        ("control_frontend_sr_cordoba", 95.2),
    ]

    for clave, base_medida in controles:
        caso = fixtures[clave]
        reqs_procesados = aplicar_reglas(
            caso["requisitos"],
            caso["puesto"],
            areas,
            base_candidato,
        )
        score, _ = calculate_match_score_with_breakdown(reqs_procesados)
        assert score >= 70.0, f"Control {clave} bajó de 70 (score={score})"
        delta = abs(score - base_medida)
        assert delta <= 3.01, f"Control {clave} tuvo cambio > 3 puntos (base={base_medida}, score={score}, delta={delta})"

    if os.environ.get("INVARIANT_CHECK") == "1":
        return

    # Defectos / Criterios de aceptación 1-4
    # 1. Brava (brava_salesforce_ba, hoy 75): nota ≤ 55.
    brava = fixtures["brava_salesforce_ba"]
    reqs_brava = aplicar_reglas(
        brava["requisitos"],
        brava["puesto"],
        areas,
        base_candidato,
    )
    score_brava, _ = calculate_match_score_with_breakdown(reqs_brava)
    assert score_brava <= 55.0, f"Brava esperado ≤ 55, obtenido {score_brava}"

    # 2. Cumbre (cumbre_it_pm, hoy 80,9): ≤ 65; «Experiencia en PM (+5 años…)» queda BRECHA_REAL dura.
    cumbre = fixtures["cumbre_it_pm"]
    reqs_cumbre = aplicar_reglas(
        cumbre["requisitos"],
        cumbre["puesto"],
        areas,
        base_candidato,
    )
    score_cumbre, _ = calculate_match_score_with_breakdown(reqs_cumbre)
    assert score_cumbre <= 65.0, f"Cumbre esperado ≤ 65, obtenido {score_cumbre}"
    pm_req = next(r for r in reqs_cumbre if "PM (+5 años" in r["nombre"])
    assert pm_req["estado"] == "BRECHA_REAL"
    assert pm_req.get("dura") == "anios"
    assert pm_req.get("approved") is False

    # 3. DevOps (devops_5_anios, hoy 93,3): < 70; «Experiencia DevOps o SRE (+5 años)» BRECHA_REAL dura.
    devops = fixtures["devops_5_anios"]
    reqs_devops = aplicar_reglas(
        devops["requisitos"],
        devops["puesto"],
        areas,
        base_candidato,
    )
    score_devops, _ = calculate_match_score_with_breakdown(reqs_devops)
    assert score_devops < 70.0, f"DevOps esperado < 70, obtenido {score_devops}"
    devops_req = next(r for r in reqs_devops if "DevOps o SRE (+5 años)" in r["nombre"])
    assert devops_req["estado"] == "BRECHA_REAL"
    assert devops_req.get("dura") == "anios"
    assert devops_req.get("approved") is False

    # 4. Faro (faro_logistica): requisitos de modalidad/ubicación/jornada/horario/incorporación
    # quedan logistico; quitarlos de la lista no cambia la nota (logística neutral).
    faro = fixtures["faro_logistica"]
    reqs_ruth = aplicar_reglas(
        faro["requisitos"],
        faro["puesto"],
        areas,
        base_candidato,
    )
    score_faro_con, _ = calculate_match_score_with_breakdown(reqs_ruth)

    # Verificar que los logísticos fueron marcados
    nombres_logisticos = [
        "Modalidad 100 por ciento remoto",
        "Ubicación en Argentina",
        "Jornada Full Time",
        "Horario España con flexibilidad",
        "Incorporación inmediata",
    ]
    for r in reqs_ruth:
        if r["nombre"] in nombres_logisticos:
            assert r.get("logistico") is True, f"Requisito logístico no marcado: {r['nombre']}"

    sin_logisticos = [r for r in reqs_ruth if not r.get("logistico")]
    score_faro_sin, _ = calculate_match_score_with_breakdown(sin_logisticos)
    assert round(score_faro_con, 1) == round(score_faro_sin, 1), (
        f"Logística no fue neutral en Faro: con={score_faro_con}, sin={score_faro_sin}"
    )


def test_manuales_e_idempotencia():
    fixtures = _cargar_fixtures()
    contexto = fixtures["_contexto"]
    areas = contexto["areas"]
    base_candidato = contexto["base_candidato_habilidades"]

    # invariante: Las decisiones manuales (aprendido, eliminatorio_usuario) mandan sobre las reglas.
    # Base: false explícito veta el descarte; true no degrada un MATCH_DIRECTO ni un TRANSFERIBLE.
    # Requisito con aprendido=true no cambia por las reglas.
    # Pura: devuelve copia profunda sin mutar entradas.
    reqs_manuales = [
        {
            "nombre": "Experiencia en Salesforce (+5 años)",
            "tipo": "REQUISITO_EXCLUYENTE",
            "estado": "MATCH_DIRECTO",
            "approved": True,
            "aprendido": True,
            "justificacion": "Aprendido confirmado por el usuario",
            "eliminatorio_usuario": False,
        },
        {
            "nombre": "Certificación Salesforce Certified Architect",
            "tipo": "REQUISITO_EXCLUYENTE",
            "estado": "TRANSFERIBLE",
            "approved": True,
            "eliminatorio_usuario": True,
            "justificacion": "Decisión manual de descarte",
        },
    ]

    reqs_copia = copy.deepcopy(reqs_manuales)
    salida = aplicar_reglas(
        reqs_manuales,
        puesto="Salesforce Senior Architect",
        areas=areas,
        base_candidato=base_candidato,
    )

    # Invariante: las entradas no fueron mutadas
    assert reqs_manuales == reqs_copia

    # Invariante: aprendido=true queda intacto
    assert salida[0]["aprendido"] is True
    assert salida[0]["estado"] == "MATCH_DIRECTO"
    assert salida[0]["approved"] is True
    assert salida[0]["justificacion"] == "Aprendido confirmado por el usuario"
    assert salida[0]["eliminatorio_usuario"] is False
    assert "dura" not in salida[0]

    # Invariante: eliminatorio_usuario se conserva
    assert salida[1]["eliminatorio_usuario"] is True

    if os.environ.get("INVARIANT_CHECK") == "1":
        return

    # Defectos: idempotencia, superposiciones, precedencia y casos límite
    # 1. Idempotencia: aplicar dos veces produce el mismo resultado exacto
    salida_segunda = aplicar_reglas(
        salida,
        puesto="Salesforce Senior Architect",
        areas=areas,
        base_candidato=base_candidato,
    )
    assert salida == salida_segunda

    # 2. Superposición con precedencia: anios > nucleo > cert
    req_superpuesto = [
        {
            "nombre": "Certificación Salesforce (+5 años en Salesforce)",
            "tipo": "REQUISITO_EXCLUYENTE",
            "estado": "TRANSFERIBLE",
            "approved": True,
            "justificacion": "Tiene todo",
        }
    ]
    res_sup = aplicar_reglas(req_superpuesto, puesto="Salesforce Dev", areas=areas, base_candidato=base_candidato)
    assert res_sup[0]["estado"] == "BRECHA_REAL"
    assert res_sup[0]["dura"] == "anios"  # anios gana sobre nucleo y cert

    # Nucleo sobre cert
    req_nucleo_cert = [
        {
            "nombre": "Certificación oficial de Salesforce",
            "tipo": "REQUISITO_EXCLUYENTE",
            "estado": "BRECHA_REAL",
            "approved": False,
            "justificacion": "Certificación requerida",
        }
    ]
    res_nc = aplicar_reglas(req_nucleo_cert, puesto="Salesforce Lead", areas=areas, base_candidato=base_candidato)
    assert res_nc[0]["dura"] == "nucleo"  # nucleo gana sobre cert

    # Certificación sola en brecha previa
    req_cert_sola = [
        {
            "nombre": "Certificación PMP o PMI-ACP",
            "tipo": "REQUISITO_DESEABLE",
            "estado": "BRECHA_REAL",
            "approved": False,
            "justificacion": "No posee PMP",
        }
    ]
    res_cert = aplicar_reglas(req_cert_sola, puesto="Analista Funcional", areas=areas, base_candidato=base_candidato)
    assert res_cert[0]["dura"] == "cert"

    # Certificación NO degrada un MATCH_DIRECTO ni un TRANSFERIBLE
    req_cert_md = [
        {
            "nombre": "Certificación SCRUM Master",
            "tipo": "REQUISITO_DESEABLE",
            "estado": "MATCH_DIRECTO",
            "approved": True,
            "justificacion": "Certificado",
        }
    ]
    res_cert_md = aplicar_reglas(req_cert_md, puesto="Analista Funcional", areas=areas, base_candidato=base_candidato)
    assert res_cert_md[0]["estado"] == "MATCH_DIRECTO"
    assert "dura" not in res_cert_md[0]

    # 3. Directo desconocido vs Transferible desconocido sin área en caché
    req_desconocidos = [
        {
            "nombre": "Experiencia 5 años en Bioinformática",
            "tipo": "REQUISITO_EXCLUYENTE",
            "estado": "MATCH_DIRECTO",
            "approved": True,
            "justificacion": "Conocimiento validado en laboratorio",
        },
        {
            "nombre": "Experiencia 5 años en Robótica Quirúrgica",
            "tipo": "REQUISITO_EXCLUYENTE",
            "estado": "TRANSFERIBLE",
            "approved": True,
            "justificacion": "Transferible de mecatrónica",
        },
    ]
    res_desc = aplicar_reglas(req_desconocidos, puesto="Ingeniero", areas=areas, base_candidato=base_candidato)
    # Directo desconocido se conserva
    assert res_desc[0]["estado"] == "MATCH_DIRECTO"
    assert "dura" not in res_desc[0]
    # Transferible desconocido pasa a brecha con justificación sin años en esa área
    assert res_desc[1]["estado"] == "BRECHA_REAL"
    assert res_desc[1]["dura"] == "anios"
    assert "sin años en esa área" in res_desc[1]["justificacion"]

    # 4. Responsabilidad con token de núcleo NO se degrada
    req_resp = [
        {
            "nombre": "Arquitectura y diseño de soluciones Salesforce",
            "tipo": "RESPONSABILIDAD",
            "estado": "TRANSFERIBLE",
            "approved": True,
            "justificacion": "Experiencia en arquitectura corporativa",
        }
    ]
    res_resp = aplicar_reglas(req_resp, puesto="Salesforce Architect", areas=areas, base_candidato=base_candidato)
    assert res_resp[0]["estado"] == "TRANSFERIBLE"
    assert res_resp[0]["approved"] is True
    assert "dura" not in res_resp[0]

    # 5. Tokens completos evitan falsos núcleos (p. ej. cloud no confunde cloudflare)
    # Si el puesto pide "Cloudflare Specialist", no debe marcar "cloud"
    tokens_cf = extraer_tokens_nucleo("Cloudflare Specialist", base_candidato="Manejo de Cloud y Linux")
    assert "cloudflare" in tokens_cf
    req_cloud = [
        {
            "nombre": "Experiencia en Cloud y servidores",
            "tipo": "REQUISITO_EXCLUYENTE",
            "estado": "TRANSFERIBLE",
            "approved": True,
            "justificacion": "Conoce cloud",
        }
    ]
    res_cf = aplicar_reglas(req_cloud, puesto="Cloudflare Specialist", areas=areas, base_candidato="Manejo de Cloud")
    assert res_cf[0]["estado"] == "TRANSFERIBLE"


def test_integracion_aprendizaje_antes_de_reglas(tmp_path, monkeypatch):
    # invariante: el desglose conserva su estructura y no falla con entradas vacías
    score_vacio, desglose_vacio = calculate_match_score_with_breakdown([])
    assert score_vacio == 0.0
    assert "brechas" in desglose_vacio
    assert "transferibles" in desglose_vacio

    if os.environ.get("INVARIANT_CHECK") == "1":
        return

    monkeypatch.setattr("core.paths.DATA_DIR", tmp_path)
    areas_mock = [
        {"area": "análisis técnico funcional", "anios": "7"},
        {"area": "DevOps", "anios": "1"},
    ]
    monkeypatch.setattr("core.perfil_areas.anios_por_area_en_cache", lambda candidato_dir=None: areas_mock)

    # 2. analyze_offer integra aprendizaje antes de reglas
    # Bridge mock que devuelve requisitos iniciales
    mock_bridge = AsyncMock()
    mock_bridge.run_prompt = AsyncMock(
        return_value={
            "empresa": "Brava",
            "puesto": "Salesforce Business Analyst",
            "dolores_empresa": ["Mejorar procesos"],
            "requisitos": [
                {
                    "nombre": "Experiencia +5 años en DevOps",
                    "tipo": "REQUISITO_EXCLUYENTE",
                    "estado": "TRANSFERIBLE",
                    "approved": True,
                    "justificacion": "Sabe algo de devops",
                },
                {
                    "nombre": "Ubicación 100% remota",
                    "tipo": "REQUISITO_EXCLUYENTE",
                    "estado": "MATCH_DIRECTO",
                    "approved": True,
                    "justificacion": "Remoto",
                },
            ],
        }
    )

    estratega_bridge = StrategistAgent(bridge=mock_bridge)
    # Correr analyze_offer
    import asyncio
    res_analisis = asyncio.run(estratega_bridge.analyze_offer("Oferta de prueba", effort="low"))

    assert "requisitos" in res_analisis
    reqs_an = res_analisis["requisitos"]
    devops_an = next(r for r in reqs_an if "DevOps" in r["nombre"])
    # 5 años en DevOps con 1 año en el perfil debe degradar a BRECHA_REAL con dura=anios
    assert devops_an["estado"] == "BRECHA_REAL"
    assert devops_an.get("dura") == "anios"
    assert devops_an.get("approved") is False

    remoto_an = next(r for r in reqs_an if "remota" in r["nombre"].lower())
    assert remoto_an.get("logistico") is True


def test_brecha_de_nucleo_explica_su_motivo():
    """El tooltip de «Brecha fuerte» mostraba la justificación vieja del modelo («…transferible»)."""
    from core.afinidad_reglas import aplicar_reglas
    reqs = [{"nombre": "Stack MERN", "estado": "TRANSFERIBLE", "tipo": "REQUISITO_EXCLUYENTE",
             "approved": True, "justificacion": "React transferible desde Java"}]
    out = aplicar_reglas(reqs, puesto="Senior MERN Stack Developer", areas=[], base_candidato="Java SQL soporte")
    assert out[0]["estado"] == "BRECHA_REAL" and out[0]["dura"] == "nucleo"
    assert out[0]["justificacion"] == "El puesto es de «mern» y no figura en el perfil"
    assert aplicar_reglas(out, puesto="Senior MERN Stack Developer", areas=[], base_candidato="Java SQL soporte") == out
