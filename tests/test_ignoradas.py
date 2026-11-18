"""Filtro temporal «Ignoradas» (creador, 09/11): publicado hace 1 mes o más se guarda aparte."""
import pytest

from core import colas, radar_store
from tests.test_imagenes_motor import motor


@pytest.mark.parametrize("rel,antigua", [
    ("1 mes", True), ("2 meses", True), ("1 a", True), ("1 año", True), ("30 d", True), ("5 sem", True),
    ("1 mo", True), ("4 sem", False), ("29 d", False), ("3 h", False), ("2 días", False), ("", False),
    (None, False), ("Editado", False),
])
def test_es_antigua(rel, antigua):
    assert colas.es_antigua(rel) is antigua


@pytest.mark.asyncio
@pytest.mark.parametrize("fuente", ["feed", "busqueda"])
async def test_lo_viejo_se_guarda_ignorado_y_no_se_procesa(monkeypatch, fuente):
    loop = motor(monkeypatch)
    texto = "Buscamos Analista Funcional, enviar CV a rrhh@example.com"
    posts = [
        {"urn": "urn:li:activity:viejo", "texto": texto, "emails": ["rrhh@example.com"], "fecha_rel": "1 mes"},
        {"urn": "urn:li:activity:nuevo", "texto": "Otra búsqueda: Soporte N1, CV a it@example.com",
         "emails": ["it@example.com"], "fecha_rel": "3 d"},
    ]
    await loop._guardar_posts(posts, "qa", fuente=fuente)
    estados = {p["urn"]: p["estado"] for p in radar_store.list_posts()}
    assert estados == {"urn:li:activity:viejo": "IGNORADA", "urn:li:activity:nuevo": "NUEVO"}
    assert [p["urn"] for p in radar_store.list_posts(estado="NUEVO")] == ["urn:li:activity:nuevo"]
    assert radar_store.candidatas_cola() == []
    # No tapa una republicación reciente del mismo texto
    assert radar_store.existe_texto(texto) is False


def test_manual_nunca_se_ignora():
    pid = radar_store.insert_post_if_new({"urn": "manual:x", "texto": "Oferta", "fecha_rel": "2 meses"},
                                         query="manual", fuente=radar_store.FUENTE_MANUAL)
    assert radar_store.get_post(pid)["estado"] == "NUEVO"


def test_filtro_ignoradas_aislado_en_la_ui():
    js = open("static/radar.js", encoding="utf-8").read()
    html = open("static/index.html", encoding="utf-8").read()
    assert "'IGNORADA'];" in js and 'data-estado="IGNORADA"' in html
    assert "if (filtro === 'IGNORADA' || ignorada) return filtro === 'IGNORADA' && ignorada;" in js
    assert "if (e === 'IGNORADA') {" in js  # sin acciones
