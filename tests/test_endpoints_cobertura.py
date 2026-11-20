"""Endpoints que ningún otro test ejercitaba: canal SSE y reprocesar del Radar."""
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import server
from core.radar import RadarLoop


class _RequestFalso:
    def __init__(self):
        self.desconectado = False

    async def is_disconnected(self):
        return self.desconectado


@pytest.mark.asyncio
async def test_stream_saluda_reenvia_eventos_y_se_desuscribe():
    antes = list(server.subscribers)
    req = _RequestFalso()
    resp = await server.api_stream(req)
    assert resp.media_type == "text/event-stream"
    gen = resp.body_iterator
    try:
        assert "event: connected" in await gen.__anext__()
        cola = server.subscribers[-1]
        assert cola not in antes
        await cola.put('event: radar_status\ndata: {"estado": "PAUSA"}\n\n')
        assert await gen.__anext__() == 'event: radar_status\ndata: {"estado": "PAUSA"}\n\n'
    finally:
        await gen.aclose()
    assert server.subscribers == antes, "al cerrarse la conexión la cola se desuscribe"


@pytest.mark.asyncio
async def test_stream_corta_si_el_cliente_se_desconecto():
    antes = list(server.subscribers)
    req = _RequestFalso()
    resp = await server.api_stream(req)
    gen = resp.body_iterator
    await gen.__anext__()
    req.desconectado = True
    with pytest.raises(StopAsyncIteration):
        await gen.__anext__()
    assert server.subscribers == antes


def test_api_reprocesar_devuelve_cuantos_volvieron_a_cada_cola(monkeypatch):
    stub = AsyncMock(spec=RadarLoop)
    stub.reprocesar.return_value = {"triage": [1, 2, 3], "analisis": [4], "generacion": []}
    monkeypatch.setattr(server, "radar_loop", stub)
    r = TestClient(server.app).post("/api/radar/reprocesar")
    assert r.status_code == 200, r.text
    assert r.json() == {"triage": 3, "analisis": 1, "generacion": 0}
    stub.reprocesar.assert_awaited_once()
