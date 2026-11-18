import pytest
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

from core.expander import CapabilityExpanderAgent
from core.strategist import StrategistAgent
from server import app


@pytest.fixture
def temp_expander(tmp_path):
    storage = tmp_path / "data" / "candidato" / "competencias_expandidas.json"
    expander = CapabilityExpanderAgent(storage_path=storage)
    return expander, storage


def test_get_expanded_competencies_creates_empty_file_if_absent(temp_expander):
    expander, storage = temp_expander
    assert not storage.exists()
    
    result = expander.get_expanded_competencies()
    assert result == []
    assert storage.exists()
    assert json.loads(storage.read_text(encoding="utf-8")) == []


def test_confirm_competencies_adds_and_deduplicates(temp_expander):
    expander, storage = temp_expander
    
    initial = [
        {
            "nombre": "Docker",
            "categoria": "Herramientas",
            "nivel": "Intermedio",
            "evidencia": "Despliegues locales y contenedores de prueba"
        }
    ]
    saved = expander.confirm_competencies(initial)
    assert len(saved) == 1
    assert saved[0]["nombre"] == "Docker"
    assert "created_at" in saved[0]
    
    # Agregar con diferente capitalización debe deduplicar y actualizar
    duplicate_update = [
        {
            "nombre": "docker",
            "categoria": "Infraestructura",
            "nivel": "Avanzado",
            "evidencia": "Despliegues de microservicios con Docker Compose"
        },
        {
            "nombre": "Kubernetes",
            "categoria": "Herramientas",
            "nivel": "Básico",
            "evidencia": "Minikube en entornos de prueba"
        }
    ]
    updated = expander.confirm_competencies(duplicate_update)
    assert len(updated) == 2
    
    # Verificar persistencia en disco
    on_disk = json.loads(storage.read_text(encoding="utf-8"))
    assert len(on_disk) == 2
    
    docker_item = next(item for item in on_disk if item["nombre"].lower() == "docker")
    assert docker_item["nivel"] == "Avanzado"
    assert docker_item["categoria"] == "Infraestructura"
    assert "Docker Compose" in docker_item["evidencia"]


def test_remove_competency_by_index_and_name(temp_expander):
    expander, storage = temp_expander
    
    items = [
        {"nombre": "Docker", "categoria": "Herramientas", "nivel": "Intermedio", "evidencia": "Locales"},
        {"nombre": "Kubernetes", "categoria": "Infraestructura", "nivel": "Básico", "evidencia": "Minikube"},
        {"nombre": "Terraform", "categoria": "Herramientas", "nivel": "Básico", "evidencia": "HCL"}
    ]
    expander.confirm_competencies(items)
    assert len(expander.get_expanded_competencies()) == 3
    
    # Eliminar por índice (índice 1 -> Kubernetes)
    assert expander.remove_competency(1) is True
    current = expander.get_expanded_competencies()
    assert len(current) == 2
    assert [c["nombre"] for c in current] == ["Docker", "Terraform"]
    
    # Eliminar por nombre (case-insensitive "terraform")
    assert expander.remove_competency("terraform") is True
    current = expander.get_expanded_competencies()
    assert len(current) == 1
    assert current[0]["nombre"] == "Docker"
    
    # Intentar eliminar inexistente
    assert expander.remove_competency("no_existe") is False
    assert expander.remove_competency(99) is False


@pytest.mark.asyncio
async def test_generate_discovery_questions_mocked_llm(temp_expander):
    expander, _ = temp_expander
    mock_bridge = AsyncMock()
    mock_bridge.run_prompt.return_value = {
        "preguntas": [
            {
                "keyword": "Docker",
                "pregunta": "¿Has empaquetado aplicaciones o levantado entornos locales con Docker?",
                "categoria_sugerida": "Herramientas",
                "sugerencia_evidencia": "Contenedores para pruebas locales"
            }
        ]
    }
    expander.bridge = mock_bridge
    
    questions = await expander.generate_discovery_questions("DevOps Engineer", ["Docker"])
    assert len(questions) == 1
    assert questions[0]["keyword"] == "Docker"
    assert "Docker" in questions[0]["pregunta"]
    assert questions[0]["categoria_sugerida"] == "Herramientas"
    assert mock_bridge.run_prompt.called


@pytest.mark.asyncio
async def test_generate_discovery_questions_deterministic_fallback(temp_expander):
    expander, _ = temp_expander
    mock_bridge = AsyncMock()
    mock_bridge.run_prompt.side_effect = RuntimeError("Bridge unavailable")
    expander.bridge = mock_bridge
    
    questions = await expander.generate_discovery_questions("Analista Funcional", ["Power BI", "AWS"])
    assert len(questions) == 2
    assert questions[0]["keyword"] == "Power BI"
    assert "¿" in questions[0]["pregunta"]
    assert questions[1]["keyword"] == "AWS"
    assert questions[1]["categoria_sugerida"] in ("Herramientas", "Infraestructura")


@pytest.mark.asyncio
async def test_strategist_integrates_expanded_competencies(tmp_path):
    storage = tmp_path / "competencias_expandidas.json"
    expander = CapabilityExpanderAgent(storage_path=storage)
    
    # Registrar competencia expandida que normalmente sería BRECHA_REAL
    expander.confirm_competencies([
        {
            "nombre": "Kubernetes",
            "categoria": "Infraestructura",
            "nivel": "Intermedio",
            "evidencia": "Administración de pods y despliegues en clusters locales"
        }
    ])
    
    strategist = StrategistAgent(expander=expander)
    
    offer_text = """
    Empresa: CloudOps Inc.
    Puesto: Cloud Platform Analyst
    Requisitos excluyentes:
    • Experiencia indispensable con Kubernetes en producción.
    """
    
    # Forzar ejecución determinística simulando fallo o bypass de agy
    result = strategist._deterministic_fallback(offer_text, strategist.load_candidate_experience(), expander.get_expanded_competencies())
    
    k8s_req = next((r for r in result["requisitos"] if "kubernetes" in r["nombre"].lower()), None)
    assert k8s_req is not None
    assert k8s_req["estado"] in ("MATCH_DIRECTO", "TRANSFERIBLE")
    assert k8s_req["approved"] is True
    assert "Administración de pods" in k8s_req["justificacion"] or "expandida" in k8s_req["justificacion"].lower()


def test_api_expand_endpoints(tmp_path, monkeypatch):
    test_storage = tmp_path / "api_competencias.json"
    mock_bridge = AsyncMock()
    mock_bridge.run_prompt.return_value = {
        "preguntas": [
            {
                "keyword": "Cypress",
                "pregunta": "¿Has implementado suites de test e2e con Cypress?",
                "categoria_sugerida": "Herramientas",
                "sugerencia_evidencia": "Automatización E2E en proyecto sandbox"
            },
            {
                "keyword": "Postman",
                "pregunta": "¿Has creado colecciones de prueba en Postman?",
                "categoria_sugerida": "Herramientas",
                "sugerencia_evidencia": "Colecciones y variables de entorno"
            }
        ]
    }
    expander_instance = CapabilityExpanderAgent(bridge=mock_bridge, storage_path=test_storage)
    
    # Monkeypatch singleton en server.py
    monkeypatch.setattr("server.expander_agent", expander_instance)
    
    client = TestClient(app)
    
    # 1. GET inicial vacío
    res_get = client.get("/api/candidato/expand")
    assert res_get.status_code == 200
    assert res_get.json() == {"competencias": []}
    
    # 2. POST /ask (preguntas socráticas)
    res_ask = client.post("/api/candidato/expand/ask", json={
        "target_role": "Analista QA",
        "missing_keywords": ["Cypress", "Postman"]
    })
    assert res_ask.status_code == 200
    preguntas = res_ask.json().get("preguntas", [])
    assert len(preguntas) == 2
    assert any(p["keyword"] == "Cypress" for p in preguntas)
    
    # 3. POST /confirm (guardar competencias)
    res_confirm = client.post("/api/candidato/expand/confirm", json={
        "competencias": [
            {
                "nombre": "Cypress",
                "categoria": "Herramientas",
                "nivel": "Intermedio",
                "evidencia": "Automatización E2E en proyecto sandbox"
            }
        ]
    })
    assert res_confirm.status_code == 200
    competencias = res_confirm.json().get("competencias", [])
    assert len(competencias) == 1
    assert competencias[0]["nombre"] == "Cypress"
    
    # 4. DELETE /api/candidato/expand/0
    res_del = client.delete("/api/candidato/expand/0")
    assert res_del.status_code == 200
    assert res_del.json().get("competencias") == []


def test_ui_html_and_js_elements_present():
    html_path = Path("static/index.html")
    js_path = Path("static/app.js")
    
    assert html_path.exists()
    assert js_path.exists()
    
    html_content = html_path.read_text(encoding="utf-8")
    js_content = js_path.read_text(encoding="utf-8")
    
    # Verificaciones de que los elementos obsoletos fueron purgados
    assert "modalExpandirPerfil" not in html_content
    assert "btnExplorarBrechas" not in html_content
    assert "cuestionarioPreguntas" not in html_content
