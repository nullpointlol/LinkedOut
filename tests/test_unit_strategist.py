"""Unitarios de `core/strategist.py`: puntaje, esfuerzo del bridge y normalización de la respuesta simulada."""
from unittest.mock import AsyncMock

import pytest

from core import strategist
from core.strategist import StrategistAgent, calculate_match_score_with_breakdown


class _Expansor:
    def __init__(self, competencias=None):
        self.competencias = competencias or []

    def get_expanded_competencies(self):
        return list(self.competencias)


class _Bridge:
    def __init__(self, respuesta=None, effort="low"):
        self.effort = effort
        self.run_prompt = AsyncMock(return_value=respuesta)


@pytest.fixture(autouse=True)
def _sin_aprendizajes(monkeypatch, tmp_path):
    """El Estratega lee aprendizajes del candidato: en estos tests, un archivo propio y vacío."""
    import core.aprendizajes as ap
    monkeypatch.setattr(ap, "APRENDIZAJES_FILE", tmp_path / "aprendizajes.json")


def test_estado_desconocido_aprobado_suma_y_no_aprobado_es_brecha():
    """Un requisito con estado no estándar suma si está aprobado; si no, es brecha (con «dura»)."""
    score, desglose = calculate_match_score_with_breakdown([
        {"nombre": "Python", "estado": "OTRO", "approved": True},
        {"nombre": "Kubernetes", "estado": "PENDIENTE", "approved": False, "dura": "excluyente"},
    ])
    assert score == 50.0
    assert desglose["brechas"] == [{"nombre": "Kubernetes", "puntos_deducidos": 50.0, "dura": "excluyente"}]


def test_esfuerzo_pedido_se_impone_al_bridge():
    """Si se pide un esfuerzo distinto del que trae el bridge, se le cambia al bridge."""
    b = _Bridge(effort="high")
    agente = StrategistAgent(bridge=b, expander=_Expansor(), effort="medium")
    assert b.effort == "medium"
    assert agente.effort == "medium"


@pytest.mark.asyncio
async def test_respuesta_sin_tipo_se_completa_y_competencias_van_al_prompt():
    """Las competencias expandidas van al prompt; los requisitos sin tipo reciben uno según nombre y
    estado, y los que no traen cita no se usan como evidencia de descarte."""
    respuesta = {"empresa": "Acme", "puesto": "Analista", "requisitos": [
        {"nombre": "Relevamiento de procesos", "estado": "MATCH_DIRECTO", "texto": ""},
        {"nombre": "Docker", "estado": "TRANSFERIBLE", "texto": "   "},
    ]}
    b = _Bridge(respuesta)
    expansor = _Expansor([{"nombre": "Terraform", "categoria": "Infraestructura", "nivel": "Básico",
                           "evidencia": "Laboratorio personal"}])
    res = await StrategistAgent(bridge=b, expander=expansor).analyze_offer("Buscamos analista en Acme")
    prompt = b.run_prompt.call_args.args[0]
    assert "- Terraform (Infraestructura, Básico): Laboratorio personal" in prompt
    tipos = {r["nombre"]: r["tipo"] for r in res["requisitos"]}
    assert tipos == {"Relevamiento de procesos": "RESPONSABILIDAD", "Docker": "REQUISITO_DESEABLE"}
    assert "no_apto" not in res
    assert res["es_oferta"] is True


@pytest.mark.asyncio
async def test_respuesta_sin_requisitos_falla_el_analisis(caplog):
    """Si el modelo no devuelve requisitos se registra el error y el análisis falla (sin IA no se analiza)."""
    b = _Bridge({"empresa": "Acme"})
    with pytest.raises(strategist.AnalisisSinIA):
        await StrategistAgent(bridge=b, expander=_Expansor()).analyze_offer("Analista\nAcme\n• Python")
    assert "sin estructura de requisitos" in caplog.text


@pytest.mark.asyncio
async def test_decision_de_descarte_del_usuario_se_aplica_y_no_la_pisa_el_modelo():
    """Lo que el usuario marcó como eliminatorio en otra oferta se aplica al requisito equivalente; el
    modelo no puede atribuirse esa decisión (sus marcas de usuario se descartan)."""
    import core.aprendizajes as ap
    ap.registrar_aprendizaje("Experiencia en gastronomía", "BRECHA_REAL", eliminatorio=True)
    b = _Bridge({"empresa": "Acme", "puesto": "Mozo", "requisitos": [
        {"nombre": "Experiencia en gastronomia", "tipo": "REQUISITO_EXCLUYENTE", "estado": "MATCH_DIRECTO",
         "texto": "Experiencia en gastronomía", "eliminatorio_usuario": False},
        {"nombre": "Atención al público", "tipo": "REQUISITO_EXCLUYENTE", "estado": "MATCH_DIRECTO",
         "eliminatorio_usuario": True, "aprendido": True},
    ]})
    res = await StrategistAgent(bridge=b, expander=_Expansor()).analyze_offer(
        "Buscamos mozo. Experiencia en gastronomía.")
    gastro, atencion = res["requisitos"]
    assert (gastro["estado"], gastro["eliminatorio_usuario"], gastro["aprendido"]) == ("BRECHA_REAL", True, True)
    assert "eliminatorio_usuario" not in atencion and "aprendido" not in atencion
