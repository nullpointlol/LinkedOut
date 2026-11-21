"""Tests para verificar el selector unificado de modelos de Antigravity CLI y propagación de --effort."""
import pytest
from fastapi.testclient import TestClient
from server import app
from core.bridge import AntigravityBridge


@pytest.fixture
def client():
    return TestClient(app)


def test_build_command_includes_effort_flag():
    """_build_command debe incluir '--effort <effort>' cuando self.effort está definido."""
    bridge = AntigravityBridge(model="gemini-3.8-flash-high", effort="high")
    cmd = bridge._build_command("test prompt", json_mode=True)

    assert "--model" in cmd
    model_idx = cmd.index("--model")
    assert cmd[model_idx + 1] == "gemini-3.8-flash-high"

    assert "--effort" in cmd
    effort_idx = cmd.index("--effort")
    assert cmd[effort_idx + 1] == "high"


def test_build_command_respects_custom_effort():
    """_build_command debe propagar un nivel de esfuerzo personalizado como 'low' o 'medium' en modelos compatibles."""
    bridge = AntigravityBridge(model="gemini-3.8-flash-high", effort="medium")
    cmd = bridge._build_command("otro prompt", json_mode=False)

    assert "--effort" in cmd
    assert cmd[cmd.index("--effort") + 1] == "medium"

    # En modelos que no soportan --effort en el CLI (ej: Claude), no debe enviarse flag --effort
    bridge_claude = AntigravityBridge(model="claude-sonnet-4-6", effort="medium")
    cmd_claude = bridge_claude._build_command("otro prompt", json_mode=False)
    assert "--effort" not in cmd_claude


def test_get_model_info_returns_models_details():
    """get_model_info debe incluir models_details con id, display_name y effort para cada modelo."""
    bridge = AntigravityBridge()
    info = bridge.get_model_info()

    assert "models_details" in info
    assert isinstance(info["models_details"], list)
    assert len(info["models_details"]) > 0

    first = info["models_details"][0]
    assert "id" in first
    assert "display_name" in first
    assert "effort" in first


def test_api_set_model_config_updates_model_and_effort(client):
    """POST /api/model-config debe actualizar el modelo activo y el effort globalmente."""
    bridge_pre = AntigravityBridge()
    original_model = bridge_pre.model
    original_effort = bridge_pre.effort

    try:
        response = client.post(
            "/api/model-config",
            json={"model": "claude-sonnet-4-6", "effort": "high"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "MODEL_UPDATED"
        assert data["model"] == "claude-sonnet-4-6"
        assert data["effort"] == "high"
        assert "Claude Sonnet" in data["display_name"]

        # Verificar que una nueva instancia de bridge tome el modelo y effort actualizados
        bridge_new = AntigravityBridge()
        assert bridge_new.model == "claude-sonnet-4-6"
        assert bridge_new.effort == "high"

        cmd = bridge_new._build_command("p")
        # Claude no lleva flag --effort en el comando generado para evitar error en agy CLI
        assert "--effort" not in cmd

        # Actualizar a un modelo Gemini con effort compatible
        res_gemini = client.post(
            "/api/model-config",
            json={"model": "gemini-3.8-flash-low", "effort": "low"}
        )
        assert res_gemini.status_code == 200
        bridge_gemini = AntigravityBridge()
        cmd_gemini = bridge_gemini._build_command("p")
        assert cmd_gemini[cmd_gemini.index("--effort") + 1] == "low"

    finally:
        # Restaurar estado original
        client.post(
            "/api/model-config",
            json={"model": original_model, "effort": original_effort}
        )
