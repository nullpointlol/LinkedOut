"""Ventanas sobrantes: los portales de prueba no abren Edge y el Radar no lleva --no-sandbox."""
import re
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

RAIZ = Path(__file__).resolve().parent.parent


@pytest.mark.asyncio
@pytest.mark.parametrize("valor,reanuda", [("1", False), (None, True), ("0", True)])
async def test_sin_radar_no_reanuda(monkeypatch, valor, reanuda):
    import server
    if valor is None:
        monkeypatch.delenv("LINKEDOUT_SIN_RADAR", raising=False)
    else:
        monkeypatch.setenv("LINKEDOUT_SIN_RADAR", valor)
    reanudar = AsyncMock()
    monkeypatch.setattr(server.radar_loop, "resume_if_needed", reanudar)
    monkeypatch.setattr(server.radar_loop, "stop_for_shutdown", AsyncMock())
    monkeypatch.setattr(server, "asegurar_anios_por_area", AsyncMock())
    async with server.lifespan(server.app):
        pass
    assert reanudar.called is reanuda


def test_edge_con_sandbox():
    fuente = (RAIZ / "core" / "linkedin_harvester.py").read_text(encoding="utf-8")
    llamada = fuente[fuente.index("launch_persistent_context("):]
    llamada = llamada[:llamada.index(")\n")]
    assert re.search(r"chromium_sandbox\s*=\s*True", llamada)
    assert "--no-sandbox" not in fuente
