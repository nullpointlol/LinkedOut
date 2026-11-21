"""Un pedido más largo que la línea de comandos de Windows va por stdin (WinError 206 del 15/11)."""
import json
from unittest.mock import AsyncMock, patch

import pytest

from core import bridge as modulo_bridge
from core.bridge import AntigravityBridge, LARGO_MAX_PEDIDO_EN_LINEA, mensaje_stdin, resultado_stream

SALIDA = "\n".join([
    json.dumps({"event": "init", "conversation_id": "c1", "init": {"model": "m"}}),
    json.dumps({"event": "step_update", "step_update": {"text_delta": "{\"ok\": 1}"}}),
    json.dumps({"event": "result", "result": {"conversation_id": "c1", "status": "SUCCESS",
                                               "response": "{\"ok\": 1}", "usage": {"total_tokens": 9}}}),
])


def _bridge(tmp_path):
    agy = tmp_path / "agy.exe"
    agy.write_bytes(b"")
    b = AntigravityBridge()
    b.agy_path = agy
    return b


@pytest.mark.asyncio
async def test_un_pedido_largo_va_por_stdin_y_se_lee_igual(tmp_path):
    proc = AsyncMock()
    proc.communicate.return_value = (SALIDA.encode("utf-8"), b"")
    proc.returncode = 0
    pedido = "Analizá esta oferta. " + "á" * (LARGO_MAX_PEDIDO_EN_LINEA + 10)
    with patch.object(modulo_bridge, "_crear_proceso", return_value=proc) as crear:
        respuesta = await _bridge(tmp_path).run_prompt(pedido, json_mode=True)
    args, kwargs = crear.call_args
    assert pedido not in args and "-p" not in args
    assert args[args.index("--input-format") + 1] == "stream-json"
    assert kwargs["stdin"] == modulo_bridge.asyncio.subprocess.PIPE
    enviado = json.loads(proc.communicate.call_args[0][0].decode("utf-8"))
    assert enviado == {"event": "user", "message": {"content": pedido}}
    assert respuesta == {"ok": 1}


@pytest.mark.asyncio
async def test_un_pedido_corto_sigue_en_la_linea_de_comandos(tmp_path):
    proc = AsyncMock()
    proc.communicate.return_value = (b'{"status": "SUCCESS", "response": "ok"}', b"")
    proc.returncode = 0
    with patch.object(modulo_bridge, "_crear_proceso", return_value=proc) as crear:
        await _bridge(tmp_path).run_prompt("corto", json_mode=False)
    args, kwargs = crear.call_args
    assert args[-2:] == ("-p", "corto") and "stdin" not in kwargs
    assert proc.communicate.call_args[0] == ()


def test_resultado_stream_en_modo_texto_y_con_error():
    assert resultado_stream(SALIDA, json_mode=False) == "{\"ok\": 1}"
    error = json.dumps({"event": "result", "result": {"status": "ERROR", "error": "UNAVAILABLE (code 503)"}})
    assert json.loads(resultado_stream(error, json_mode=False))["status"] == "ERROR"
    assert mensaje_stdin("hola").endswith(b"\n")
