import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from core.bridge import AntigravityBridge, DEFAULT_MODEL
from server import app


def test_bridge_default_is_gemini_38_flash_high():
    bridge = AntigravityBridge()
    assert bridge.model == "gemini-3.8-flash-high"
    assert DEFAULT_MODEL == "gemini-3.8-flash-high"


def test_bridge_build_command_includes_disable_slash_commands():
    bridge = AntigravityBridge()
    cmd = bridge._build_command("hola mundo", json_mode=True)

    assert "--disable-slash-commands" in cmd
    assert "--model" in cmd
    assert "gemini-3.8-flash-high" in cmd
    assert "--dangerously-skip-permissions" in cmd
    assert "--output-format" in cmd
    assert "json" in cmd


def test_bridge_set_active_model():
    bridge = AntigravityBridge()
    bridge.set_active_model("gemini-3.8-flash-medium")
    assert bridge.model == "gemini-3.8-flash-medium"
    assert bridge.effort == "medium"

    cmd = bridge._build_command("prueba", json_mode=False)
    assert "gemini-3.8-flash-medium" in cmd

    # Volver al default
    bridge.set_active_model("gemini-3.8-flash-high")
    assert bridge.model == "gemini-3.8-flash-high"
    assert bridge.effort == "high"


def test_api_model_config_endpoint_success():
    client = TestClient(app)

    # Cambiar a medium
    resp = client.post("/api/model-config", json={"model": "gemini-3.8-flash-medium"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "MODEL_UPDATED"
    assert data["model"] == "gemini-3.8-flash-medium"
    assert data["effort"] == "medium"

    # Verificar que GET /api/model-info refleje el cambio
    info_resp = client.get("/api/model-info")
    assert info_resp.status_code == 200
    info_data = info_resp.json()
    assert info_data["configured_model"] == "gemini-3.8-flash-medium"

    # Restaurar a high
    resp2 = client.post("/api/model-config", json={"model": "gemini-3.8-flash-high"})
    assert resp2.status_code == 200
    assert resp2.json()["model"] == "gemini-3.8-flash-high"


def test_api_model_config_endpoint_invalid_model():
    client = TestClient(app)
    resp = client.post("/api/model-config", json={"model": "modelo-inexistente-xyz"})
    assert resp.status_code == 400
    assert "Modelo no soportado" in resp.json()["detail"]


def test_api_model_config_allows_other_available_models():
    client = TestClient(app)
    # Claude o GPT si están disponibles en la CLI
    resp = client.post("/api/model-config", json={"model": "claude-sonnet-4-6"})
    assert resp.status_code == 200
    assert resp.json()["model"] == "claude-sonnet-4-6"

    # Restaurar al default obligatorio
    resp_restore = client.post("/api/model-config", json={"model": "gemini-3.8-flash-high"})
    assert resp_restore.status_code == 200
    assert resp_restore.json()["model"] == "gemini-3.8-flash-high"
