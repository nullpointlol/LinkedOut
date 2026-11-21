from unittest.mock import AsyncMock
import pytest
from core.bridge import AntigravityBridge, extract_json_payload

def test_extract_json_payload_from_markdown():
    raw_response = '```json\n{"nombre": "Empresa X", "score": 95}\n```'
    parsed = extract_json_payload(raw_response)
    assert parsed == {"nombre": "Empresa X", "score": 95}

def test_extract_json_payload_from_agy_wrapper():
    wrapper = '{"conversation_id": "123", "status": "SUCCESS", "response": "```json\\n{\\"ok\\": true}\\n```"}'
    parsed = extract_json_payload(wrapper)
    assert parsed == {"ok": True}

def test_extract_json_payload_plain_json():
    plain = '{"status": "READY", "items": [1, 2, 3]}'
    parsed = extract_json_payload(plain)
    assert parsed == {"status": "READY", "items": [1, 2, 3]}

def test_bridge_detects_binary():
    bridge = AntigravityBridge()
    assert bridge.agy_path is not None
    assert bridge.agy_path.exists()

@pytest.mark.asyncio
async def test_bridge_run_prompt(monkeypatch):
    bridge = AntigravityBridge()
    prompt = 'Responde en JSON: {"message": "pong"}'
    mock_ejecutar = AsyncMock(return_value={"message": "pong"})
    monkeypatch.setattr(AntigravityBridge, "_ejecutar_prompt", mock_ejecutar)
    # Test execution with fallback / fast response
    result = await bridge.run_prompt(prompt, json_mode=True)
    assert mock_ejecutar.call_count == 1
    assert isinstance(result, dict)
    assert "message" in result or "response" in result or "raw" in result
