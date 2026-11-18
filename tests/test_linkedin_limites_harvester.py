"""Tests de límites del harvester de LinkedIn (C2 de _ref/feature/plan.md):

- Orden alternado en URL y harvester ("date_posted" vs "relevance")
- Validación de orden inválido (ValueError)
- Espera entre scrolls con rango (3.0, 7.0) y jitter ±25%
"""
import random
import pytest

from core import linkedin_harvester
from core.linkedin_harvester import LinkedInHarvester
from core.linkedin_parser import build_search_url
from tests.test_linkedin_harvester import FakePage


def _make_harvester(page, rng=None, sleep_fn=None):
    h = LinkedInHarvester(rng=rng, sleep_fn=sleep_fn or (lambda s: None))
    h._page = page
    h._opened = True
    return h


def test_url_date_posted_igual_a_hoy():
    url_base = build_search_url("x")
    url_orden = build_search_url("x", orden="date_posted")
    assert url_base == url_orden
    assert 'sortBy=%22date_posted%22' in url_base
    assert 'datePosted=%22past-week%22' in url_base
    assert 'sortBy=%22date_posted%22' in url_orden
    assert 'datePosted=%22past-week%22' in url_orden


def test_url_relevance():
    url = build_search_url("x", orden="relevance")
    assert 'sortBy=%22relevance%22' in url
    assert 'datePosted=%22past-week%22' in url


def test_url_orden_invalido():
    with pytest.raises(ValueError):
        build_search_url("x", orden="otro")


@pytest.mark.asyncio
async def test_harvest_pasa_el_orden():
    page = FakePage()
    h = _make_harvester(page)
    try:
        await h.harvest("x", orden="relevance")
        assert page.goto_calls == [build_search_url("x", orden="relevance")]
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_espera_entre_scrolls_con_jitter():
    assert linkedin_harvester._SCROLL_WAIT == (3.0, 7.0)
    assert linkedin_harvester._JITTER == 0.25

    class FakeMaxRng(random.Random):
        def uniform(self, a, b):
            return max(a, b)

        def randint(self, a, b):
            return b

    esperas_max = []
    h_max = _make_harvester(FakePage(), rng=FakeMaxRng(), sleep_fn=esperas_max.append)
    try:
        await h_max.harvest("x", max_posts=30, max_scrolls=1)
        assert len(esperas_max) >= 2
        assert esperas_max[1] == pytest.approx(8.75)
    finally:
        await h_max.close()

    class FakeMinRng(random.Random):
        def uniform(self, a, b):
            return min(a, b)

        def randint(self, a, b):
            return a

    esperas_min = []
    h_min = _make_harvester(FakePage(), rng=FakeMinRng(), sleep_fn=esperas_min.append)
    try:
        await h_min.harvest("x", max_posts=30, max_scrolls=1)
        assert len(esperas_min) >= 2
        assert esperas_min[1] == pytest.approx(2.25)
    finally:
        await h_min.close()
