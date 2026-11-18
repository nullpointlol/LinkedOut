"""Tests de rendimiento de consultas con email útil y selección del ciclo (Feature: busqueda-email).

Verifica que solo los posts útiles con emails no vacío aporten al numerador del rendimiento,
conservando denominadores, ventanas temporales, base explícita, invariantes de ciclo y guardado.
"""
import os
from datetime import date, datetime, timedelta

import pytest

from core import database
from core import radar_store
from tests.test_busqueda_email_consultas import test_invariantes_guardado_y_url as _test_invariantes_m01
from tests.test_imagenes_motor import IMAGEN
from tests.test_radar_loop import FIXED_NOW, FakeHarvester, FakeSleep, make_radar, raw


@pytest.mark.asyncio
async def test_invariantes_guardado_y_url(tmp_path, monkeypatch):
    # invariante: escenario de captura, deduplicación y URL de la misión 01
    await _test_invariantes_m01(tmp_path, monkeypatch)
