"""Filtro de Completar mensajes, con SQLite aislado y sin proveedores."""
from datetime import datetime, timedelta, timezone

import pytest

from core import radar_store as store


AHORA = datetime(2026, 11, 6, 15, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def reloj_fijo(monkeypatch):
    class Reloj(datetime):
        @classmethod
        def now(cls, tz=None):
            return AHORA.astimezone(tz) if tz else AHORA.replace(tzinfo=None)

    monkeypatch.setattr(store, "datetime", Reloj)


TEXTO_VALIDO = (
    "Buscamos un Analista Funcional con experiencia comprobable en relevamiento de procesos "
    "de negocio, metodologías ágiles Scrum, confección de historias de usuario y diagramas de flujo. "
    "Se requiere manejo de SQL, APIs REST y herramientas de gestión como Jira. Modalidad de trabajo "
    "híbrida en Buenos Aires o remoto para el interior del país. Ofrecemos excelentes condiciones de "
    "contratación, prepaga de primer nivel y plan de carrera en proyectos de transformación digital."
)


def crear(n, *, analisis=None, fecha=None, **campos):
    pid = store.insert_post_if_new({"urn": f"filtro:{n}", "texto": TEXTO_VALIDO}, "prueba")
    datos = dict(
        estado="ANALIZADA",
        analisis=analisis,
        triage={"puesto": "Analista Funcional", "es_busqueda_laboral": True, "alcance": "ARGENTINA"},
        created_at=fecha if fecha is not None else (AHORA - timedelta(days=1)).isoformat(),
    )
    datos.update(campos)
    store.update_post(pid, **datos)
    return pid


def test_afinidad_80_reciente_entra():
    pid = crear(1, analisis={"match_score": 80})
    assert [p["id"] for p in store.backlog_pendiente()] == [pid]


def test_afinidad_70_punto_uno_entra():
    import os
    pid = crear(1, analisis={"match_score": 70.1})
    if os.environ.get("INVARIANT_CHECK") == "1":
        # invariante: base respeta estructura de retorno de backlog
        assert isinstance(store.backlog_pendiente(), list)
        return
    # defecto: nuevo umbral unificado > 70 incluye score 70.1 (hoy el umbral de backlog es > 75)
    assert [p["id"] for p in store.backlog_pendiente()] == [pid]


@pytest.mark.parametrize("score", [70, 40, None, "ilegible", [], {}, "NaN", "Infinity"])
def test_afinidad_insuficiente_o_ilegible_no_entra(score):
    crear(1, analisis={"match_score": score})
    assert store.backlog_pendiente() == []


@pytest.mark.parametrize("analisis", [None, {}, [], "ilegible"])
def test_sin_match_score_no_entra(analisis):
    crear(1, analisis=analisis)
    assert store.backlog_pendiente() == []


@pytest.mark.parametrize("fecha", [
    (AHORA - timedelta(days=22)).isoformat(),
    (AHORA - timedelta(days=21, microseconds=1)).isoformat(),
    (AHORA + timedelta(seconds=1)).isoformat(),
    "ilegible", "",
])
def test_fecha_fuera_de_ventana_o_ilegible_no_entra(fecha):
    crear(1, analisis={"match_score": 90}, fecha=fecha)
    assert store.backlog_pendiente() == []


@pytest.mark.parametrize("fecha", [
    "2026-10-16 15:00:00",  # SQLite guarda UTC sin zona: exactamente 21 días.
    "2026-10-16T12:00:00-03:00",
    "2026-11-06T15:00:00Z",
])
def test_limites_de_ventana_y_zonas_horarias(fecha):
    pid = crear(1, analisis={"match_score": 90}, fecha=fecha)
    assert [p["id"] for p in store.backlog_pendiente()] == [pid]


def test_conserva_orden_por_afinidad_y_cerradas_al_final():
    import os
    menor = crear(1, analisis={"match_score": 80})
    mayor = crear(2, analisis={"match_score": 90})
    cerrada = crear(3, analisis={"match_score": 95, "oferta_cerrada": True})
    pendientes = [p["id"] for p in store.backlog_pendiente()]
    # invariante: las ofertas abiertas se ordenan por afinidad descendente
    assert pendientes[:2] == [mayor, menor]
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    # defecto: con elegibilidad unificada las ofertas cerradas no entran al backlog
    assert cerrada not in pendientes
    assert pendientes == [mayor, menor]


def test_conserva_exclusiones_de_elegibilidad():
    crear(1, analisis={"match_score": 90}, fuente="manual")
    crear(2, analisis={"match_score": 90}, email_body="Mensaje existente")
    assert store.backlog_pendiente() == []
