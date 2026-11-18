"""Tests de consultas orientadas a contacto por email y roles (Feature: busqueda-email).

Verifica proporciones del catálogo, conservación del ritmo, URLs generadas, rotación de ciclos
y reglas de guardado según spec y plan.
"""
import os
import re
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest

from core.linkedin_harvester import HarvestResult
from core.linkedin_parser import build_search_url
from core.radar import DEFAULT_QUERIES
from core.radar_store import list_posts
from tests.test_imagenes_motor import IMAGEN
from tests.test_radar_loop import FIXED_NOW, FakeHarvester, FakeSleep, make_radar, raw


def _normalizar(texto: str) -> str:
    """Normaliza tildes y minúsculas para análisis en tests."""
    res = texto.lower()
    for con_tilde, sin_tilde in (("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u")):
        res = res.replace(con_tilde, sin_tilde)
    return res


def test_catalogo_amplio():
    """Catálogo (11/11): las búsquedas activas de busquedas.json, sin duplicados ni URLs."""
    from core.radar_consultas import consultas_activas
    assert DEFAULT_QUERIES == consultas_activas()  # todas, en el orden del archivo
    assert len(set(DEFAULT_QUERIES)) == len(DEFAULT_QUERIES)
    for q in [_normalizar(q) for q in DEFAULT_QUERIES]:
        assert not any(x in q for x in ("http://", "https://", "forms.gle"))


@pytest.mark.asyncio
async def test_invariantes_ciclo_pausas_y_rotacion(tmp_path, monkeypatch):
    """Creador, 12/11: cada vuelta lee el feed y después todas las búsquedas en el orden del archivo, sin agrupar
    por idioma, con la pausa entre lecturas del perfil (3–6 min)."""
    sleep = FakeSleep()
    h = FakeHarvester()
    radar = make_radar(tmp_path, harvester=h, sleep=sleep, queries=("a", "b", "c", "d"), queries_en=("c", "d"))
    try:
        await radar.run_cycle()
        assert h.calls == ["feed", "a", "b", "c", "d"]
        assert len(sleep.calls) == 4 and all(180 <= s <= 360 for s in sleep.calls)
    finally:
        await radar.stop()

    # Sin búsquedas: solo el feed y ninguna pausa
    h_vacio, sleep_vacio = FakeHarvester(), FakeSleep()
    radar_vacio = make_radar(tmp_path, harvester=h_vacio, sleep=sleep_vacio, queries=())
    try:
        await radar_vacio.run_cycle()
        assert h_vacio.calls == ["feed"] and sleep_vacio.calls == []
    finally:
        await radar_vacio.stop()


@pytest.mark.asyncio
async def test_invariantes_guardado_y_url(tmp_path, monkeypatch):
    # invariante: para cada consulta build_search_url mantiene keywords, sortBy y datePosted
    for q in DEFAULT_QUERIES:
        parsed = parse_qs(urlsplit(build_search_url(q)).query)
        assert parsed.get("keywords") == [q]
        assert parsed.get("sortBy") == ['"date_posted"']
        assert parsed.get("datePosted") == ['"past-week"']

    # Posts de prueba
    p_feed = dict(
        raw(301, emails=(), texto="Aviso en feed con link https://example.com/job pero sin email"),
        links=["https://example.com/job"],
    )
    p_busq_sin_email = dict(
        raw(302, emails=(), texto="Aviso busqueda con link https://example.com/job sin email ni imagen"),
        links=["https://example.com/job"],
    )
    p_busq_con_email = dict(
        raw(303, emails=("rrhh@empresa.test",), texto="Aviso busqueda con email rrhh@empresa.test"),
    )
    p_busq_con_imagen = dict(
        raw(304, emails=(), texto=""),
        imagenes=[IMAGEN],
    )

    h = FakeHarvester({
        "feed": HarvestResult(posts=[p_feed]),
        "q1": HarvestResult(posts=[p_busq_sin_email, p_busq_con_email, p_busq_con_imagen]),
    })

    # q1 en inglés: se guarda igual que en castellano (todo post con texto o imagen)
    radar = make_radar(tmp_path, harvester=h, queries=("q1",), queries_en=("q1",))
    # Desactivar _wake_processing en ese doble para observar solo captura
    monkeypatch.setattr(radar, "_wake_processing", lambda: None)

    try:
        await radar.run_cycle()

        guardados = {p["urn"]: p for p in list_posts()}

        # invariante: feed con link sin email se guarda
        assert p_feed["urn"] in guardados

        # invariante (12/11): sin «solo con email» en inglés, la búsqueda con texto sin email también se guarda
        assert p_busq_sin_email["urn"] in guardados

        # invariante: búsqueda con email sí se guarda
        assert p_busq_con_email["urn"] in guardados

        # invariante: imagen feedshare válida sin email sigue entrando
        assert p_busq_con_imagen["urn"] in guardados

        total_guardados_antes = len(guardados)

        # invariante: releer mismo URN no duplica
        await radar.run_cycle()
        guardados_despues = list_posts()
        assert len(guardados_despues) == total_guardados_antes
    finally:
        await radar.stop()
