"""Unitarios de `core/strategist.py`: puntaje con estados desconocidos, esfuerzo del bridge,
normalización de la respuesta del modelo (simulado) y fallback con competencias expandidas."""
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
async def test_respuesta_sin_requisitos_usa_el_fallback(caplog):
    """Si el modelo no devuelve requisitos se registra el error y se usa el análisis determinístico."""
    b = _Bridge({"empresa": "Acme"})
    res = await StrategistAgent(bridge=b, expander=_Expansor()).analyze_offer("Analista\nAcme\n• Python")
    assert res["analisis_fallback"] is True
    assert "sin estructura de requisitos" in caplog.text


def test_fallback_ignora_competencia_sin_nombre_en_las_vinetas():
    """Al recorrer las viñetas, una competencia expandida sin nombre no marca nada como validado."""
    agente = StrategistAgent(bridge=_Bridge(), expander=_Expansor())
    oferta = ("Analista\nAcme\nRequisitos\n• Experiencia con Kubernetes en producción\n"
              "• Manejo de Terraform avanzado")
    res = agente._deterministic_fallback(oferta, "", [{"nombre": ""}, {"nombre": "Terraform", "evidencia": "Lab"}])
    estados = {r["nombre"]: r["estado"] for r in res["requisitos"]}
    assert estados["Experiencia con Kubernetes en producción"] == "BRECHA_REAL"
    assert estados["Manejo de Terraform avanzado"] == "MATCH_DIRECTO"


def test_fallback_catalogo_no_valida_brechas_con_competencia_sin_nombre(monkeypatch, tmp_path):
    """Una competencia expandida sin nombre no debe convertir Kubernetes en MATCH_DIRECTO."""
    from core import cv_guardas
    # Kubernetes es un conocimiento ausente del candidato de prueba (Mi perfil), no del código
    ausentes = tmp_path / "conocimientos_ausentes.json"
    ausentes.write_text('{"conocimientos": ["Kubernetes"]}', encoding="utf-8")
    monkeypatch.setattr(cv_guardas, "RUTA_CONOCIMIENTOS_AUSENTES", ausentes)
    agente = StrategistAgent(bridge=_Bridge(), expander=_Expansor())
    res = agente._deterministic_fallback("Analista\nAcme\nUsamos Kubernetes.", "", [{"nombre": ""}])
    k8s = next(r for r in res["requisitos"] if r["nombre"] == "Kubernetes")
    assert k8s["estado"] == "BRECHA_REAL"


def test_fallback_con_competencias_expandidas_en_catalogo_y_extra():
    """Con pocas viñetas, el catálogo marca como MATCH_DIRECTO lo que el candidato validó, y suma las
    competencias expandidas que la oferta menciona."""
    agente = StrategistAgent(bridge=_Bridge(), expander=_Expansor())
    competencias = [
        {"nombre": "Docker", "evidencia": "Despliegues locales"},
        {"nombre": "Terraform", "evidencia": ""},
    ]
    oferta = "Analista\nAcme\nSe valora mucho experiencia previa con Docker y Terraform."
    res = agente._deterministic_fallback(oferta, "experiencia", competencias)
    por_nombre = {r["nombre"]: r for r in res["requisitos"]}
    assert por_nombre["Docker"]["estado"] == "MATCH_DIRECTO"
    assert por_nombre["Docker"]["justificacion"] == "Competencia expandida validada: Despliegues locales"
    assert por_nombre["Terraform"]["justificacion"] == "Competencia expandida validada (Terraform)"
    assert res["empresa"] == "Acme"


def test_fallback_catalogo_con_competencia_sin_evidencia():
    """Una competencia del catálogo validada sin evidencia se justifica con su nombre."""
    agente = StrategistAgent(bridge=_Bridge(), expander=_Expansor())
    res = agente._deterministic_fallback("Analista\nAcme\nUsamos Linux.", "", [{"nombre": "Linux"}])
    linux = next(r for r in res["requisitos"] if r["nombre"] == "Linux")
    assert linux["justificacion"] == "Competencia expandida validada (Linux)"
    assert strategist.calculate_match_score([linux]) == 100.0


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
